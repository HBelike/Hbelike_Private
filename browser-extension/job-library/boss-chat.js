// 只归一化求职者读取结果，未复制上游实现。端点与字段依据：
// https://github.com/can4hou6joeng4/boss-agent-cli/blob/b6e5a0aca58bb67116ed0cfc32febed253802092/src/boss_agent_cli/api/boss.yaml
// https://github.com/jackwener/OpenCLI/blob/97dc0cd5214b9602bcda78d7422f231ee8a803e9/clis/boss/utils.js
// 前者采用列表 GET，后者另有列表/补全路径；本地契约通过不代表真实账号响应已验收。
const CHAT_URL = 'https://www.zhipin.com/web/geek/chat'

export class BossChatReadError extends Error {
  constructor(code, message) {
    super(message)
    this.name = 'BossChatReadError'
    this.code = code
  }
}

function text(value, limit = 2000) {
  return typeof value === 'string' ? value.trim().slice(0, limit) : ''
}

function identifier(value) {
  if (typeof value === 'number') return Number.isSafeInteger(value) && value > 0 ? String(value) : ''
  if (typeof value !== 'string' || value.trim().length > 512) return ''
  const result = text(value, 512)
  return result && !/^(?:0|-1|null|undefined|unknown)$/i.test(result) ? result : ''
}

function pageNumber(value) {
  return Math.max(1, Math.min(100, Math.floor(Number(value) || 1)))
}

function timestamp(value) {
  let milliseconds
  if (typeof value === 'number' || (typeof value === 'string' && /^\d+(?:\.\d+)?$/.test(value.trim()))) {
    const number = Number(value)
    milliseconds = number < 100_000_000_000 ? number * 1000 : number
  } else if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/i.test(value)) {
    milliseconds = Date.parse(value)
  }
  if (!Number.isFinite(milliseconds)) return null
  const parsed = new Date(milliseconds)
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString()
}

function payload(response) {
  const failure = classifyBossChatFailure(response)
  if (failure) throw new BossChatReadError(failure.code, failure.message)
  return response.zpData
}

function sourceUrl(peerId) {
  return `${CHAT_URL}?id=${encodeURIComponent(peerId)}`
}

function hasMore(data) {
  const value = data?.hasMore ?? data?.has_more
  if (value === true || value === 1) return true
  if (value === false || value === 0) return false
  return null
}

export function buildBossChatListEndpoint(page = 1) {
  return `/wapi/zprelation/friend/getGeekFriendList.json?page=${pageNumber(page)}`
}

export function buildBossChatMessagesEndpoint({ peerId, securityId, page = 1, count = 50 } = {}) {
  const peer = identifier(peerId)
  const security = text(securityId, 8192)
  if (!peer || !security) throw new BossChatReadError('invalid_chat_request', '会话缺少招聘者标识或当前读取标识，请刷新列表。')
  const params = new URLSearchParams({
    bossId: peer,
    securityId: security,
    page: String(pageNumber(page)),
    c: String(Math.max(1, Math.min(100, Math.floor(Number(count) || 50)))),
    src: '0'
  })
  return `/wapi/zpchat/geek/historyMsg?${params.toString()}`
}

export function classifyBossChatFailure(response) {
  if (!response || typeof response !== 'object') return { code: 'chat_response_invalid', message: 'BOSS 返回的会话数据无效。' }
  if (response.ok === false && response.error?.code) return response.error
  const code = Number(response.code)
  const message = text(response.message || response.msg, 300)
  if ([9, 429].includes(code) || /操作过于频繁|沟通上限|访问频繁|请求频繁|限流/.test(message)) {
    return { code: 'rate_limited', message: message || 'BOSS 限制了当前读取频率，请稍后手动继续。' }
  }
  if ([31, 32, 35, 36, 38].includes(code) || /安全验证|验证码|环境存在异常|环境异常|账号异常|访问受限/.test(message)) {
    return { code: 'verification_required', message: message || 'BOSS 要求处理账号或访问环境验证。' }
  }
  if ([7, 24, 37, 401].includes(code)) return { code: 'login_required', message: message || '请在 BOSS 页面确认求职者身份并重新登录。' }
  if (code !== 0 || !response.zpData || typeof response.zpData !== 'object') {
    return { code: 'chat_response_invalid', message: message || 'BOSS 未返回可识别的求职者会话数据。' }
  }
  return null
}

export function normalizeBossChatAccount(raw, expectedAccountId = '') {
  const input = raw?.account || raw || {}
  const accountId = identifier(input.platform_account_id ?? input.uid ?? input.userId)
  if (!accountId) throw new BossChatReadError('login_required', '未取得 BOSS 当前账号的稳定 UID，请确认求职者页面已登录。')
  const expected = identifier(expectedAccountId)
  if (expected && expected !== accountId) throw new BossChatReadError('account_changed', 'BOSS 登录账号已变化，请重新选择账号后同步。')
  return { platform: 'boss', platform_account_id: accountId, display_name: text(input.display_name || input.name || input.nickName || input.userName, 240) }
}

export function normalizeBossChatConversations(response, { account, observedAt } = {}) {
  const normalizedAccount = normalizeBossChatAccount(account)
  const data = payload(response)
  const rows = data.result ?? data.friendList ?? data.conversations
  if (!Array.isArray(rows)) throw new BossChatReadError('chat_response_invalid', 'BOSS 求职者会话列表字段发生变化，请更新浏览器助手。')
  const conversations = new Map()
  for (const row of rows) {
    const peerId = identifier(row?.uid ?? row?.friendId ?? row?.bossId)
    if (!peerId || peerId === normalizedAccount.platform_account_id) throw new BossChatReadError('chat_response_invalid', 'BOSS 会话缺少可核对的招聘者 UID。')
    const unread = row.unreadMsgCount ?? row.unreadCount ?? row.unread_count
    const unreadNumber = unread === null || unread === undefined || unread === '' ? null : Number(unread)
    conversations.set(peerId, {
      platform_conversation_id: peerId,
      peer_id: peerId,
      peer_name: text(row.name || row.bossName || row.recruiterName, 240),
      company: text(row.brandName || row.companyName || row.company, 240),
      job_title: text(row.jobName || row.jobTitle, 240),
      unread_count: Number.isSafeInteger(unreadNumber) && unreadNumber >= 0 ? unreadNumber : null,
      last_message_at: timestamp(row.lastTS ?? row.lastMessageInfo?.msgTime ?? row.updateTime ?? row.lastTime),
      last_message_preview: text(row.lastMsg || row.lastMessageInfo?.showText || row.lastMessageInfo?.text, 2000),
      security_id: text(row.securityId || row.security_id, 8192),
      source_url: sourceUrl(peerId)
    })
  }
  return { account: normalizedAccount, conversations: [...conversations.values()], has_more: hasMore(data), observed_at: timestamp(observedAt) || new Date().toISOString() }
}

function messageType(row) {
  const body = row.body && typeof row.body === 'object' ? row.body : {}
  if (body.image || body.images || body.type === 2) return 'image'
  if (body.sound || body.audio || body.voice) return 'audio'
  if (body.file || body.attachment || body.resume) return 'file'
  if (body.type === 1 || (!body.type && (typeof row.text === 'string' || typeof body.text === 'string'))) return 'text'
  return 'unknown'
}

export function normalizeBossChatMessages(response, { account, peerId, observedAt } = {}) {
  const normalizedAccount = normalizeBossChatAccount(account)
  const peer = identifier(peerId)
  if (!peer || peer === normalizedAccount.platform_account_id) throw new BossChatReadError('invalid_chat_request', '招聘者 UID 无效，请重新选择会话。')
  const data = payload(response)
  const rows = data.messages ?? data.historyMsgList
  if (!Array.isArray(rows)) throw new BossChatReadError('chat_response_invalid', 'BOSS 聊天消息字段发生变化，请更新浏览器助手。')
  const messages = new Map()
  for (const row of rows) {
    const messageId = identifier(row?.mid ?? row?.id ?? row?.msgId ?? row?.messageId)
    const senderId = identifier(row?.from?.uid ?? row?.fromUid ?? row?.senderId)
    const recipientId = identifier(row?.to?.uid ?? row?.toUid ?? row?.recipientId)
    const sentAt = timestamp(row?.time ?? row?.timestamp ?? row?.sentAt)
    if (!messageId || !senderId || !sentAt) throw new BossChatReadError('chat_response_invalid', '部分消息缺少真实消息 ID、发送方或原始时间，本次同步未导入。')
    const outgoing = senderId === normalizedAccount.platform_account_id
    if (!outgoing && senderId !== peer) throw new BossChatReadError('conversation_changed', '源消息中的招聘者与当前会话不一致，已停止同步。')
    if (recipientId && recipientId !== (outgoing ? peer : normalizedAccount.platform_account_id)) {
      throw new BossChatReadError('account_changed', '源消息中的账号或会话归属已变化，已停止同步。')
    }
    const type = messageType(row)
    messages.set(messageId, {
      platform_conversation_id: peer,
      platform_message_id: messageId,
      sender_id: senderId,
      direction: outgoing ? 'outgoing' : 'incoming',
      message_type: type,
      text: type === 'text' && typeof (row.body?.text ?? row.text ?? row.body?.content ?? row.body?.showText) === 'string'
        ? (row.body?.text ?? row.text ?? row.body?.content ?? row.body?.showText).slice(0, 20000) : '',
      sent_at: sentAt,
      source_url: sourceUrl(peer)
    })
  }
  return { account: normalizedAccount, conversation: { platform_conversation_id: peer, peer_id: peer }, messages: [...messages.values()], has_more: hasMore(data), observed_at: timestamp(observedAt) || new Date().toISOString() }
}
