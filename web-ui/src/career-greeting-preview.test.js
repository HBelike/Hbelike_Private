import test from 'node:test'
import assert from 'node:assert/strict'
import {
  advanceGreetingSend,
  GREETING_MESSAGE_TARGET_LENGTH,
  greetingMessageLength,
  greetingMessageError,
  updateGreetingMessage,
  applyGreetingFailure,
  applyGreetingResult,
  createGreetingItems,
  findGreetingFailureAction,
  needsGreetingRiskWarning,
  normalizeGreetingLimit,
  markGreetingGenerating,
  queueGreetingItems,
  recordGreetingAttempt,
  retryGreetingItems,
  stopGreetingItems,
  updateGreetingItemStatus,
  toggleGreetingJob
} from './career-greeting-preview.js'

const job = (id) => ({
  id,
  title: `全栈工程师 ${id}`,
  company: `示例公司 ${id}`,
  companyShort: `示例 ${id}`,
  description: '负责微服务与分布式系统交付。',
  skills: ['微服务', '分布式']
})

test('批次上限固定在一到十之间', () => {
  assert.equal(normalizeGreetingLimit(0), 1)
  assert.equal(normalizeGreetingLimit(6), 6)
  assert.equal(normalizeGreetingLimit(99), 10)
})

test('职位多选去重并服从用户上限', () => {
  let state = toggleGreetingJob([], job('1'), 1)
  assert.deepEqual(state.jobs.map((item) => item.id), ['1'])
  state = toggleGreetingJob(state.jobs, job('2'), 1)
  assert.match(state.error, /最多选择 1 个岗位/)
  state = toggleGreetingJob(state.jobs, job('1'), 1)
  assert.deepEqual(state.jobs, [])
})

test('选满十条后仍可移除已有岗位', () => {
  const jobs = Array.from({ length: 10 }, (_, index) => job(String(index + 1)))
  const removed = toggleGreetingJob(jobs, jobs[4], 10)
  assert.equal(removed.jobs.length, 9)
  assert.equal(removed.error, '')
})

test('创建每岗一条待生成记录并应用真实模型结果', () => {
  const items = createGreetingItems([job('1'), job('2')])
  assert.equal(items.length, 2)
  assert.equal(items[0].revision, 0)
  assert.equal(items[0].status, 'generating')
  assert.equal(items[0].message, '')
  const generated = applyGreetingResult(items, items[0].id, {
    message: '根据简历和 JD 生成的招呼语',
    resume_evidence: [{ id: 'CV-001', summary: '真实简历证据' }],
    jd_highlights: [{ id: 'JD-001', summary: '岗位关注点' }],
    model: { model_id: 'deepseek-v4-pro' }
  })
  assert.equal(generated[0].revision, 1)
  assert.equal(generated[0].status, 'ready')
  assert.equal(generated[0].message, '根据简历和 JD 生成的招呼语')
  assert.deepEqual(generated[0].evidence, ['真实简历证据'])
  assert.equal(generated[0].generatedModel, 'deepseek-v4-pro')
})

test('初次生成失败可重试，重新生成失败保留上一版', () => {
  const [pending] = createGreetingItems([job('1')])
  const failed = applyGreetingFailure([pending], pending.id, new Error('模型不可用'))
  assert.equal(failed[0].status, 'generation_failed')
  assert.equal(failed[0].generationError, '模型不可用')

  const [ready] = applyGreetingResult([pending], pending.id, { message: '上一版招呼语' })
  const generating = markGreetingGenerating([ready], ready.id)
  assert.equal(generating[0].status, 'generating')
  const preserved = applyGreetingFailure(generating, ready.id, new Error('重新生成失败'))
  assert.equal(preserved[0].status, 'ready')
  assert.equal(preserved[0].message, '上一版招呼语')
})

test('超过五条需要风险确认', () => {
  assert.equal(needsGreetingRiskWarning(5), false)
  assert.equal(needsGreetingRiskWarning(6), true)
})

test('本地发送每次只推进一个待发送岗位', () => {
  const items = createGreetingItems([job('1'), job('2')]).map((item) => ({ ...item, message: '有效招呼语', status: 'queued' }))
  const first = advanceGreetingSend(items)
  assert.equal(first[0].status, 'sent')
  assert.equal(first[1].status, 'queued')
  const second = advanceGreetingSend(first)
  assert.equal(second[1].status, 'sent')
})

test('取消项不会进入本地发送队列', () => {
  const items = createGreetingItems([job('1'), job('2')])
  const queued = items.map((item, index) => ({
    ...item,
    message: '有效招呼语',
    included: index === 0,
    status: index === 0 ? 'queued' : 'excluded'
  }))
  const sent = advanceGreetingSend(queued)
  assert.equal(sent[0].status, 'sent')
  assert.equal(sent[1].status, 'excluded')
})

test('没有生成成功的岗位不会进入发送队列', () => {
  const [pending] = createGreetingItems([job('1')])
  const [failed] = applyGreetingFailure([pending], pending.id, new Error('生成失败'))
  const queued = queueGreetingItems([failed])
  assert.equal(queued[0].status, 'excluded')
})

test('串行发送状态按预检、发送、完成推进', () => {
  const items = createGreetingItems([job('1'), job('2')]).map((item) => ({ ...item, message: '有效招呼语', status: 'queued' }))
  const preflighting = updateGreetingItemStatus(items, items[0].id, 'preflighting')
  assert.deepEqual(preflighting.map((item) => item.status), ['preflighting', 'queued'])
  const sending = updateGreetingItemStatus(preflighting, items[0].id, 'sending')
  assert.deepEqual(sending.map((item) => item.status), ['sending', 'queued'])
  const sent = updateGreetingItemStatus(sending, items[0].id, 'sent')
  assert.deepEqual(sent.map((item) => item.status), ['sent', 'queued'])
})

test('每个岗位记录发送尝试次数和最近处理时间', () => {
  const [item] = createGreetingItems([job('1')])
  const attempted = recordGreetingAttempt([item], item.id, '2026-08-24T10:00:00.000Z')
  assert.equal(attempted[0].attemptCount, 1)
  assert.equal(attempted[0].lastAttemptAt, '2026-08-24T10:00:00.000Z')
})

test('停止批次会停止未发送和预检中的岗位，但不篡改已提交项', () => {
  const items = createGreetingItems([job('1'), job('2'), job('3')]).map((item, index) => ({
    ...item,
    status: ['sending', 'preflighting', 'queued'][index]
  }))
  assert.deepEqual(stopGreetingItems(items).map((item) => item.status), ['sending', 'stopped', 'stopped'])
})

test('安全失败可重试当前项并恢复后续串行队列', () => {
  const items = createGreetingItems([job('1'), job('2'), job('3')]).map((item, index) => ({
    ...item,
    message: '有效招呼语',
    status: ['sent', 'failed', 'stopped'][index],
    retryable: index === 1,
    retryMode: index === 1 ? 'message' : ''
  }))
  const retried = retryGreetingItems(items, items[1].id)
  assert.deepEqual(retried.map((item) => item.status), ['sent', 'queued', 'queued'])
  assert.equal(retried[1].retryable, false)
  assert.equal(retried[1].nextAttemptMode, 'message')
})

test('结果未知的失败项不会进入重试队列', () => {
  const [item] = createGreetingItems([job('1')])
  const retried = retryGreetingItems([{ ...item, status: 'failed', retryable: false }], item.id)
  assert.equal(retried[0].status, 'failed')
})

test('失败操作不受当前选中的已停止岗位影响', () => {
  const items = createGreetingItems([job('1'), job('2')]).map((item, index) => ({
    ...item,
    status: index === 0 ? 'failed' : 'stopped',
    retryable: index === 0,
    errorCode: index === 0 ? 'verification_required' : ''
  }))
  assert.deepEqual(findGreetingFailureAction(items), {
    itemId: items[0].id,
    type: 'retry'
  })
})

test('浏览器标签页忙且文案未提交时显示重新发送入口', () => {
  const [item] = createGreetingItems([job('1')])
  assert.deepEqual(findGreetingFailureAction([{
    ...item,
    status: 'failed',
    retryable: true,
    retryMode: 'message',
    errorCode: 'browser_tab_busy',
    submissionState: 'not_submitted',
    defaultGreetingSent: true
  }]), {
    itemId: item.id,
    type: 'retry'
  })
})

test('旧版助手产生的安全验证失败引导更新而不盲目重发', () => {
  const [item] = createGreetingItems([job('1')])
  assert.deepEqual(findGreetingFailureAction([{
    ...item,
    status: 'failed',
    retryable: false,
    errorCode: 'verification_required',
    submissionState: ''
  }]), {
    itemId: item.id,
    type: 'update_extension'
  })
  assert.equal(findGreetingFailureAction([{
    ...item,
    status: 'failed',
    retryable: false,
    errorCode: 'send_unknown',
    submissionState: 'unknown'
  }]), null)
})


test('招呼语按Unicode codepoint计数，200字仅为建议目标', () => {
  assert.equal(GREETING_MESSAGE_TARGET_LENGTH, 200)
  assert.equal(greetingMessageError('中'.repeat(200)), '')
  assert.equal(greetingMessageError('中'.repeat(201)), '')
  assert.equal(greetingMessageError('中'.repeat(3000)), '')
  assert.equal(greetingMessageLength('A 1，😀'), 5)
  assert.equal(greetingMessageLength('😀'.repeat(200)), 200)
  assert.equal(greetingMessageError('😀'.repeat(200)), '')
  assert.equal(greetingMessageError('😀'.repeat(201)), '')
  assert.match(greetingMessageError('   '), /不能为空/)
  assert.equal(greetingMessageError('  ' + '中'.repeat(200) + '  '), '')
  assert.equal(greetingMessageError('中'.repeat(199) + ' 1'), '')
})

test('模型生成超过建议字数的文案保留全文并可进入审核发送队列', () => {
  const [item] = createGreetingItems([job('long')])
  const generated = applyGreetingResult([item], item.id, { message: '中'.repeat(201) })
  assert.equal(generated[0].message.length, 201)
  assert.equal(generated[0].messageError, '')
  assert.equal(queueGreetingItems(generated)[0].status, 'queued')
})

test('手工编辑或粘贴超过建议字数的文案可发送，空白仍不可发送', () => {
  const [item] = createGreetingItems([job('edit')])
  const ready = applyGreetingResult([item], item.id, { message: '原始招呼语' })
  const edited = updateGreetingMessage(ready, item.id, '😀'.repeat(201))
  assert.equal(greetingMessageLength(edited[0].message), 201)
  assert.equal(edited[0].messageError, '')
  assert.equal(queueGreetingItems(edited)[0].status, 'queued')
  const empty = updateGreetingMessage(edited, item.id, '   ')
  assert.match(empty[0].messageError, /不能为空/)
  assert.equal(queueGreetingItems(empty)[0].status, 'excluded')
})

test('重发失败项和恢复后续项均不按建议字数拦截', () => {
  const items = [
    { id: 'failed', message: '中'.repeat(201), status: 'failed', included: true, retryable: true },
    { id: 'later', message: '正常文案', status: 'stopped', included: true }
  ]
  const retried = retryGreetingItems(items, 'failed')
  assert.deepEqual(retried.map((item) => item.status), ['queued', 'queued'])
  assert.equal(retried[0].message, items[0].message)
  const allowed = retryGreetingItems([{ ...items[0], message: '正常文案' }, { ...items[1], message: '中'.repeat(201) }], 'failed')
  assert.deepEqual(allowed.map((item) => item.status), ['queued', 'queued'])
})

test('模拟推进允许超过建议字数的文案，但仍拒绝空文案', () => {
  const [sent] = advanceGreetingSend([{ id: 'long', message: '中'.repeat(201), status: 'queued' }])
  assert.equal(sent.status, 'sent')
  const [empty] = advanceGreetingSend([{ id: 'empty', message: '  ', status: 'queued' }])
  assert.equal(empty.status, 'failed')
  assert.equal(empty.errorCode, 'message_invalid')
})
