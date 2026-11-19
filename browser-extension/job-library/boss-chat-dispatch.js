import { normalizeBossChatAccount, normalizeBossChatConversations, normalizeBossChatMessages } from './boss-chat.js'
import { readBossChatPage } from './boss-chat-page.js'
import { checkBossChatApplication } from './boss-chat-control.js'
import { replyBossChatPage } from './boss-chat-reply-page.js'

const PAUSE_CODES = new Set(['verification_required', 'login_required', 'login_or_verification_required', 'rate_limited', 'account_changed', 'account_mismatch', 'conversation_changed'])
const pauseKey = (accountId) => `boss-chat-pause-v1:${accountId}`
const cancelKey = (operationId) => `boss-chat-cancel-v1:${operationId}`
const failed = (code, message, extra = {}) => ({ ok: false, error: { code, message, ...extra } })

export function createBossChatDispatcher({ getChatTab, navigateChat, beforeSource = async () => {} }) {
  const sourceOperations = new Map()

  async function app(sender, request) {
    if (!Number.isInteger(sender?.tab?.id)) return failed('execution_required', '请从招聘沟通页面发起操作。')
    try {
      const results = await chrome.scripting.executeScript({ target: { tabId: sender.tab.id }, func: checkBossChatApplication, args: [request] })
      return results?.[0]?.result || failed('execution_unavailable', '发起页面未返回执行资格，操作已停止。')
    } catch { return failed('execution_unavailable', '发起页面已离开或不可访问，操作已停止。') }
  }

  async function paused(accountId) {
    const key = pauseKey(accountId)
    return (await chrome.storage.local.get(key))[key]
  }

  async function authorize(sender, payload) {
    if (!payload.execution?.account_id || !payload.execution?.executor_id || !Number.isInteger(payload.execution?.revision) || payload.execution.revision < 1 || !payload.accountId) return failed('execution_required', '当前助手需要有效账号执行资格，请更新招聘沟通页面。')
    const pause = await paused(payload.accountId)
    if (pause) return failed('account_paused', pause.message || '该账号已暂停，请人工处理后手动继续。', { stopBatch: true })
    if (payload.clientOperationId && (await chrome.storage.local.get(cancelKey(payload.clientOperationId)))[cancelKey(payload.clientOperationId)]) return failed('operation_cancelled', '本次操作已取消，未触发新的提交。', { submissionState: 'not_submitted' })
    return app(sender, { ...payload, kind: 'check' })
  }

  async function rememberPause(sender, payload, result, observedAccountId) {
    if (result?.ok !== false || !PAUSE_CODES.has(result.error?.code)) return result
    const accountId = payload.accountId || observedAccountId
    if (accountId) await chrome.storage.local.set({ [pauseKey(accountId)]: { code: result.error.code, message: result.error.message, observed_at: new Date().toISOString() } })
    if (payload.execution) await app(sender, { ...payload, kind: 'pause', allowHidden: true, error: { code: result.error.code, message: result.error.message } })
    return { ...result, error: { ...result.error, stopBatch: true } }
  }

  async function source(tabId, func, request) {
    const executions = await chrome.scripting.executeScript({ target: { tabId }, world: 'MAIN', func, args: [request] })
    return executions?.[0]?.result || failed('chat_read_failed', 'BOSS 页面未返回可核对的结果。')
  }

  async function transition(sender, payload, status, error) {
    return app(sender, { ...payload, kind: 'transition', allowHidden: ['submitted', 'unknown', 'failed', 'cancelled'].includes(status), status, error })
  }

  async function stopReply(sender, payload, result, submitted = false) {
    const cancelled = ['operation_cancelled', 'operation_expired', 'page_hidden', 'execution_lost'].includes(result.error?.code)
    const status = submitted || result.error?.submissionState === 'unknown' ? 'unknown' : cancelled ? 'cancelled' : 'failed'
    const tabId = sourceOperations.get(payload.clientOperationId)
    if (tabId) await source(tabId, replyBossChatPage, { clientOperationId: payload.clientOperationId, phase: 'cancel' }).catch(() => undefined)
    const update = await transition(sender, payload, status, status === 'cancelled' ? 'not_submitted' : result.error?.code || '结果待核对')
    await rememberPause(sender, payload, result)
    return { ...result, ...(update.ok ? { data: { operation: update.data.operation, submissionState: status === 'unknown' ? 'unknown' : 'not_submitted' } } : {}) }
  }

  async function reply(sender, payload) {
    if (!payload.peerId || !payload.securityId || !payload.clientOperationId || typeof payload.text !== 'string' || !payload.text.trim()) return failed('chat_identity_missing', '发送缺少已确认的会话或正文。', { submissionState: 'not_submitted' })
    let guard = await authorize(sender, payload)
    if (!guard.ok) return guard
    if (guard.data.operation?.status !== 'pending') return { ok: true, data: { operation: guard.data.operation, duplicate: true } }
    let claimed = await transition(sender, payload, 'executing')
    if (!claimed.ok) return claimed
    if (claimed.data.transitioned !== true) return { ok: true, data: { operation: claimed.data.operation, duplicate: true } }
    let submissionStarted = false
    try {
      const tab = await getChatTab()
      sourceOperations.set(payload.clientOperationId, tab.id)
      await beforeSource()
      guard = await authorize(sender, payload)
      if (!guard.ok) return stopReply(sender, payload, guard)
      const operation = guard.data.operation
      if (operation?.status !== 'executing' || operation.cancel_requested || Date.parse(operation.expires_at) <= Date.now()) return stopReply(sender, payload, failed('operation_expired', '本次确认已失效，请重新确认。'))
      const args = { ...payload, expiresAt: Date.parse(operation.expires_at), phase: 'prepare' }
      let inspected = await source(tab.id, replyBossChatPage, { ...args, phase: 'inspect' })
      if (!inspected.ok) return stopReply(sender, payload, inspected)
      if (inspected.data?.peer_id !== payload.peerId) {
        guard = await authorize(sender, payload)
        if (!guard.ok || guard.data.operation?.status !== 'executing' || guard.data.operation?.cancel_requested) return stopReply(sender, payload, guard.ok ? failed('operation_cancelled', '本次操作已失效，未切换 BOSS 会话。') : guard)
        // 资格核对可能等待网络；导航前再看一次草稿，避免覆盖期间新增的人工输入。
        inspected = await source(tab.id, replyBossChatPage, { ...args, phase: 'inspect' })
        if (!inspected.ok) return stopReply(sender, payload, inspected)
        if (inspected.data?.peer_id !== payload.peerId) {
          if (!navigateChat) return stopReply(sender, payload, failed('conversation_changed', '当前助手无法定位目标会话，未触发提交。'))
          await navigateChat(tab.id, payload.peerId)
        }
        guard = await authorize(sender, payload)
        if (!guard.ok || guard.data.operation?.status !== 'executing' || guard.data.operation?.cancel_requested) return stopReply(sender, payload, guard.ok ? failed('operation_cancelled', '会话定位期间本次操作已失效，未触发提交。') : guard)
      }
      // URL 仅用于定位；填写与点击仍由源页当前会话的稳定 UID 证明目标。
      const prepared = await source(tab.id, replyBossChatPage, args)
      if (!prepared.ok) return stopReply(sender, payload, prepared)
      guard = await authorize(sender, payload)
      if (!guard.ok) return stopReply(sender, payload, guard)
      if (guard.data.operation?.status !== 'executing' || guard.data.operation?.cancel_requested) return stopReply(sender, payload, failed('operation_cancelled', '本次操作已取消，未触发提交。'))
      const committing = await transition(sender, payload, 'submitting')
      if (!committing.ok) return stopReply(sender, payload, committing)
      if (committing.data.transitioned !== true) return stopReply(sender, payload, failed('operation_duplicate', '该操作已由其他请求处理，未再次提交。'))
      guard = await authorize(sender, payload)
      if (!guard.ok || guard.data.operation?.status !== 'submitting' || guard.data.operation?.cancel_requested) return stopReply(sender, payload, guard.ok ? failed('operation_cancelled', '本次操作已取消，未触发提交。') : guard)
      submissionStarted = true
      const result = await source(tab.id, replyBossChatPage, { ...args, phase: 'submit' })
      const submitted = result.data?.submissionState === 'submitted' || result.error?.submissionState === 'unknown'
      if (!result.ok) return stopReply(sender, payload, result, submitted)
      const saved = await transition(sender, payload, 'submitted')
      if (!saved.ok) return failed('submission_unknown', '已触发提交但结果未能保存，请到 BOSS 核对，勿重发旧操作。', { submissionState: 'unknown' })
      return { ok: true, data: { operation: saved.data.operation, status: 'submitted', submissionState: 'submitted' } }
    } catch (error) {
      // 提交阶段注入断线无法判断是否已点击；准备阶段则能确认尚未提交。
      return stopReply(sender, payload, failed(submissionStarted ? 'submission_unknown' : 'execution_unavailable', submissionStarted ? '执行连接已中断，请到 BOSS 核对，勿重发旧操作。' : '准备操作已中断，未触发提交。', { submissionState: submissionStarted ? 'unknown' : 'not_submitted' }), submissionStarted)
    } finally { sourceOperations.delete(payload.clientOperationId) }
  }

  return async function dispatch(action, payload = {}, sender) {
    try {
      if (action === 'boss_chat_cancel') {
        if (!payload.clientOperationId) return failed('operation_missing', '缺少要取消的发送操作。')
        await chrome.storage.local.set({ [cancelKey(payload.clientOperationId)]: Date.now() })
        const tabId = sourceOperations.get(payload.clientOperationId)
        // 源页响应慢也不能拖住服务端取消；本地失效标记已先持久保存。
        if (tabId) void source(tabId, replyBossChatPage, { clientOperationId: payload.clientOperationId, phase: 'cancel' }).catch(() => undefined)
        const result = await app(sender, { ...payload, kind: 'cancel', allowHidden: true })
        return result.ok ? { ok: true, data: { operation: result.data.operation } } : result
      }
      if (action === 'boss_chat_resume') {
        const result = await app(sender, { ...payload, kind: 'resume' })
        if (result.ok && payload.accountId) await chrome.storage.local.remove(pauseKey(payload.accountId))
        return result
      }
      if (action === 'boss_chat_reply') return reply(sender, payload)
      const operation = { boss_chat_state: 'state', boss_chat_list: 'list', boss_chat_messages: 'messages' }[action]
      if (!operation) return failed('unsupported_action', '不支持的招聘沟通动作。')
      if (operation !== 'state' && (!payload.execution || !payload.accountId)) return failed('execution_required', '请先取得当前账号执行资格。')
      if (operation === 'messages' && (!payload.peerId || !payload.securityId)) return failed('chat_identity_missing', '会话身份不完整，请重新同步会话列表。')
      if (operation !== 'state') {
        const guard = await authorize(sender, payload)
        if (!guard.ok) return guard
      }
      const tab = await getChatTab()
      await beforeSource()
      if (operation !== 'state') {
        const guard = await authorize(sender, payload)
        if (!guard.ok) return guard
      }
      const result = await source(tab.id, readBossChatPage, { action: operation, accountId: payload.accountId || '', peerId: payload.peerId || '', securityId: payload.securityId || '', page: payload.page ?? 1, count: payload.count ?? 50 })
      if (!result.ok) return rememberPause(sender, payload, result)
      const account = normalizeBossChatAccount(result.data?.account, payload.accountId)
      const observedAt = result.data?.observed_at
      const data = operation === 'state'
        ? { account, observed_at: observedAt, paused: Boolean(await paused(account.platform_account_id)), pause: await paused(account.platform_account_id) || null }
        : operation === 'list' ? normalizeBossChatConversations(result.data?.response, { account, observedAt }) : normalizeBossChatMessages(result.data?.response, { account, peerId: payload.peerId, observedAt })
      return { ok: true, data }
    } catch (error) {
      return rememberPause(sender, payload, failed(error?.code || 'chat_read_failed', error?.message || '招聘沟通操作失败，请检查页面后手动继续。', { stopBatch: true }))
    }
  }
}
