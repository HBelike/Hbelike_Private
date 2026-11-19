// 搜索批次独立保存，失败操作保留到用户重试成功。
export function createJobSearchSession({ request, conversationId, onAnalysis = () => {}, onChange = () => {} }) {
  const batches = new Map()
  const listeners = new Set()
  let activeBatchId = ''
  let focusedJobRef = ''
  let interacted = false
  const base = `/api/career/conversations/${conversationId}/job-search/batches`
  const id = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`
  const key = (job) => String(job.jobId || job.id || job.securityId || '')
  const notify = () => { const snapshot = view(); onChange(snapshot); for (const listener of listeners) listener(snapshot) }
  function mergeResult(state, result) {
    const current = state.batch.revision ?? 0
    // 回执版本属于原操作；当前批次版本只能前进，晚到响应也不能覆盖较新状态。
    if (result.batch && (result.batch.revision ?? 0) >= current) state.batch = { ...state.batch, ...result.batch }
    state.batch.revision = Math.max(current, state.batch.revision ?? 0, result.revision ?? 0)
  }
  function adopt(batch) {
    if (!batch?.id) return null
    const state = batches.get(batch.id) || { batch, refs: {}, tail: Promise.resolve(), failed: [], pending: 0 }
    if ((batch.revision || 0) >= (state.batch.revision || 0)) state.batch = { ...state.batch, ...batch }
    batches.set(batch.id, state)
    notify()
    return state
  }
  function view() {
    return { active_batch_id: activeBatchId || null, focused_job_ref: focusedJobRef || null,
      revisions: Object.fromEntries([...batches].map(([batchId, state]) => [batchId, state.batch.revision])),
      uncommitted_batch_ids: [...batches].filter(([, state]) => !state.cancelled && (state.failed.some((entry) => entry.action !== 'analysis/retry') || state.pending)).map(([batchId]) => batchId) }
  }
  function queue(batchId, action, body, method = 'POST') {
    const state = batches.get(batchId)
    if (state?.cancelled && action !== 'complete') return Promise.resolve(null)
    if (!state) return Promise.reject(new Error('搜索批次尚未保存，请重新搜索。'))
    const operation = { action, body: { request_id: id(), ...body }, method }
    return perform(state, operation)
  }
  function perform(state, operation) {
    state.pending++
    const pending = state.tail.catch(() => {}).then(async () => {
      if (state.cancelled && operation.body.status !== 'cancelled') return null
      // 失败之后的操作不能越过未保存的数据。
      if (state.failed.some((entry) => entry.action !== 'analysis/retry') && !state.failed.includes(operation)) {
        state.failed.push(operation)
        throw new Error('岗位结果尚未保存成功，请重试保存。')
      }
      try {
        const result = await request(`${base}/${state.batch.id}/${operation.action}`, {
          method: operation.method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(operation.body)
        })
        if (state.cancelled && operation.body.status !== 'cancelled') return null
        Object.assign(state.refs, result.job_refs || {})
        mergeResult(state, result)
        state.failed = state.failed.filter((entry) => entry !== operation)
        if (operation.body.status === 'cancelled') state.failed = []
        if (result.analysis_turn) {
          state.batch.analysis_turn_id = result.analysis_turn.id || result.analysis_turn.turn_id || result.analysis_turn.turn?.id
          onAnalysis(result.analysis_turn, state.batch)
        }
        return result
      } catch (error) {
        if ((!state.cancelled || operation.body.status === 'cancelled') && !state.failed.includes(operation)) state.failed.push(operation)
        throw error
      } finally { notify() }
    })
    state.tail = pending
    pending.finally(() => { state.pending--; notify() }).catch(() => {})
    return pending
  }
  async function start(sourceMessageId, conditions, analysisRequested) {
    const result = await request(base, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ request_id: id(), source_message_id: sourceMessageId, conditions, analysis_requested: analysisRequested }) })
    const batch = result.batch
    adopt(batch)
    interacted = true
    activeBatchId = batch.id
    focusedJobRef = ''
    notify()
    return batch
  }
  async function savePage(batchId, jobs, page, hasMore) {
    return queue(batchId, 'pages', { jobs: jobs.map((job) => ({ ...job })), page, has_more: Boolean(hasMore) })
  }
  async function saveDetail(batchId, job) {
    const state = batches.get(batchId)
    const jobRef = job.job_ref || state.refs[key(job)]
    if (!jobRef) throw new Error('岗位基础信息尚未保存，无法保存详情。')
    return queue(batchId, 'details', { jobs: [{ ...job, job_ref: jobRef }] })
  }
  async function focus(batchId, job) {
    interacted = true
    activeBatchId = batchId
    const state = batches.get(batchId)
    focusedJobRef = job.job_ref || state.refs[key(job)] || ''
    notify()
    if (focusedJobRef) await queue(batchId, 'focus', { job_ref: focusedJobRef }, 'PATCH')
  }
  function setAnalysisState(batchId, status, error = '') {
    const state = batches.get(batchId)
    if (!state) return
    state.batch.analysis_state = status
    state.batch.analysis_error = error
    notify()
  }
  async function retryAnalysis(batchId) {
    const state = batches.get(batchId)
    if (!state) throw new Error('找不到对应搜索批次。')
    setAnalysisState(batchId, 'queued')
    try {
      const failed = state.failed.find((operation) => operation.action === 'analysis/retry')
      return failed ? await perform(state, failed) : await queue(batchId, 'analysis/retry', {})
    } catch (error) {
      setAnalysisState(batchId, 'failed', error.message)
      throw error
    }
  }
  function relevantBatchIds(text = '') {
    if (!isJobSearchFollowup(text)) return []
    const list = [...batches.values()].map((state) => state.batch)
    const ordered = list.map((batch, index) => ({ batch, number: Number(batch.batch_no) || index + 1 }))
      .sort((a, b) => b.number - a.number)
    if (/所有|全部|每一批/.test(text)) return ordered.map(({ batch }) => batch.id)
    const numbers = new Set([...text.matchAll(/S(\d+)-J\d+/gi)].map((match) => Number(match[1])))
    const chineseNumber = (value) => {
      if (/^\d+$/.test(value)) return Number(value)
      const digits = { 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 }
      if (!value.includes('十')) return digits[value] || 0
      const [tens, units] = value.split('十')
      return (digits[tens] || 1) * 10 + (digits[units] || 0)
    }
    for (const match of text.matchAll(/第\s*([一二两三四五六七八九十\d]+)\s*批/g)) numbers.add(chineseNumber(match[1]))
    const named = ordered.filter(({ batch }) => {
      const keyword = String(batch.conditions?.keyword || '').trim().toLocaleLowerCase()
      const normalized = text.toLocaleLowerCase()
      return keyword.length >= 2 && (normalized.includes(keyword) || keyword.split(/\s+/).some((part) => part.length >= 2 && normalized.includes(part)))
    })
    const explicit = ordered.filter(({ batch, number }) => numbers.has(number) || named.some((item) => item.batch.id === batch.id))
    if (explicit.length) return explicit.map(({ batch }) => batch.id)
    if (numbers.size) return []
    if (/比较|对比|两批|这两批/.test(text)) return ordered.slice(0, 2).map(({ batch }) => batch.id)
    return activeBatchId ? [activeBatchId] : []
  }
  async function ensureSaved(batchIds = activeBatchId ? [activeBatchId] : []) {
    const relevant = batchIds.map((batchId) => batches.get(batchId)).filter((state) => state && !state.cancelled)
    await Promise.all(relevant.map((state) => state.tail.catch((error) => { if (state.failed.some((operation) => operation.action !== 'analysis/retry')) throw error })))
    if (relevant.some((state) => state.failed.some((operation) => operation.action !== 'analysis/retry'))) throw new Error('岗位结果尚未保存成功，请先重试保存。')
    return view()
  }
  async function retry(batchId) {
    const state = batches.get(batchId)
    await state.tail.catch(() => {})
    const operations = [...state.failed]
    for (const operation of operations) await perform(state, operation)
    return view()
  }
  async function restore(sourceMessageId) {
    const result = await request(base)
    const list = (result.batches || result || []).filter((batch) => !sourceMessageId || batch.source_message_id === sourceMessageId)
    list.forEach(adopt)
    if (!interacted) {
      const latest = [...batches.values()].map((state) => state.batch).sort((a, b) => String(b.last_focused_at || '').localeCompare(String(a.last_focused_at || '')) || (b.batch_no || 0) - (a.batch_no || 0))[0]
      if (latest) { activeBatchId = latest.id; focusedJobRef = latest.focused_job_ref || ''; notify() }
    }
    // 刷新后恢复已有分析订阅，不重新执行搜索。
    for (const batch of list) if (batch.analysis_turn_id) onAnalysis({ id: batch.analysis_turn_id }, batch)
    return list
  }
  async function jobs(batchId, revision) {
    let cursor = 0
    const all = []
    do {
      const result = await request(`${base}/${batchId}/jobs?revision=${revision ?? batches.get(batchId)?.batch.revision ?? 0}&cursor=${cursor}`)
      all.push(...(result.jobs || []))
      cursor = result.next_cursor
    } while (cursor != null)
    return all
  }
  async function cancel(batchId) {
    const state = batches.get(batchId)
    if (!state) return null
    state.cancelled = true
    // 取消是独立动作，不能被正在等待或已失败的保存队列挡住。
    const operation = { action: 'complete', body: { request_id: id(), status: 'cancelled' }, method: 'POST' }
    state.failed = []
    notify()
    try {
      const result = await request(`${base}/${batchId}/complete`, { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(operation.body) })
      mergeResult(state, result)
      state.batch = { ...state.batch, analysis_state: 'cancelled', analysis_error: '' }
      return result
    } catch (error) {
      state.failed.push(operation)
      throw error
    } finally { notify() }
  }
  return { start, savePage, saveDetail, focus, ensureSaved, relevantBatchIds, retry, retryAnalysis, setAnalysisState, restore, jobs, view,
    subscribe: (listener) => { listeners.add(listener); return () => listeners.delete(listener) },
    complete: (batchId, options = {}) => queue(batchId, 'complete', options),
    cancel,
    state: (batchId) => batches.get(batchId),
    activate: (batchId) => { interacted = true; activeBatchId = batchId; focusedJobRef = batches.get(batchId)?.batch.focused_job_ref || ''; notify() } }
}

export function isJobSearchFollowup(text) {
  if (/帮我找|重新搜|再搜|再找|搜索|检索/.test(text)) return false
  return /岗位|职位|这批|那批|这个|比较|对比|推荐|适合|匹配|网易|腾讯|S\d+-J\d+|第[一二两三四五六七八九十\d]+[个批]/.test(text)
}
