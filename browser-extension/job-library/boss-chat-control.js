// 在发起页面执行；同源 Cookie 留在浏览器，不传给扩展或招聘平台。
export async function checkBossChatApplication(request = {}) {
  const failure = (code, message) => ({ ok: false, error: { code, message } })
  const execution = request.execution || {}
  if (!execution.account_id || !execution.executor_id || !Number.isInteger(execution.revision) || execution.revision < 1) {
    return failure('execution_required', '请先在招聘沟通页取得当前账号的执行资格。')
  }
  const finishing = ['cancel', 'pause'].includes(request.kind) || (request.kind === 'transition' && ['submitted', 'unknown', 'failed', 'cancelled'].includes(request.status))
  if (!finishing && (document.hidden || document.visibilityState === 'hidden')) return failure('page_hidden', '招聘沟通页面已在后台，尚未提交的操作已停止。')
  if (!finishing && !/^\/career\/messages\/?$/.test(location.pathname)) return failure('execution_lost', '招聘沟通页面已离开，尚未提交的操作已停止。')
  const root = '/api/career/external-chat'
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 8000)
  async function api(path, body) {
    const response = await fetch(`${root}${path}`, { credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
      ...(body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) })
    const data = await response.json().catch(() => ({}))
    if (!response.ok) throw Object.assign(new Error(data.detail?.message || '无法确认当前执行资格，操作已停止。'), { code: data.detail?.code || 'execution_unavailable' })
    return data
  }
  try {
    if (request.kind === 'cancel') return { ok: true, data: await api(`/operations/${encodeURIComponent(request.clientOperationId)}/cancel`, { reason: '用户取消或页面离开' }) }
    if (request.kind === 'pause') return { ok: true, data: await api(`/accounts/${encodeURIComponent(execution.account_id)}/pause`, request.error) }
    const body = { executor_id: execution.executor_id, revision: execution.revision }
    if (request.kind === 'resume') return { ok: true, data: await api(`/accounts/${encodeURIComponent(execution.account_id)}/resume`, body) }
    if (request.kind === 'transition') return { ok: true, data: await api(`/operations/${encodeURIComponent(request.clientOperationId)}/transition`, { ...body, status: request.status, ...(request.error ? { error: request.error } : {}) }) }
    const checked = await api(`/accounts/${encodeURIComponent(execution.account_id)}/execution/check`, body)
    if (checked.execution?.executor_id !== execution.executor_id || checked.execution?.revision !== execution.revision || checked.execution?.paused) return failure('execution_lost', '当前页面已失去该账号的执行资格。')
    const accounts = await api('/accounts')
    const account = accounts.accounts?.find((item) => item.id === execution.account_id)
    if (!account || account.platform !== 'boss' || account.platform_account_id !== request.accountId) return failure('account_mismatch', '浏览器账号与当前执行资格的账号不一致。')
    let operation = null
    if (request.clientOperationId) {
      operation = (await api(`/operations/${encodeURIComponent(request.clientOperationId)}`)).operation
      if (!operation || operation.account_id !== execution.account_id || operation.executor_id !== execution.executor_id || operation.revision !== execution.revision || operation.text !== request.text) return failure('operation_mismatch', '发送操作与本次确认内容或执行端不一致。')
      const conversations = await api(`/accounts/${encodeURIComponent(execution.account_id)}/conversations`)
      const conversation = conversations.conversations?.find((item) => item.id === operation.conversation_id)
      if (!conversation || conversation.peer_id !== request.peerId) return failure('conversation_changed', '发送操作的招聘者身份不一致。')
    }
    if (document.hidden || document.visibilityState === 'hidden' || !/^\/career\/messages\/?$/.test(location.pathname)) return failure('page_hidden', '页面可见性或路由已变化，操作已停止。')
    return { ok: true, data: { execution: checked.execution, operation } }
  } catch (error) {
    return failure(error?.code || 'execution_unavailable', error?.name === 'AbortError' ? '执行资格核对超时，操作已停止。' : error?.message || '执行资格核对失败。')
  } finally { clearTimeout(timer) }
}
