// 单独的持续回复函数；不建联，不按历史正文猜测发送结果。
export function replyBossChatPage(request = {}) {
  const registryKey = '__findJobBossChatOperationsV1'
  const operations = window[registryKey] ||= Object.create(null)
  for (const [key, operation] of Object.entries(operations)) {
    if (Number.isFinite(operation.expiresAt) && Date.now() >= operation.expiresAt) delete operations[key]
  }
  const id = typeof request.clientOperationId === 'string' ? request.clientOperationId : ''
  const fail = (code, message) => ({ ok: false, error: { code, message, submissionState: 'not_submitted' } })
  if (!id) return fail('operation_missing', '缺少已确认的发送操作。')
  if (request.phase === 'cancel') {
    const previous = operations[id]
    operations[id] = { cancelled: true, submitted: previous?.submitted === true, expiresAt: previous?.expiresAt || Date.now() + 30000 }
    return { ok: true, data: { submitted: operations[id].submitted } }
  }
  function identifier(raw) {
    if (typeof raw === 'number') return Number.isSafeInteger(raw) && raw > 0 ? String(raw) : ''
    return typeof raw === 'string' && raw.trim().length <= 512 ? raw.trim() : ''
  }
  function visible(element) {
    if (!element) return false
    const rect = element.getBoundingClientRect()
    const style = window.getComputedStyle(element)
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden'
  }
  function checkIdentity(inspect = false) {
    if (location.origin !== 'https://www.zhipin.com' || /\/web\/(?:user|passport)(?:\/|$)|\/login(?:\/|$)/i.test(location.pathname)) return fail('login_required', 'BOSS 登录状态已失效，请登录求职者账号。')
    if (/verify|captcha|security-check|\/403\.html/i.test(location.pathname) || [...document.querySelectorAll('iframe[src*="captcha"],iframe[src*="verify"],.geetest_panel,.captcha-container,[data-captcha]')].some(visible)) return fail('verification_required', 'BOSS 要求验证，请人工处理后继续。')
    if (!/^\/web\/geek\/chat\/?$/.test(location.pathname)) return fail('conversation_changed', 'BOSS 沟通页面已变化。')
    const notices = [...document.querySelectorAll('.toast,.toast-text,.dialog-message,[role="alert"]')].filter(visible).map((element) => element.textContent || '').join(' ')
    if (/操作过于频繁|访问频繁|请求频繁|沟通上限|限流/.test(notices)) return fail('rate_limited', 'BOSS 限制了当前沟通频率，请稍后手动继续。')
    const root = document.querySelector('#app') || document.querySelector('[data-v-app]')
    const state = root?.__vue__?.$store?.state || root?.__vue_app__?.config?.globalProperties?.$store?.state || {}
    const page = window._PAGE || {}
    const user = (page.uid ?? page.userId ?? page.userInfo?.uid) ? page : state.userInfo || {}
    const account = identifier(user.uid ?? user.userId ?? user.userInfo?.uid)
    if (!account || Number(user.identity) === 1 || account !== request.accountId) return fail('account_changed', 'BOSS 当前求职账号与确认账号不一致。')
    // 仅从明确的当前会话上下文取 UID，不能把 URL、姓名或岗位当作身份。
    const input = document.querySelector('#chat-input')
    const node = input?.closest?.('[data-peer-uid],[data-friend-uid]')
      || document.querySelector('[data-current-peer-uid]')
    const chat = state.chat?.currentFriend || state.chat?.currentConversation || state.chat?.currentChat
      || state.chatInfo?.currentFriend || state.currentFriend || state.currentConversation
    const peer = identifier(node?.getAttribute('data-peer-uid') || node?.getAttribute('data-friend-uid') || node?.getAttribute('data-current-peer-uid') || chat?.uid || chat?.friendId || chat?.bossId || chat?.peerId)
    if (!inspect && (!peer || peer !== request.peerId)) return fail('conversation_changed', '无法核对 BOSS 当前招聘者，未触发填写或提交。请到 BOSS 核对后重新确认。')
    const button = document.querySelector('.chat-op .btn-send')
    if (!inspect && (!input || !button || !visible(input) || !visible(button))) return fail('chat_unavailable', 'BOSS 聊天输入框尚不可用。')
    return { input, button, peer }
  }
  const checked = checkIdentity(request.phase === 'inspect')
  if (checked.ok === false) return checked
  const previous = operations[id]
  if (previous?.submitted) return { ok: true, data: { status: 'submitted', submissionState: 'submitted', duplicate: true } }
  if (previous?.cancelled || !Number.isFinite(request.expiresAt) || Date.now() >= request.expiresAt) return fail('operation_expired', '本次发送已取消或超过确认期限，请重新确认。')
  if (request.phase === 'inspect') {
    if (checked.input?.textContent?.trim()) return fail('draft_exists', 'BOSS 当前会话有未发送草稿，请先处理，助手不会切走该会话。')
    return { ok: true, data: { peer_id: checked.peer, has_draft: false } }
  }
  const text = typeof request.text === 'string' ? request.text : ''
  if (!text.trim() || text.length > 20000) return fail('message_invalid', '回复内容为空或超过允许长度。')
  if (request.phase === 'prepare') {
    if (previous) return fail('operation_duplicate', '该操作已经开始，不能再次填写。')
    if (checked.input.textContent?.trim()) return fail('draft_exists', 'BOSS 输入框已有未发送草稿，请先在 BOSS 处理。')
    operations[id] = { accountId: request.accountId, peerId: request.peerId, text, input: checked.input, expiresAt: request.expiresAt, cancelled: false, submitted: false }
    checked.input.focus()
    const focused = checkIdentity()
    if (focused.ok === false || focused.input !== checked.input || checked.input.textContent?.trim()) return focused.ok === false ? focused : fail('draft_changed', '聚焦后输入框已变化，未填写或提交。')
    checked.input.textContent = text
    checked.input.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }))
    checked.input.dispatchEvent(new Event('change', { bubbles: true }))
    const after = checkIdentity()
    if (after.ok === false || after.input !== checked.input || checked.input.textContent !== text) return after.ok === false ? after : fail('draft_changed', '填写过程中输入框已变化，未触发提交。')
    return { ok: true, data: { status: 'prepared' } }
  }
  if (request.phase !== 'submit' || !previous || previous.input !== checked.input || previous.text !== text || previous.peerId !== request.peerId || previous.accountId !== request.accountId || checked.input.textContent !== text) return fail('draft_changed', '输入框或本次确认内容已变化，未触发提交。')
  if (previous.cancelled || Date.now() >= request.expiresAt) return fail('operation_expired', '本次发送已失效，未触发提交。')
  if (checked.button.disabled || checked.button.getAttribute('aria-disabled') === 'true') return fail('send_disabled', 'BOSS 发送按钮当前不可用。')
  // 同一 JS 任务内最后核对再点击；点击后只能记录已提交，不能据此宣称平台接受。
  previous.submitted = true
  delete previous.text
  delete previous.input
  try { checked.button.click() } catch { return { ok: false, error: { code: 'submission_unknown', message: '触发提交时发生异常，请到 BOSS 核对，勿重发旧操作。', submissionState: 'unknown' } } }
  return { ok: true, data: { status: 'submitted', submissionState: 'submitted' } }
}
