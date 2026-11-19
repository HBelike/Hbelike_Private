import test from 'node:test'
import assert from 'node:assert/strict'
import { createJobSearchSession, isJobSearchFollowup } from './job-search-session.js'

test('旧操作回执和晚到批次不能降低当前版本，显式历史查询仍可用', async () => {
  let loseResponse = true
  let currentRevision = 0
  const revisions = []
  const session = createJobSearchSession({ conversationId: 'history', request: async (url, options = {}) => {
    if (url.endsWith('/batches') && options.method === 'POST') return { batch: { id: 'batch', batch_no: 1, revision: 0 } }
    if (url.endsWith('/batches')) return { batches: [{ id: 'batch', batch_no: 1, revision: currentRevision }] }
    if (url.includes('/jobs?')) { revisions.push(new URL(url, 'https://example.test').searchParams.get('revision')); return { jobs: [], next_cursor: null } }
    if (loseResponse) { loseResponse = false; currentRevision = 1; throw new Error('响应丢失') }
    return { batch: { id: 'batch', revision: currentRevision }, revision: 1, job_refs: {} }
  } })
  await session.start('card', {}, false)
  await assert.rejects(session.savePage('batch', [{ id: 'job' }], 1, false))
  currentRevision = 2
  await session.restore()
  await session.retry('batch')
  assert.equal(session.state('batch').batch.revision, 2)
  currentRevision = 1
  await session.savePage('batch', [{ id: 'job' }], 1, false)
  assert.equal(session.state('batch').batch.revision, 2)
  await session.jobs('batch')
  await session.jobs('batch', 1)
  assert.deepEqual(revisions, ['2', '1'])
})

function setup() {
  let sequence = 0
  const stored = new Map()
  const calls = []
  let fail = false
  let hold
  const request = async (url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : {}
    calls.push({ url, body })
    if (url.endsWith('/batches') && options.method === 'POST') {
      const batch = { id: `batch-${++sequence}`, batch_no: sequence, revision: 0, conditions: body.conditions }
      stored.set(batch.id, { batch, jobs: [] })
      return { batch }
    }
    const batchId = url.match(/\/batches\/([^/]+)/)?.[1]
    const record = stored.get(batchId)
    if (hold) { const wait = hold; hold = null; await wait }
    if (fail) throw new Error('保存失败')
    if (url.endsWith('/pages')) record.jobs.push(...body.jobs)
    record.batch = { ...record.batch, revision: record.batch.revision + 1 }
    return { batch: record.batch, revision: record.batch.revision,
      job_refs: Object.fromEntries(record.jobs.map((job, index) => [job.jobId || job.id, `S1-J${index + 1}`])) }
  }
  const session = createJobSearchSession({ request, conversationId: 'conversation' })
  return { session, stored, calls, fail: (value) => { fail = value }, hold: (value) => { hold = value } }
}

test('旧批页晚到仍只写原批，全部15条和第12岗位可引用', async () => {
  const state = setup()
  const a = await state.session.start('card', { keyword: '网易' }, false)
  const b = await state.session.start('card', { keyword: '腾讯' }, false)
  const jobs = Array.from({ length: 15 }, (_, index) => ({ id: `job-${index}`, title: `岗位${index}` }))
  await state.session.savePage(a.id, jobs, 1, false)
  assert.equal(state.stored.get(a.id).jobs.length, 15)
  assert.equal(state.stored.get(b.id).jobs.length, 0)
  await state.session.focus(a.id, jobs[11])
  assert.equal(state.session.view().focused_job_ref, 'S1-J12')
  assert.equal(state.session.view().active_batch_id, a.id)
})

test('已结束的失败仍为dirty，重试沿用同一个request_id', async () => {
  const state = setup()
  const a = await state.session.start('card', { keyword: '网易' }, false)
  state.fail(true)
  await assert.rejects(state.session.savePage(a.id, [{ id: 'job' }], 1, false))
  await assert.rejects(state.session.ensureSaved())
  assert.deepEqual(state.session.view().uncommitted_batch_ids, [a.id])
  const failedId = state.calls.at(-1).body.request_id
  state.fail(false)
  await state.session.retry(a.id)
  assert.equal(state.calls.at(-1).body.request_id, failedId)
  await state.session.ensureSaved()
  assert.deepEqual(state.session.view().uncommitted_batch_ids, [])
})

test('追问等待尚在保存的结果，失败不会被Promise队列清理掩盖', async () => {
  const state = setup()
  const a = await state.session.start('card', { keyword: '网易' }, false)
  let release
  state.hold(new Promise((resolve) => { release = resolve }))
  const write = state.session.savePage(a.id, [{ id: 'job' }], 1, false)
  let captured = false
  const capture = state.session.ensureSaved().then(() => { captured = true })
  await Promise.resolve()
  assert.equal(captured, false)
  release()
  await write
  await capture
  assert.equal(captured, true)
})

test('普通聊天不因历史错误被识别为岗位追问', () => {
  assert.equal(isJobSearchFollowup('你好'), false)
  assert.equal(isJobSearchFollowup('这个岗位适合我吗'), true)
})


test('取消后晚回包不能再创建新写入，取消状态有明确服务端请求', async () => {
  const state = setup()
  const a = await state.session.start('card', { keyword: '网易' }, false)
  await state.session.cancel(a.id)
  const count = state.calls.length
  await state.session.savePage(a.id, [{ id: 'late' }], 2, false)
  assert.equal(state.calls.length, count)
  assert.equal(state.calls.at(-1).body.status, 'cancelled')
})

test('保存失败已结束时仍能取消，不要求用户先保存才能收起', async () => {
  const state = setup()
  const batch = await state.session.start('card', { keyword: '网易' }, true)
  state.fail(true)
  await assert.rejects(state.session.savePage(batch.id, [{ id: 'job' }], 1, false))
  state.fail(false)
  await state.session.cancel(batch.id)
  assert.equal(state.calls.at(-1).body.status, 'cancelled')
  assert.deepEqual(state.session.view().uncommitted_batch_ids, [])
})

test('取消不等待卡住的旧保存，旧回包不能恢复批次状态', async () => {
  const state = setup()
  const batch = await state.session.start('card', { keyword: '网易' }, true)
  let release
  state.hold(new Promise((resolve) => { release = resolve }))
  const pending = state.session.savePage(batch.id, [{ id: 'late-job' }], 1, false)
  await new Promise((resolve) => setImmediate(resolve))
  let cancelled = false
  const cancellation = state.session.cancel(batch.id).then(() => { cancelled = true })
  await new Promise((resolve) => setImmediate(resolve))
  try { assert.equal(cancelled, true) }
  finally { release() }
  await cancellation
  await pending
  assert.equal(state.session.state(batch.id).cancelled, true)
  assert.deepEqual(state.session.view().uncommitted_batch_ids, [])
})

test('另一批的已结束失败不阻断当前批追问，比较两批才等待两边', async () => {
  const state = setup()
  const a = await state.session.start('card', { keyword: '网易' }, false)
  state.fail(true)
  await assert.rejects(state.session.savePage(a.id, [{ id: 'job' }], 1, false))
  state.fail(false)
  const b = await state.session.start('card', { keyword: '腾讯' }, false)
  await state.session.savePage(b.id, [{ id: 'other' }], 1, false)
  await state.session.ensureSaved()
  await assert.rejects(state.session.ensureSaved([a.id, b.id]))
})


test('显式旧公司或S1-J12引用检查旧批dirty，而非只检查活动批', async () => {
  const state = setup()
  const netease = await state.session.start('card', { keyword: '网易' }, false)
  state.fail(true)
  await assert.rejects(state.session.savePage(netease.id, [{ id: 'job' }], 1, false))
  state.fail(false)
  const tencent = await state.session.start('card', { keyword: '腾讯' }, false)
  await state.session.savePage(tencent.id, [{ id: 'other' }], 1, false)
  for (const text of ['网易这批哪些适合我', 'S1-J12适合吗', '第一批哪些适合', '第1批哪些适合']) {
    assert.deepEqual(state.session.relevantBatchIds(text), [netease.id])
    await assert.rejects(state.session.ensureSaved(state.session.relevantBatchIds(text)))
  }
  assert.deepEqual(state.session.relevantBatchIds('这个岗位适合我吗'), [tencent.id])
  await state.session.ensureSaved(state.session.relevantBatchIds('这个岗位适合我吗'))
})

test('默认比较最近两批，不被第三批无关dirty拦住，明确全部则包含它', async () => {
  const state = setup()
  const old = await state.session.start('card', { keyword: '阿里' }, false)
  state.fail(true)
  await assert.rejects(state.session.savePage(old.id, [{ id: 'old' }], 1, false))
  state.fail(false)
  const netease = await state.session.start('card', { keyword: '网易' }, false)
  await state.session.savePage(netease.id, [{ id: 'netease' }], 1, false)
  const tencent = await state.session.start('card', { keyword: '腾讯' }, false)
  await state.session.savePage(tencent.id, [{ id: 'tencent' }], 1, false)
  for (const text of ['比较这两批', '比较网易和腾讯']) {
    assert.deepEqual(state.session.relevantBatchIds(text), [tencent.id, netease.id])
    await state.session.ensureSaved(state.session.relevantBatchIds(text))
  }
  await assert.rejects(state.session.ensureSaved(state.session.relevantBatchIds('比较所有批次')))
  assert.deepEqual(state.session.relevantBatchIds('你好'), [])
})


test('推荐失败状态可见，重试HTTP失败沿用request_id且不误标岗位未保存', async () => {
  const state = setup()
  const batch = await state.session.start('card', { keyword: '网易' }, true)
  await state.session.savePage(batch.id, [{ id: 'job' }], 1, false)
  let observed = 0
  state.session.subscribe(() => { observed++ })
  state.session.setAnalysisState(batch.id, 'failed', '模型暂不可用')
  assert.equal(state.session.state(batch.id).batch.analysis_state, 'failed')
  assert.equal(state.session.state(batch.id).batch.analysis_error, '模型暂不可用')
  state.fail(true)
  await assert.rejects(state.session.retryAnalysis(batch.id))
  const requestId = state.calls.at(-1).body.request_id
  await state.session.ensureSaved([batch.id])
  state.fail(false)
  await state.session.retryAnalysis(batch.id)
  assert.equal(state.calls.at(-1).body.request_id, requestId)
  assert.ok(state.calls.at(-1).url.endsWith('/analysis/retry'))
  state.session.setAnalysisState(batch.id, 'succeeded')
  assert.equal(state.session.state(batch.id).batch.analysis_error, '')
  assert.ok(observed >= 3)
})
