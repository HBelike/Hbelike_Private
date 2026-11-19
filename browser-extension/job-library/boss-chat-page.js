// 以 MAIN world 注入的自包含读取函数；不引用扩展模块中的闭包或执行写入操作。
export async function readBossChatPage(request = {}) {
  function value(raw, limit = 8192) {
    if (typeof raw === 'number') return Number.isSafeInteger(raw) && raw > 0 ? String(raw) : ''
    return typeof raw === 'string' ? raw.trim().slice(0, limit) : ''
  }
  function failure(code, message) { return { ok: false, error: { code, message } } }
  function visible(element) {
    if (!element) return false
    const rect = element.getBoundingClientRect?.()
    if (!rect || rect.width <= 0 || rect.height <= 0) return false
    const style = window.getComputedStyle?.(element)
    return !style || (style.display !== 'none' && style.visibility !== 'hidden')
  }
  function pageFailure(url = location.href) {
    const parsed = new URL(url, location.href)
    if (parsed.origin !== 'https://www.zhipin.com') return failure('login_required', '请在 BOSS 官方求职者页面登录后再读取消息。')
    if (/verify|security-check|captcha|\/403\.html/i.test(parsed.pathname)
      || [...document.querySelectorAll('iframe[src*="captcha"],iframe[src*="verify"],.geetest_panel,.captcha-container,[data-captcha]')].some(visible)) {
      return failure('verification_required', 'BOSS 要求安全验证，请在官方页面处理后手动继续。')
    }
    if (/\/web\/(?:user|passport)(?:\/|$)|\/login(?:\/|$)/i.test(parsed.pathname)) return failure('login_required', 'BOSS 登录状态已失效，请登录求职者账号。')
    return null
  }
  function readAccount() {
    const page = window._PAGE
    let user = page
    let uid = page?.uid ?? page?.userId ?? page?.userInfo?.uid
    if (!uid) {
      const root = document.querySelector('#app') || document.querySelector('[data-v-app]')
      const vue = root?.__vue__
      const globals = root?.__vue_app__?.config?.globalProperties
      user = vue?.$store?.state?.userInfo || vue?.$root?.$data?.userInfo || globals?.$store?.state?.userInfo
      uid = user?.uid ?? user?.userId
    }
    const id = value(uid, 512)
    if (!id || /^(?:0|-1|unknown|null|undefined)$/i.test(id)) return null
    if (Number(user?.identity ?? page?.identity) === 1) return null
    return { platform: 'boss', platform_account_id: id, display_name: value(user?.name || user?.nickName || user?.userName, 240) }
  }
  function apiFailure(response) {
    if (!response || typeof response !== 'object') return failure('chat_response_invalid', 'BOSS 返回了无法识别的聊天数据。')
    const code = Number(response.code)
    const message = value(response.message || response.msg, 300)
    if ([9, 429].includes(code) || /操作过于频繁|访问频繁|请求频繁|沟通上限|限流/.test(message)) return failure('rate_limited', message || 'BOSS 限制了读取频率，请稍后手动继续。')
    if ([31, 32, 35, 36, 38].includes(code) || /安全验证|验证码|环境存在异常|环境异常|账号异常|访问受限/.test(message)) return failure('verification_required', message || 'BOSS 要求处理账号或访问环境验证。')
    if ([7, 24, 37, 401].includes(code)) return failure('login_required', message || '请在 BOSS 确认求职者身份并重新登录。')
    if (code !== 0 || !response.zpData || typeof response.zpData !== 'object') return failure('chat_response_invalid', message || 'BOSS 未返回有效求职者聊天数据。')
    return null
  }
  // 仅在 JSON 字符串外保留较长的标识整数，避免 mid/uid 在解析时损失精度。
  function parseResponse(source) {
    let key = ''
    const idKeys = new Set(['mid', 'id', 'msgId', 'messageId', 'clientMid', 'cmid', 'uid', 'userId', 'friendId', 'bossId'])
    const quoted = source.replace(/"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g, (token, offset) => {
      if (token.startsWith('"')) {
        if (/^\s*:/.test(source.slice(offset + token.length))) key = JSON.parse(token)
        return token
      }
      if (idKeys.has(key) && /:\s*$/.test(source.slice(0, offset)) && /^\d{16,}$/.test(token)) return JSON.stringify(token)
      return token
    })
    return JSON.parse(quoted)
  }

  const blocked = pageFailure()
  if (blocked) return blocked
  let account = readAccount()
  // 标签页 complete 不等于求职者应用已挂载；最多等待三秒，不扩展到凭据或对方资料。
  for (let attempt = 0; !account && attempt < 15; attempt += 1) {
    if (Number(window._PAGE?.identity) === 1) break
    await new Promise((resolve) => setTimeout(resolve, 200))
    const delayedBlock = pageFailure()
    if (delayedBlock) return delayedBlock
    account = readAccount()
  }
  if (!account) return failure('login_required', '未取得 BOSS 当前求职者账号的稳定 UID，请确认页面已登录。')
  const expected = value(request.accountId, 512)
  if (expected && expected !== account.platform_account_id) return failure('account_changed', 'BOSS 登录账号已变化，请重新选择账号后同步。')
  const action = String(request.action || 'state').replace(/^boss_chat_/, '')
  if (action === 'state') return { ok: true, data: { account, observed_at: new Date().toISOString() } }
  if (!['list', 'messages'].includes(action)) return failure('invalid_chat_request', '不支持的 BOSS 消息读取操作。')

  const page = Math.max(1, Math.min(100, Math.floor(Number(request.page) || 1)))
  let endpoint = `/wapi/zprelation/friend/getGeekFriendList.json?page=${page}`
  if (action === 'messages') {
    const peer = value(request.peerId, 512)
    const security = value(request.securityId, 8192)
    if (!expected || !peer || !security || peer === account.platform_account_id) return failure('invalid_chat_request', '会话缺少账号、招聘者或当前读取标识，请刷新会话列表。')
    const params = new URLSearchParams({ bossId: peer, securityId: security, page: String(page), c: String(Math.max(1, Math.min(100, Math.floor(Number(request.count) || 50)))), src: '0' })
    endpoint = `/wapi/zpchat/geek/historyMsg?${params.toString()}`
  }

  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 12000)
  try {
    const response = await fetch(endpoint, { method: 'GET', credentials: 'include', cache: 'no-store', headers: { Accept: 'application/json' }, signal: controller.signal })
    const after = readAccount()
    if (!after || after.platform_account_id !== account.platform_account_id) return failure('account_changed', 'BOSS 账号在读取期间发生变化，已丢弃本次结果。')
    const changedPage = pageFailure(response.url || location.href)
    if (changedPage) return changedPage
    if (response.status === 429) return failure('rate_limited', 'BOSS 限制了读取频率，请稍后手动继续。')
    if (response.status === 401) return failure('login_required', 'BOSS 登录状态已失效。')
    if (response.status === 403) return failure('verification_required', 'BOSS 拒绝了当前访问，请在官方页面核对账号和验证状态。')
    if (!response.ok) return failure('chat_read_failed', `BOSS 消息读取失败（HTTP ${response.status}）。`)
    let body
    try {
      const raw = await response.text()
      if (/^\s*</.test(raw) && /captcha|geetest|安全验证|环境存在异常/i.test(raw)) return failure('verification_required', 'BOSS 返回了安全验证页面，请在官方页面处理后手动继续。')
      body = parseResponse(raw)
    } catch {
      return failure('chat_response_invalid', 'BOSS 未返回可识别的聊天 JSON，请检查官方页面状态。')
    }
    const current = readAccount()
    if (!current || current.platform_account_id !== account.platform_account_id) return failure('account_changed', 'BOSS 账号在读取期间发生变化，已丢弃本次结果。')
    const apiError = apiFailure(body)
    if (apiError) return apiError
    return { ok: true, data: { account, response: body, observed_at: new Date().toISOString() } }
  } catch (error) {
    return failure('chat_read_failed', error?.name === 'AbortError' ? 'BOSS 消息读取超时，请稍后手动重试。' : 'BOSS 消息读取失败，请检查网络和官方页面。')
  } finally {
    clearTimeout(timeout)
  }
}
