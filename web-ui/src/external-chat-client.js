const ROOT = '/api/career/external-chat'
const WINDOW_MS = 30 * 24 * 60 * 60 * 1000
const PAUSE_CODES = new Set(['verification_required', 'login_required', 'login_or_verification_required', 'rate_limited', 'account_mismatch', 'boss_account_mismatch', 'chat_account_mismatch', 'account_changed', 'conversation_changed', 'session_refresh_failed'])

export class ExternalChatError extends Error {
  constructor(code, message, details = {}) {
    super(message)
    this.code = code
    this.status = details.status
    this.path = details.path
    this.method = details.method
  }
}

export function createExternalChatClient(request = fetch) {
  async function json(path, { signal, ...options } = {}) {
    const response = await request(`${ROOT}${path}`, { credentials: 'same-origin', cache: 'no-store', signal, ...options })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      const details = { status: response.status, path: `${ROOT}${path}`, method: options.method || 'GET' }
      // FastAPI 未匹配路由时使用裸 Not Found；业务资源不存在则保留后端的结构化错误。
      if (response.status === 404 && typeof payload.detail === 'string' && payload.detail.trim().toLowerCase() === 'not found') {
        throw new ExternalChatError('backend_route_unavailable', '招聘沟通接口尚未就绪，请更新或重启后端服务后重新检测。', details)
      }
      throw new ExternalChatError(payload.detail?.code || 'history_unavailable', payload.detail?.message || (typeof payload.detail === 'string' ? payload.detail : '读取招聘沟通记录失败，请稍后重试。'), details)
    }
    return payload
  }
  return {
    accounts: (options) => json('/accounts', options),
    conversations: (id, options) => json(`/accounts/${encodeURIComponent(id)}/conversations`, options),
    messages: (id, { before = '', signal } = {}) => json(`/conversations/${encodeURIComponent(id)}/messages?${new URLSearchParams({ limit: '100', ...(before ? { before } : {}) })}`, { signal }),
    sync: (payload, options) => json('/sync', { ...options, method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(externalChatSyncPayload(payload)) }),
    claimExecution: (id, payload, options) => post(`/accounts/${encodeURIComponent(id)}/execution`, payload, options),
    checkExecution: (id, payload, options) => post(`/accounts/${encodeURIComponent(id)}/execution/check`, payload, options),
    releaseExecution: (id, payload, options) => post(`/accounts/${encodeURIComponent(id)}/execution/release`, payload, options),
    pauseAccount: (id, payload, options) => post(`/accounts/${encodeURIComponent(id)}/pause`, payload, options),
    resumeAccount: (id, payload, options) => post(`/accounts/${encodeURIComponent(id)}/resume`, payload, options),
    operations: (id, options) => json(`/accounts/${encodeURIComponent(id)}/operations`, options),
    operation: (id, options) => json(`/operations/${encodeURIComponent(id)}`, options),
    createOperation: (id, payload, options) => post(`/conversations/${encodeURIComponent(id)}/operations`, payload, options),
    cancelOperation: (id, payload, options) => post(`/operations/${encodeURIComponent(id)}/cancel`, payload, options)
  }
  function post(path, payload, options = {}) {
    return json(path, { ...options, method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
  }
}

export function externalChatSyncPayload({ account, conversations = [], messages = [] }) {
  // 动态 security_id 只留在浏览器，不能作为平台历史的一部分上传。
  const fields = (value, names) => Object.fromEntries(names.filter((name) => value?.[name] !== undefined).map((name) => [name, value[name]]))
  return {
    account: fields(account, ['platform', 'platform_account_id', 'display_name']),
    conversations: conversations.map((item) => fields(item, ['platform_conversation_id', 'peer_id', 'peer_name', 'company', 'job_title', 'unread_count', 'last_message_at', 'last_message_preview', 'source_url'])),
    messages: messages.map((item) => fields(item, ['platform_conversation_id', 'platform_message_id', 'sender_id', 'direction', 'message_type', 'text', 'sent_at', 'source_url']))
  }
}

export function recentExternalChatMessages(messages, now = Date.now()) {
  const unique = new Map()
  for (const message of Array.isArray(messages) ? messages : []) {
    const time = Date.parse(message.sent_at)
    if (!Number.isFinite(time) || time < now - WINDOW_MS || !message.platform_message_id) continue
    unique.set(String(message.platform_message_id), message)
  }
  return [...unique.values()].sort(compareMessages)
}

function compareMessages(left, right) {
  return Date.parse(left.sent_at) - Date.parse(right.sent_at) || String(left.id || left.platform_message_id).localeCompare(String(right.id || right.platform_message_id))
}

function isPauseError(error) {
  return PAUSE_CODES.has(error?.code) || error?.stopBatch === true
}

export function bossChatSourceUrl(value) {
  try {
    const url = new URL(value)
    if (url.protocol === 'https:' && url.hostname === 'www.zhipin.com' && /^\/web\/geek\/chat(?:\/|$)/.test(url.pathname)) return url.href
  } catch { /* 没有源链接时打开 BOSS 官方聊天首页。 */ }
  return 'https://www.zhipin.com/web/geek/chat'
}

export function externalMessageTypeLabel(type) {
  return ({ image: '图片消息', file: '附件消息', audio: '语音消息', unknown: '暂不支持的消息' })[type] || '暂不支持的消息'
}

function sameAccount(left, right) {
  return left?.platform === 'boss' && right?.platform === 'boss' && String(left.platform_account_id || '') !== '' && String(left.platform_account_id) === String(right.platform_account_id)
}

function requireAccount(expected, observed) {
  if (!sameAccount(expected, observed)) throw new ExternalChatError('account_mismatch', '当前浏览器的 BOSS 账号与所选历史账号不一致，同步已暂停。请切换 BOSS 账号或选择对应的历史账号，再手动继续。')
}

export function createExternalChatInbox({ api, bridge, onChange = () => {}, now = Date.now, executorId = globalThis.crypto.randomUUID() }) {
  let disposed = false
  let visible = true
  let visibilityRevision = 0
  let accountRevision = 0
  let conversationRevision = 0
  let pauseRevision = 0
  let refreshRevision = 0
  let refreshPending = null
  let execution = null
  const lifecycle = new AbortController()
  const liveConversations = new Map()
  const completedSource = new Map()
  const activeOperations = new Map()
  let state = { accounts: [], selectedAccountId: '', conversations: [], selectedConversationId: '', messages: [], hasMore: false, loading: true, loadingMessages: false, syncing: false, connection: 'checking', error: '', pauseReason: '', lastSyncedAt: '', browserAccount: null, listHasMore: false, syncIncomplete: '', execution: null, operations: [], sending: false, canReply: false }
  const currentAccount = () => state.accounts.find((item) => item.id === state.selectedAccountId)
  const currentConversation = () => state.conversations.find((item) => item.id === state.selectedConversationId)
  const current = (revision) => !disposed && revision === accountRevision
  const currentMessage = (revision, messageRevision) => current(revision) && messageRevision === conversationRevision
  const options = () => ({ signal: lifecycle.signal })
  const executionPayload = () => ({ executor_id: executorId, revision: execution?.revision })
  const bridgeExecution = () => ({ account_id: state.selectedAccountId, ...executionPayload() })

  function publish(patch = {}) {
    if (disposed) return
    state = { ...state, ...patch }
    onChange({ ...state })
  }

  function fail(error, account = currentAccount()) {
    if (disposed || error?.name === 'AbortError') return
    const code = error?.code || ''
    const message = error?.message || '同步失败，请检查浏览器助手后手动重试。'
    if (code === 'backend_route_unavailable') {
      publish({ connection: 'service_unavailable', error: message, syncing: false, canReply: false })
    } else if (['execution_busy', 'execution_lost', 'execution_in_progress'].includes(code)) {
      execution = null
      publish({ connection: 'history_only', execution: null, syncing: false, error: message })
    } else if (isPauseError(error) || code === 'account_paused') {
      pauseRevision += 1
      publish({ connection: 'paused', pauseReason: message, error: '', syncing: false })
      // 已丢弃的旧会话仍可报告账号级限制；只保存原因，不上传正文。
      if (account && code !== 'account_paused' && !['account_mismatch', 'account_changed'].includes(code)) {
        void api.pauseAccount(account.id, { code, message }, options()).catch(() => {})
      }
    } else if (['extension_unavailable', 'bridge_timeout', 'bridge_disconnected', 'request_timeout', 'bridge_error'].includes(code)) publish({ connection: 'offline', error: message })
    else publish({ error: message })
  }

  function prune() {
    const cutoff = now() - WINDOW_MS
    publish({ messages: recentExternalChatMessages(state.messages, now()), operations: state.operations.filter((item) => (item.retain_until ? Date.parse(item.retain_until) >= now() : Date.parse(item.created_at) >= cutoff)), conversations: state.conversations.map((item) => !Number.isFinite(Date.parse(item.last_message_at)) || Date.parse(item.last_message_at) < cutoff ? { ...item, last_message_preview: '', last_message_at: null, unread_count: null } : item) })
  }

  async function checkExecution(account) {
    if (!execution || !visible || disposed) throw new ExternalChatError('execution_lost', '当前页面没有活动执行资格，请在此接管后继续。')
    const revision = accountRevision
    const visibilityToken = visibilityRevision
    const heldRevision = execution.revision
    const result = await api.checkExecution(account.id, { executor_id: executorId, revision: heldRevision }, options())
    if (!current(revision) || !visible || visibilityToken !== visibilityRevision) return
    if (state.selectedAccountId !== account.id || execution?.revision !== heldRevision) throw new ExternalChatError('execution_lost', '账号或执行资格已变化，本次旧检查结果已丢弃。')
    execution = result.execution
    publish({ execution })
  }

  async function readOperations(account = currentAccount()) {
    if (!account) return
    const revision = accountRevision
    const result = await api.operations(account.id, options())
    if (current(revision)) publish({ operations: result.operations || [] })
  }

  async function readHistoryMessages(conversation, revision, messageRevision, { before = '', append = false, refreshLatest = false } = {}) {
    const result = await api.messages(conversation.id, { ...options(), before })
    if (!currentMessage(revision, messageRevision)) return
    const retained = recentExternalChatMessages(state.messages, now())
    const incoming = recentExternalChatMessages(result.messages, now())
    const keepBoundary = refreshLatest && retained.length > 0 && (!incoming.length || compareMessages(retained[0], incoming[0]) < 0)
    publish({ messages: recentExternalChatMessages(append || refreshLatest ? [...retained, ...incoming] : incoming, now()), hasMore: keepBoundary ? state.hasMore : result.has_more === true })
  }

  async function readSourceMessages(conversation, revision, messageRevision) {
    const account = currentAccount()
    const live = liveConversations.get(String(conversation.platform_conversation_id))
    if (!account || state.connection !== 'ready' || !visible || !currentMessage(revision, messageRevision)) return
    if (!live?.security_id) { publish({ syncIncomplete: '当前列表尚未取得此会话的有效读取标识，请重新同步或到 BOSS 核对。' }); return }
    const pauseToken = pauseRevision
    const visibilityToken = visibilityRevision
    const active = () => currentMessage(revision, messageRevision) && visible && visibilityToken === visibilityRevision && pauseToken === pauseRevision && state.connection === 'ready'
    try {
      requireAccount(account, state.browserAccount)
      const previous = completedSource.get(conversation.id)
      const seenPages = new Set()
      let head = []
      let complete = false
      for (let page = 1; page <= 100 && active(); page += 1) {
        await checkExecution(account)
        if (!active()) return
        const result = await bridge.getBossMessages({ accountId: account.platform_account_id, peerId: conversation.peer_id, securityId: live.security_id, page, count: 50, execution: bridgeExecution() })
        if (!current(revision) || visibilityToken !== visibilityRevision) return
        requireAccount(account, result.account)
        if (!active()) return
        if (String(result.conversation?.platform_conversation_id) !== String(conversation.platform_conversation_id) || String(result.conversation?.peer_id) !== String(conversation.peer_id) || (result.messages || []).some((item) => String(item.platform_conversation_id) !== String(conversation.platform_conversation_id))) throw new ExternalChatError('account_mismatch', '浏览器返回的会话身份发生变化，同步已暂停。')
        const rows = result.messages || []
        const ids = rows.map((item) => String(item.platform_message_id))
        if (page === 1) head = ids
        const fingerprint = [...ids].sort().join('|')
        if (seenPages.has(fingerprint)) { publish({ syncIncomplete: '源平台返回重复页，历史尚未补齐，请到 BOSS 核对后重新同步。' }); break }
        seenPages.add(fingerprint)
        await api.sync({ account: result.account, conversations: [live], messages: rows }, options())
        if (!active()) return
        // 只有前一次已经确认完整的区间，才能作为增量同步的衔接边界。
        if (result.has_more === false || (previous?.size && ids.some((id) => previous.has(id)))) { complete = true; break }
        if (result.has_more !== true) { publish({ syncIncomplete: '源平台未提供可靠的历史结束信号，已保存取得的消息，其余记录待核对。' }); break }
        if (!rows.length || page === 100) publish({ syncIncomplete: '本次历史补齐尚未完成，已取得的消息已保存，可重新同步或到 BOSS 核对。' })
      }
      if (!active()) return
      if (complete) { completedSource.set(conversation.id, new Set(head)); publish({ syncIncomplete: '' }) }
      await readHistoryMessages(conversation, revision, messageRevision, { refreshLatest: true })
      if (active()) publish({ lastSyncedAt: new Date(now()).toISOString() })
    } catch (error) {
      // 已返回的账号限制仍须上报；旧可见周期的局部错误不能覆盖前台新同步。
      if (active() || (current(revision) && (isPauseError(error) || error?.code === 'account_paused'))) throw error
    }
  }

  async function selectConversation(id, { readSource = true } = {}) {
    if (disposed) return
    void cancelPending('切换会话')
    const revision = accountRevision
    const messageRevision = ++conversationRevision
    const conversation = state.conversations.find((item) => item.id === id)
    publish({ selectedConversationId: conversation?.id || '', messages: [], hasMore: false, loadingMessages: Boolean(conversation), error: '', syncIncomplete: '' })
    if (!conversation) return
    try {
      await readHistoryMessages(conversation, revision, messageRevision)
      if (readSource && currentMessage(revision, messageRevision)) await readSourceMessages(conversation, revision, messageRevision)
    } catch (error) { if (current(revision) && (isPauseError(error) || currentMessage(revision, messageRevision))) fail(error) }
    finally { if (currentMessage(revision, messageRevision)) publish({ loadingMessages: false }) }
  }

  async function releaseExecution() {
    const id = state.selectedAccountId
    const held = execution
    execution = null
    if (id && held) await api.releaseExecution(id, { executor_id: executorId, revision: held.revision }).catch(() => {})
  }

  async function selectAccount(id, { synchronize = true } = {}) {
    const revision = ++accountRevision
    conversationRevision += 1
    refreshPending = null
    await cancelPending('切换账号')
    await releaseExecution()
    if (!current(revision)) return
    liveConversations.clear()
    completedSource.clear()
    publish({ selectedAccountId: id, conversations: [], selectedConversationId: '', messages: [], operations: [], hasMore: false, loading: Boolean(id), loadingMessages: false, lastSyncedAt: '', error: '', pauseReason: '', execution: null, connection: 'checking' })
    try {
      if (id) {
        const result = await api.conversations(id, options())
        if (!current(revision)) return
        publish({ conversations: (result.conversations || []).filter((item) => item.account_id === id), lastSyncedAt: currentAccount()?.last_synced_at || '' })
        await readOperations()
      }
      if (current(revision) && synchronize) await refresh({ manual: true })
    } catch (error) { if (current(revision)) fail(error) }
    finally { if (current(revision)) publish({ loading: false }) }
  }

  function refresh({ manual = false, takeover = false } = {}) {
    if (disposed) return Promise.resolve()
    prune()
    if (!visible || (!manual && state.connection !== 'ready')) return Promise.resolve()
    if (refreshPending) return refreshPending
    const revision = accountRevision
    const pauseToken = pauseRevision
    const visibilityToken = visibilityRevision
    const refreshToken = ++refreshRevision
    const resuming = manual && state.connection === 'paused'
    const recheckingService = manual && state.connection === 'service_unavailable'
    const active = () => current(revision) && visible && visibilityToken === visibilityRevision && pauseToken === pauseRevision
    publish({ syncing: true, error: '' })
    const task = (async () => {
      try {
        if (recheckingService) {
          // 后端仍缺路由时先终止，不能把“重新检测”变成反复访问 BOSS。
          const history = await api.accounts(options())
          if (!active()) return
          publish({ accounts: (history.accounts || []).filter((item) => item.platform === 'boss') })
          await readOperations()
          if (!active()) return
        }
        if (manual || state.connection !== 'ready') {
          const ping = await bridge.ping()
          if (!active()) return
          if (ping?.connected === false) throw new ExternalChatError('extension_unavailable', '浏览器助手未连接，仍可查看已保存的历史。')
          if (!ping?.capabilities?.includes('boss_chat_execute_v1')) { publish({ connection: 'needs_update', error: '请更新浏览器助手，以支持分页、统一暂停和执行资格检查。' }); return }
          publish({ canReply: ping.capabilities.includes('boss_chat_reply') })
        }
        const sourceState = await bridge.getBossChatState()
        if (!active()) return
        if (!sourceState.account?.platform_account_id || sourceState.account.platform !== 'boss') throw new ExternalChatError('login_required', '请先登录 BOSS 求职者账号，再手动继续。')
        let account = currentAccount()
        if (account) requireAccount(account, sourceState.account)
        else {
          const registered = await api.sync({ account: sourceState.account, conversations: [], messages: [] }, options())
          if (!active()) return
          const accounts = (await api.accounts(options())).accounts || []
          account = accounts.find((item) => sameAccount(item, sourceState.account))
          if (!account || registered.account?.id !== account.id) throw new ExternalChatError('account_mismatch', '平台保存的账号身份不一致。')
          publish({ accounts: accounts.filter((item) => item.platform === 'boss'), selectedAccountId: account.id })
        }
        publish({ browserAccount: sourceState.account })
        const claimed = execution && !takeover && !resuming
          ? await api.checkExecution(account.id, executionPayload(), options())
          : await api.claimExecution(account.id, { executor_id: executorId, takeover }, options())
        if (!active()) return
        execution = claimed.execution
        if (execution?.paused) {
          if (!resuming) throw new ExternalChatError('account_paused', execution.pause_reason || '此账号已暂停，请处理后手动继续。')
          // 本轮本人身份探测已成功，随后才解除共享暂停并清理扩展暂停。
          execution = (await api.resumeAccount(account.id, executionPayload(), options())).execution
        }
        if (resuming) await bridge.resumeBossChat({ accountId: account.platform_account_id, execution: bridgeExecution() })
        const live = new Map()
        const seenPages = new Set()
        let incomplete = ''
        let more = false
        for (let page = 1; page <= 100 && active(); page += 1) {
          await checkExecution(account)
          if (!active()) return
          const list = await bridge.listBossConversations({ accountId: account.platform_account_id, page, execution: bridgeExecution() })
          requireAccount(account, list.account)
          if (!active()) return
          const rows = list.conversations || []
          const fingerprint = rows.map((item) => item.platform_conversation_id).sort().join('|')
          if (seenPages.has(fingerprint)) { incomplete = '会话列表返回重复页，更多联系人尚未补齐。'; more = true; break }
          seenPages.add(fingerprint)
          for (const item of rows) live.set(String(item.platform_conversation_id), item)
          await api.sync({ account: list.account, conversations: rows, messages: [] }, options())
          if (list.has_more === false) break
          more = true
          if (list.has_more !== true) { incomplete = '会话列表结束状态未知，仅显示已取得和已保存的联系人。'; break }
          if (!rows.length || page === 100) { incomplete = '更多联系人尚未补齐，请到 BOSS 核对。'; break }
          more = false
        }
        if (!active()) return
        const accounts = (await api.accounts(options())).accounts || []
        const result = await api.conversations(account.id, options())
        if (!active()) return
        liveConversations.clear()
        for (const [key, value] of live) liveConversations.set(key, value)
        const conversations = (result.conversations || []).filter((item) => item.account_id === account.id)
        publish({ accounts: accounts.filter((item) => item.platform === 'boss'), conversations, connection: 'ready', pauseReason: '', lastSyncedAt: new Date(now()).toISOString(), listHasMore: more, syncIncomplete: incomplete, execution })
        await readOperations(account)
        const selected = conversations.find((item) => item.id === state.selectedConversationId)
        if (selected && active()) await readSourceMessages(selected, revision, conversationRevision)
      } catch (error) {
        if (active() || (current(revision) && (isPauseError(error) || error?.code === 'account_paused'))) fail(error)
      }
      finally { if (current(revision) && visibilityToken === visibilityRevision && refreshToken === refreshRevision) publish({ syncing: false }) }
    })().finally(() => { if (refreshPending === task) refreshPending = null })
    refreshPending = task
    return task
  }

  async function loadOlder() {
    const conversation = currentConversation()
    const first = state.messages[0]
    if (!conversation || !first || !state.hasMore || state.loadingMessages) return
    const revision = accountRevision
    const messageRevision = conversationRevision
    publish({ loadingMessages: true })
    try { await readHistoryMessages(conversation, revision, messageRevision, { before: first.id, append: true }) }
    catch (error) { if (currentMessage(revision, messageRevision)) fail(error) }
    finally { if (currentMessage(revision, messageRevision)) publish({ loadingMessages: false }) }
  }

  async function cancelPending(reason = '用户取消') {
    const pending = [...activeOperations.entries()]
    await Promise.allSettled(pending.map(async ([id, control]) => {
      control.cancelled = true
      await Promise.allSettled([
        api.cancelOperation(id, { reason }),
        bridge.cancelBossChatOperation({ clientOperationId: id, execution: control.execution })
      ])
    }))
  }

  async function sendMessage(text) {
    const account = currentAccount()
    const conversation = currentConversation()
    const live = liveConversations.get(String(conversation?.platform_conversation_id))
    if (!text?.trim() || state.sending || !visible || state.connection !== 'ready' || !state.canReply || !account || !conversation || !live) return false
    const revision = accountRevision
    const messageRevision = conversationRevision
    const id = globalThis.crypto.randomUUID()
    const control = { execution: bridgeExecution(), cancelled: false }
    activeOperations.set(id, control)
    publish({ sending: true, error: '' })
    try {
      await checkExecution(account)
      const created = await api.createOperation(conversation.id, { client_operation_id: id, ...executionPayload(), text: text.trim() }, options())
      if (control.cancelled || !visible || !currentMessage(revision, messageRevision)) { await api.cancelOperation(id, { reason: '页面或会话已经变化' }); return false }
      publish({ operations: [...state.operations.filter((item) => item.client_operation_id !== id), created.operation] })
      await bridge.replyBossMessage({ accountId: account.platform_account_id, peerId: conversation.peer_id, securityId: live.security_id, text: created.operation.text, clientOperationId: id, execution: control.execution })
      if (current(revision)) {
        await readOperations(account)
        if (currentMessage(revision, messageRevision)) await readSourceMessages(conversation, revision, messageRevision)
      }
      return true
    } catch (error) {
      await api.cancelOperation(id, { reason: error?.message || '等待超时，停止尚未提交的操作' }).catch(() => {})
      await bridge.cancelBossChatOperation({ clientOperationId: id, execution: control.execution }).catch(() => {})
      if (current(revision)) { fail(error, account); await readOperations(account).catch(() => {}) }
      return false
    } finally {
      activeOperations.delete(id)
      if (current(revision)) publish({ sending: false })
    }
  }

  async function cancelOperation(id) {
    if (activeOperations.has(id)) activeOperations.get(id).cancelled = true
    await Promise.allSettled([api.cancelOperation(id, { reason: '用户取消' }), bridge.cancelBossChatOperation({ clientOperationId: id, execution: bridgeExecution() })])
    await readOperations()
  }

  async function start() {
    publish()
    try {
      const result = await api.accounts(options())
      if (disposed) return
      const accounts = (result.accounts || []).filter((item) => item.platform === 'boss')
      publish({ accounts })
      await selectAccount(accounts[0]?.id || '', { synchronize: false })
      if (!disposed && state.connection !== 'service_unavailable') await refresh({ manual: true })
    } catch (error) { fail(error) }
    finally { publish({ loading: false }) }
  }

  function setVisible(value) {
    const nextVisible = Boolean(value)
    if (visible !== nextVisible) {
      visible = nextVisible
      visibilityRevision += 1
      // 不取消历史查询；只使先前源读取链失效，前台恢复时重新检查身份。
      refreshPending = null
      publish({ syncing: false })
    }
    prune()
    if (!visible) { void cancelPending('页面切到后台'); return Promise.resolve() }
    if (state.connection === 'ready') return refresh()
    return Promise.resolve()
  }

  function dispose() {
    void cancelPending('离开招聘沟通页面')
    void releaseExecution()
    disposed = true
    accountRevision += 1
    conversationRevision += 1
    lifecycle.abort()
    liveConversations.clear()
    completedSource.clear()
    state = { ...state, messages: [], conversations: [], accounts: [] }
  }

  return { start, refresh, selectAccount, selectConversation, loadOlder, prune, setVisible, dispose, sendMessage, cancelOperation, takeover: () => refresh({ manual: true, takeover: true }) }
}
