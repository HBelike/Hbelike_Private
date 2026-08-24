function text(value, maximum = 800) {
  return typeof value === 'string' ? value.trim().slice(0, maximum) : ''
}

export function normalizeGreetingPayload(payload = {}) {
  const normalized = {
    securityId: text(payload.securityId, 600),
    jobId: text(payload.jobId, 300),
    bossId: text(payload.bossId, 300),
    lid: text(payload.lid, 300),
    message: text(payload.message, 2000)
  }
  if (!normalized.securityId) throw new Error('岗位安全标识不能为空。')
  if (!normalized.jobId) throw new Error('岗位标识不能为空。')
  if (!normalized.bossId) throw new Error('招聘者标识不能为空。')
  if (!normalized.lid) throw new Error('岗位来源标识不能为空。')
  if (!normalized.message) throw new Error('招呼语不能为空。')
  return normalized
}

export function buildBossChatUrl(job = {}) {
  const normalized = normalizeGreetingPayload({
    ...job,
    message: job.message || 'preflight'
  })
  const url = new URL('https://www.zhipin.com/web/geek/chat')
  url.search = new URLSearchParams({
    id: normalized.bossId,
    jobId: normalized.jobId,
    securityId: normalized.securityId,
    lid: normalized.lid
  }).toString()
  return url.toString()
}

function responseMessage(response) {
  return text(
    response?.zpData?.bizData?.chatRemindDialog?.content
      || response?.zpData?.bizData?.chatRemindDialog?.title
      || response?.message
      || response?.msg,
    500
  )
}

export const CHAT_DELIVERY_EVIDENCE = Object.freeze({
  stableMs: 2000,
  successClassPattern: '(?:^|\\s)(?:status-delivery|status-read|status-success|status-sent)(?:\\s|$)',
  failureClassPattern: '(?:^|\\s)(?:status-fail|status-failed|status-error|send-fail)(?:\\s|$)',
  successTextPattern: '已送达|已读|发送成功',
  failureTextPattern: '发送失败|发送异常|重新发送|点击重试'
})

export function classifyOutgoingMessageEvidence(evidence = {}, rules = CHAT_DELIVERY_EVIDENCE) {
  const statusClasses = text(evidence.statusClasses, 800)
  const statusText = text(evidence.statusText, 300)
  if (new RegExp(rules.failureClassPattern, 'i').test(statusClasses)
    || new RegExp(rules.failureTextPattern).test(statusText)) {
    return 'failed'
  }
  if (new RegExp(rules.successClassPattern, 'i').test(statusClasses)
    || new RegExp(rules.successTextPattern).test(statusText)) {
    return 'sent'
  }
  if (evidence.isNew === true
    && evidence.inputCleared === true
    && Number(evidence.stableForMs) >= Number(rules.stableMs)) {
    return 'sent'
  }
  return 'pending'
}

export function classifyFriendAddResponse(response) {
  const code = Number(response?.code)
  const message = responseMessage(response)
  if (code === 0 && response?.zpData?.showGreeting === true) {
    return { ok: true, defaultGreetingSent: true }
  }
  if (code === 0) return { ok: true }
  if (code === 37) return { code: 'login_required', message: 'BOSS 登录状态已失效。', stopBatch: true }
  if (/安全验证|验证码|环境存在异常|账号异常/.test(message)) {
    return { code: 'verification_required', message: message || 'BOSS 要求安全验证。', stopBatch: true }
  }
  if (/操作过于频繁|沟通上限|沟通额度|已与\d+位BOSS沟通|限流/.test(message) || code === 429) {
    return { code: 'rate_limited', message: message || 'BOSS 限制了当前沟通频率。', stopBatch: true }
  }
  if (/职位.*(?:下线|失效|不存在|已关闭)|停止招聘/.test(message)) {
    return { code: 'job_unavailable', message: message || '岗位已失效。', stopBatch: false }
  }
  if (/已经?沟通过|已是好友|已建立沟通/.test(message)) {
    return { code: 'already_contacted', message: message || '此前已与该招聘者沟通过。', stopBatch: false }
  }
  return { code: 'boss_api_error', message: message || 'BOSS 未能建立沟通关系。', stopBatch: true }
}

export function shouldStopGreetingBatch(code) {
  return new Set([
    'verification_required',
    'rate_limited',
    'login_required',
    'send_unknown',
    'extension_error',
    'boss_api_error'
  ]).has(text(code, 80))
}
