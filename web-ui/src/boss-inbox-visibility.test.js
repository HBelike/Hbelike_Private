import assert from 'node:assert/strict'
import test from 'node:test'
import { createExternalChatInbox, ExternalChatError } from './external-chat-client.js'

const NOW = Date.parse('2026-11-09T08:00:00Z')
const account = { id: 'account-uuid', platform: 'boss', platform_account_id: '100' }
const conversation = { id: 'conversation-uuid', account_id: account.id, platform_conversation_id: '200', peer_id: '200', peer_name: '招聘者', security_id: 'local-only' }
const message = { id: 'message-uuid', platform_message_id: 'saved-message', platform_conversation_id: '200', sender_id: '200', direction: 'incoming', message_type: 'text', text: '已保存的 HR 回复', sent_at: new Date(NOW - 1000).toISOString() }
function deferred() {
  let resolve, reject
  const promise = new Promise((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}
function setup({ api: apiOverrides = {}, bridge: bridgeOverrides = {} } = {}) {
  let state
  const calls = []
  const execution = { executor_id: 'test-executor', revision: 1, paused: false }
  const api = {
    accounts: async () => ({ accounts: [account] }),
    conversations: async () => ({ conversations: [conversation] }),
    messages: async () => { calls.push('history'); return { messages: [message], has_more: false } },
    sync: async () => ({ account }),
    claimExecution: async () => ({ execution }),
    checkExecution: async () => ({ execution }),
    releaseExecution: async () => ({}),
    pauseAccount: async () => ({}),
    operations: async () => ({ operations: [] }),
    ...apiOverrides
  }
  const bridge = {
    ping: async () => ({ connected: true, capabilities: ['boss_chat_read', 'boss_chat_execute_v1'] }),
    getBossChatState: async () => { calls.push('state'); return { account } },
    listBossConversations: async () => { calls.push('list'); return { account, conversations: [conversation], has_more: false } },
    getBossMessages: async () => { calls.push('messages'); return { account, conversation, messages: [], has_more: false } },
    ...bridgeOverrides
  }
  const inbox = createExternalChatInbox({ api, bridge, now: () => NOW, executorId: 'test-executor', onChange: (value) => { state = value } })
  return { inbox, bridge, calls, get state() { return state } }
}

test('隐藏期间释放旧身份响应，不启动新的源列表请求', async (t) => {
  const context = setup()
  t.after(() => context.inbox.dispose())
  await context.inbox.start()
  const pending = deferred()
  context.bridge.getBossChatState = () => pending.promise
  const lists = context.calls.filter((item) => item === 'list').length
  const refresh = context.inbox.refresh()
  await context.inbox.setVisible(false)
  pending.resolve({ account })
  await refresh
  assert.equal(context.calls.filter((item) => item === 'list').length, lists)
  assert.equal(context.state.syncing, false)
})

test('隐藏后回前台启动新身份检查，旧刷新不能继续或清除新同步状态', async (t) => {
  const context = setup()
  t.after(() => context.inbox.dispose())
  await context.inbox.start()
  const oldState = deferred()
  const freshState = deferred()
  let probes = 0
  context.bridge.getBossChatState = () => (++probes === 1 ? oldState.promise : freshState.promise)
  const oldRefresh = context.inbox.refresh()
  await context.inbox.setVisible(false)
  const resumed = context.inbox.setVisible(true)
  assert.equal(probes, 2)
  oldState.resolve({ account: { ...account, platform_account_id: 'old-account' } })
  await oldRefresh
  assert.equal(context.state.syncing, true)
  assert.equal(context.state.pauseReason, '')
  freshState.resolve({ account })
  await resumed
  assert.equal(context.state.connection, 'ready')
  assert.equal(context.state.syncing, false)
})

test('隐藏前旧刷新迟到的超时错误不使前台新刷新离线', async (t) => {
  const context = setup()
  t.after(() => context.inbox.dispose())
  await context.inbox.start()
  const oldState = deferred()
  let probes = 0
  context.bridge.getBossChatState = () => ++probes === 1 ? oldState.promise : Promise.resolve({ account })
  const oldRefresh = context.inbox.refresh()
  await context.inbox.setVisible(false)
  await context.inbox.setVisible(true)
  oldState.reject(new ExternalChatError('request_timeout', '旧读取已超时'))
  await oldRefresh
  assert.equal(context.state.connection, 'ready')
  assert.equal(context.state.error, '')
})

test('隐藏时仍可查询平台历史，不读取源正文', async (t) => {
  const context = setup()
  t.after(() => context.inbox.dispose())
  await context.inbox.start()
  await context.inbox.setVisible(false)
  await context.inbox.selectConversation(conversation.id)
  assert.equal(context.state.messages[0].text, message.text)
  assert.equal(context.calls.includes('messages'), false)
})

test('隐藏前的正文页迟到，不继续旧分页或覆盖前台读取', async (t) => {
  const context = setup()
  t.after(() => context.inbox.dispose())
  await context.inbox.start()
  const oldPage = deferred()
  const entered = deferred()
  let pages = 0
  context.bridge.getBossMessages = async () => {
    pages += 1
    if (pages === 1) { entered.resolve(); return oldPage.promise }
    return { account, conversation, messages: [], has_more: false }
  }
  const selected = context.inbox.selectConversation(conversation.id)
  await entered.promise
  await context.inbox.setVisible(false)
  await context.inbox.setVisible(true)
  oldPage.resolve({ account: { ...account, platform_account_id: 'old-account' }, conversation, messages: [{ ...message, platform_message_id: 'old-page' }], has_more: true })
  await selected
  assert.equal(pages, 2)
  assert.equal(context.state.connection, 'ready')
  assert.equal(context.state.syncIncomplete, '')
})

test('隐藏前执行资格检查的迟到结果不能覆盖新资格', async (t) => {
  const oldCheck = deferred()
  const entered = deferred()
  let checks = 0
  let deferredCheck = false
  const freshExecution = { executor_id: 'test-executor', revision: 7, paused: false }
  const context = setup({ api: { checkExecution: async () => {
    checks += 1
    if (deferredCheck) { deferredCheck = false; entered.resolve(); return oldCheck.promise }
    return { execution: freshExecution }
  } } })
  t.after(() => context.inbox.dispose())
  await context.inbox.start()
  deferredCheck = true
  const selected = context.inbox.selectConversation(conversation.id)
  await entered.promise
  await context.inbox.setVisible(false)
  await context.inbox.setVisible(true)
  oldCheck.resolve({ execution: { ...freshExecution, revision: 1 } })
  await selected
  assert.equal(context.state.execution.revision, 7)
  assert.ok(checks >= 3)
})

test('旧可见周期正文的局部超时被丢弃，真实账号限流仍触发暂停', async (t) => {
  for (const code of ['request_timeout', 'rate_limited']) {
    const context = setup()
    t.after(() => context.inbox.dispose())
    await context.inbox.start()
    const oldPage = deferred()
    const entered = deferred()
    let reads = 0
    context.bridge.getBossMessages = async () => {
      if (++reads === 1) { entered.resolve(); return oldPage.promise }
      return { account, conversation, messages: [], has_more: false }
    }
    const selected = context.inbox.selectConversation(conversation.id)
    await entered.promise
    await context.inbox.setVisible(false)
    await context.inbox.setVisible(true)
    oldPage.reject(new ExternalChatError(code, '旧源读取返回错误'))
    await selected
    assert.equal(context.state.connection, code === 'rate_limited' ? 'paused' : 'ready')
    assert.equal(context.state.error, '')
  }
})
