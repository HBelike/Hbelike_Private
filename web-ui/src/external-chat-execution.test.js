import test from 'node:test'
import assert from 'node:assert/strict'
import { createExternalChatClient, createExternalChatInbox, ExternalChatError } from './external-chat-client.js'

const NOW = Date.parse('2026-11-09T08:00:00Z')
const ACCOUNT = { id: 'cloud-account', platform: 'boss', platform_account_id: '9007199254740993123', display_name: '本地测试账号' }
const SOURCE_ACCOUNT = { platform: ACCOUNT.platform, platform_account_id: ACCOUNT.platform_account_id, display_name: ACCOUNT.display_name }
const clone = (value) => structuredClone(value)
const flush = () => new Promise((resolve) => setImmediate(resolve))

function deferred() {
  let resolve
  let reject
  const promise = new Promise((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}

function conversation(index = 1) {
  return { id: `cloud-conversation-${index}`, account_id: ACCOUNT.id, platform_conversation_id: `source-conversation-${index}`, peer_id: `peer-${index}`, peer_name: `测试招聘者${index}`, last_message_at: new Date(NOW - index * 1000).toISOString(), last_message_preview: '测试摘要', source_url: 'https://www.zhipin.com/web/geek/chat' }
}

function message(index, conversationIndex = 1) {
  return { platform_conversation_id: `source-conversation-${conversationIndex}`, platform_message_id: `source-message-${index}`, sender_id: `peer-${conversationIndex}`, direction: 'incoming', message_type: 'text', text: `测试消息${index}`, sent_at: new Date(NOW - index * 1000).toISOString() }
}

// 这里只模拟平台存储和执行资格，所有源平台动作都通过独立 bridge 替身记录。
function memoryServer({ initialConversations = [conversation(), conversation(2)], paused = false } = {}) {
  const conversations = new Map(initialConversations.map((item) => [item.platform_conversation_id, clone(item)]))
  const messages = new Map()
  const operations = new Map()
  const calls = []
  let owner = ''
  let revision = 0
  let isPaused = paused
  const execution = () => ({ executor_id: owner, revision, paused: isPaused, pause_reason: isPaused ? '请先处理平台验证' : '' })
  function requireExecution(payload) {
    if (payload.executor_id !== owner || payload.revision !== revision) throw new ExternalChatError('execution_lost', '执行资格已在其他页面接管')
  }
  const api = {
    accounts: async () => ({ accounts: [clone(ACCOUNT)] }),
    conversations: async () => ({ conversations: clone([...conversations.values()]) }),
    messages: async (id, { before = '' } = {}) => {
      const selected = [...conversations.values()].find((item) => item.id === id)
      const rows = [...messages.values()].filter((item) => item.platform_conversation_id === selected?.platform_conversation_id).sort((a, b) => a.sent_at.localeCompare(b.sent_at) || a.id.localeCompare(b.id))
      const end = before ? rows.findIndex((item) => item.id === before) : rows.length
      const start = Math.max(0, end - 100)
      return { messages: clone(rows.slice(start, end)), has_more: start > 0 }
    },
    sync: async (payload) => {
      calls.push({ kind: 'sync', payload: clone(payload) })
      for (const item of payload.conversations || []) {
        const saved = conversations.get(item.platform_conversation_id)
        const { security_id: ignored, ...persisted } = item
        conversations.set(item.platform_conversation_id, { ...persisted, id: saved?.id || `cloud-${item.platform_conversation_id}`, account_id: ACCOUNT.id })
      }
      for (const item of payload.messages || []) messages.set(item.platform_message_id, { ...clone(item), id: `cloud-${item.platform_message_id}` })
      return { account: clone(ACCOUNT) }
    },
    claimExecution: async (_id, payload) => {
      calls.push({ kind: 'claim', payload: clone(payload) })
      if (owner && owner !== payload.executor_id && !payload.takeover) throw new ExternalChatError('execution_busy', '另一页面正在同步，可在此接管')
      if (owner !== payload.executor_id || payload.takeover) revision += 1
      owner = payload.executor_id
      return { execution: execution() }
    },
    checkExecution: async (_id, payload) => {
      requireExecution(payload)
      if (isPaused) throw new ExternalChatError('account_paused', '请先处理平台验证')
      return { execution: execution() }
    },
    releaseExecution: async (_id, payload) => {
      calls.push({ kind: 'release', payload: clone(payload) })
      if (payload.executor_id === owner && payload.revision === revision) owner = ''
      return {}
    },
    pauseAccount: async () => { isPaused = true; return { execution: execution() } },
    resumeAccount: async (_id, payload) => {
      requireExecution(payload)
      calls.push({ kind: 'resume' })
      isPaused = false
      return { execution: execution() }
    },
    operations: async () => ({ operations: clone([...operations.values()]) }),
    createOperation: async (id, payload) => {
      requireExecution(payload)
      calls.push({ kind: 'create', payload: clone(payload) })
      const operation = { ...clone(payload), conversation_id: id, status: 'pending' }
      operations.set(payload.client_operation_id, operation)
      return { operation: clone(operation) }
    },
    cancelOperation: async (id, payload) => {
      calls.push({ kind: 'cancel', id, payload: clone(payload) })
      const operation = operations.get(id)
      if (operation?.status === 'pending') operation.status = 'cancelled'
      return { operation: operation ? clone(operation) : null }
    }
  }
  return { api, calls, conversations, messages, operations }
}

function mount(server, { executorId = 'page-one', api: apiOverrides = {}, bridge: bridgeOverrides = {}, onChange = () => {} } = {}) {
  let state
  const calls = []
  const bridge = {
    ping: async () => ({ connected: true, capabilities: ['boss_chat_read', 'boss_chat_execute_v1', 'boss_chat_reply'] }),
    getBossChatState: async () => ({ account: clone(SOURCE_ACCOUNT) }),
    listBossConversations: async ({ page }) => {
      calls.push({ kind: 'list', page })
      return { account: clone(SOURCE_ACCOUNT), conversations: [...server.conversations.values()].map((item) => ({ ...clone(item), security_id: 'browser-only' })), has_more: false }
    },
    getBossMessages: async ({ peerId, page }) => {
      calls.push({ kind: 'messages', peerId, page })
      const selected = [...server.conversations.values()].find((item) => item.peer_id === peerId)
      return { account: clone(SOURCE_ACCOUNT), conversation: { platform_conversation_id: selected.platform_conversation_id, peer_id: peerId }, messages: [], has_more: false }
    },
    resumeBossChat: async () => { calls.push({ kind: 'resume' }); return {} },
    cancelBossChatOperation: async ({ clientOperationId }) => { calls.push({ kind: 'cancel', id: clientOperationId }); return {} },
    replyBossMessage: async (payload) => {
      calls.push({ kind: 'reply', payload: clone(payload) })
      server.operations.get(payload.clientOperationId).status = 'accepted'
      return { status: 'accepted' }
    },
    ...bridgeOverrides
  }
  const inbox = createExternalChatInbox({ api: { ...server.api, ...apiOverrides }, bridge, executorId, now: () => NOW, onChange: (next) => { state = next; onChange(next) } })
  return { inbox, calls, get state() { return state } }
}

function sourceMessages(rows, hasMore) {
  return { account: clone(SOURCE_ACCOUNT), conversation: { platform_conversation_id: 'source-conversation-1', peer_id: 'peer-1' }, messages: rows, has_more: hasMore }
}

test('源列表和正文各120条时遍历三页并完整保存，历史可继续翻页查看', async (t) => {
  const server = memoryServer({ initialConversations: [conversation()] })
  const contacts = Array.from({ length: 120 }, (_, index) => ({ ...conversation(index + 1), security_id: 'browser-only' }))
  const rows = Array.from({ length: 120 }, (_, index) => message(index + 1))
  const listPages = []
  const messagePages = []
  const page = mount(server, { bridge: {
    listBossConversations: async ({ page: number }) => {
      listPages.push(number)
      return { account: clone(SOURCE_ACCOUNT), conversations: contacts.slice((number - 1) * 50, number * 50), has_more: number < 3 }
    },
    getBossMessages: async ({ page: number, count }) => {
      assert.equal(count, 50)
      messagePages.push(number)
      return sourceMessages(rows.slice((number - 1) * 50, number * 50), number < 3)
    }
  } })
  t.after(() => page.inbox.dispose())
  await page.inbox.start()
  assert.deepEqual(listPages, [1, 2, 3])
  assert.equal(page.state.conversations.length, 120)
  await page.inbox.selectConversation('cloud-conversation-1')
  assert.deepEqual(messagePages, [1, 2, 3])
  assert.equal(server.messages.size, 120)
  assert.equal(page.state.syncIncomplete, '')
  assert.equal(page.state.hasMore, true)
  await page.inbox.loadOlder()
  assert.equal(page.state.messages.length, 120)
  assert.equal(page.state.hasMore, false)
})

test('源列表结束信号未知时保留已取得联系人并显示缺口，不猜测已经同步完整', async (t) => {
  const server = memoryServer()
  let reads = 0
  const page = mount(server, { bridge: { listBossConversations: async () => {
    reads += 1
    return { account: clone(SOURCE_ACCOUNT), conversations: [{ ...conversation(), security_id: 'browser-only' }], has_more: null }
  } } })
  t.after(() => page.inbox.dispose())
  await page.inbox.start()
  assert.equal(reads, 1)
  assert.equal(page.state.listHasMore, true)
  assert.match(page.state.syncIncomplete, /结束状态未知/)
  assert.equal(server.calls.filter((item) => item.kind === 'sync').length, 1)
})

test('正文结束信号未知或返回重复页时不无限翻页，已取得记录仍保存且提示不完整', async (t) => {
  for (const mode of ['unknown', 'repeated']) {
    await t.test(mode, async (subtest) => {
      const server = memoryServer()
      const reads = []
      const page = mount(server, { bridge: { getBossMessages: async ({ page: number }) => {
        reads.push(number)
        return sourceMessages([message(1), message(2)], mode === 'unknown' ? null : true)
      } } })
      subtest.after(() => page.inbox.dispose())
      await page.inbox.start()
      await page.inbox.selectConversation('cloud-conversation-1')
      assert.deepEqual(reads, mode === 'unknown' ? [1] : [1, 2])
      assert.equal(server.messages.size, 2)
      assert.equal(page.state.messages.length, 2)
      assert.match(page.state.syncIncomplete, mode === 'unknown' ? /未提供可靠.*结束信号/ : /重复页/)
    })
  }
})

test('重复联系人页显示列表缺口，不把重复页当作新的同步进度', async (t) => {
  const server = memoryServer()
  const reads = []
  const page = mount(server, { bridge: { listBossConversations: async ({ page: number }) => {
    reads.push(number)
    return { account: clone(SOURCE_ACCOUNT), conversations: [{ ...conversation(), security_id: 'browser-only' }], has_more: true }
  } } })
  t.after(() => page.inbox.dispose())
  await page.inbox.start()
  assert.deepEqual(reads, [1, 2])
  assert.match(page.state.syncIncomplete, /重复页/)
  assert.equal(page.state.listHasMore, true)
  assert.equal(server.calls.filter((item) => item.kind === 'sync').length, 1)
})

test('两个页面共享账号时第二页只看历史，只有显式接管才能执行且旧页失去资格', async (t) => {
  const server = memoryServer()
  const first = mount(server)
  const second = mount(server, { executorId: 'page-two' })
  t.after(() => { first.inbox.dispose(); second.inbox.dispose() })
  await first.inbox.start()
  await second.inbox.start()
  assert.equal(first.state.connection, 'ready')
  assert.equal(second.state.connection, 'history_only')
  assert.equal(second.state.conversations.length, 2)
  assert.equal(second.calls.some((item) => item.kind === 'list'), false)
  await second.inbox.selectConversation('cloud-conversation-1')
  await second.inbox.refresh()
  assert.equal(second.calls.some((item) => ['list', 'messages', 'reply'].includes(item.kind)), false)
  assert.equal(server.calls.some((item) => item.kind === 'claim' && item.payload.takeover), false)
  await second.inbox.takeover()
  assert.equal(second.state.connection, 'ready')
  assert.ok(second.calls.some((item) => item.kind === 'list'))
  await first.inbox.refresh()
  assert.equal(first.state.connection, 'history_only')
  assert.equal(first.calls.filter((item) => item.kind === 'list').length, 1)
})

test('后端共享暂停在页面重载后保留，自动刷新不能恢复，手动继续才解除', async (t) => {
  const server = memoryServer({ paused: true })
  const first = mount(server)
  await first.inbox.start()
  assert.equal(first.state.connection, 'paused')
  first.inbox.dispose()
  await flush()
  const reloaded = mount(server, { executorId: 'page-reloaded' })
  t.after(() => reloaded.inbox.dispose())
  await reloaded.inbox.start()
  await reloaded.inbox.refresh()
  assert.equal(reloaded.state.connection, 'paused')
  assert.equal(server.calls.some((item) => item.kind === 'resume'), false)
  assert.equal(reloaded.calls.some((item) => ['list', 'resume'].includes(item.kind)), false)
  await reloaded.inbox.refresh({ manual: true })
  assert.equal(reloaded.state.connection, 'ready')
  assert.equal(server.calls.filter((item) => item.kind === 'resume').length, 1)
  assert.equal(reloaded.calls.filter((item) => item.kind === 'resume').length, 1)
})

test('创建发件记录晚到时，页面隐藏或切换会话均取消记录且不会调用回复', async (t) => {
  for (const change of ['hide', 'switch']) {
    await t.test(change, async (subtest) => {
      const server = memoryServer()
      const entered = deferred()
      const finish = deferred()
      const page = mount(server, { api: { createOperation: async (...args) => {
        entered.resolve()
        await finish.promise
        return server.api.createOperation(...args)
      } } })
      subtest.after(() => page.inbox.dispose())
      await page.inbox.start()
      await page.inbox.selectConversation('cloud-conversation-1')
      const sending = page.inbox.sendMessage('已逐条确认的测试回复')
      await entered.promise
      if (change === 'hide') await page.inbox.setVisible(false)
      else await page.inbox.selectConversation('cloud-conversation-2')
      finish.resolve()
      assert.equal(await sending, false)
      assert.equal(page.calls.some((item) => item.kind === 'reply'), false)
      assert.equal(server.operations.size, 1)
      assert.equal([...server.operations.values()][0].status, 'cancelled')
      assert.ok(page.calls.some((item) => item.kind === 'cancel'))
    })
  }
})

test('未知发送结果恢复连接后不重放，用户再次确认同正文创建新操作ID', async (t) => {
  const server = memoryServer()
  const replies = []
  const page = mount(server, { bridge: { replyBossMessage: async (payload) => {
    replies.push(clone(payload))
    const operation = server.operations.get(payload.clientOperationId)
    if (replies.length === 1) {
      operation.status = 'unknown'
      throw new ExternalChatError('request_timeout', '源平台发送结果待核对')
    }
    operation.status = 'accepted'
    return { status: 'accepted' }
  } } })
  t.after(() => page.inbox.dispose())
  await page.inbox.start()
  await page.inbox.selectConversation('cloud-conversation-1')
  assert.equal(await page.inbox.sendMessage('好的，谢谢'), false)
  assert.equal(page.state.operations[0].status, 'unknown')
  await page.inbox.refresh({ manual: true })
  assert.equal(replies.length, 1)
  assert.equal(await page.inbox.sendMessage('好的，谢谢'), true)
  assert.equal(replies.length, 2)
  assert.notEqual(replies[0].clientOperationId, replies[1].clientOperationId)
  assert.equal(replies[0].text, replies[1].text)
  assert.equal(server.operations.get(replies[0].clientOperationId).status, 'unknown')
  assert.equal(server.operations.get(replies[1].clientOperationId).status, 'accepted')
  await page.inbox.refresh()
  assert.equal(replies.length, 2)
})

test('卸载页面时清理接口拒绝和晚到创建都被收束，不再发布或执行回复', async () => {
  const server = memoryServer()
  const entered = deferred()
  const finish = deferred()
  let changes = 0
  const unhandled = []
  const capture = (reason) => unhandled.push(reason)
  process.on('unhandledRejection', capture)
  const page = mount(server, { onChange: () => { changes += 1 }, api: {
    createOperation: async (...args) => { entered.resolve(); await finish.promise; return server.api.createOperation(...args) },
    cancelOperation: async () => { throw new Error('模拟离线取消失败') },
    releaseExecution: async () => { throw new Error('模拟离线释放失败') }
  }, bridge: { cancelBossChatOperation: async () => { throw new Error('模拟扩展已断开') } } })
  try {
    await page.inbox.start()
    await page.inbox.selectConversation('cloud-conversation-1')
    const sending = page.inbox.sendMessage('待创建的测试回复')
    await entered.promise
    page.inbox.dispose()
    const count = changes
    finish.resolve()
    assert.equal(await sending, false)
    await flush()
    await flush()
    assert.deepEqual(unhandled, [])
    assert.equal(changes, count)
    assert.equal(page.calls.some((item) => item.kind === 'reply'), false)
  } finally {
    process.off('unhandledRejection', capture)
    page.inbox.dispose()
  }
})

test('执行资格和发件API正确传递身份、正文、取消原因及结构化冲突', async () => {
  const requests = []
  let conflict = false
  const api = createExternalChatClient(async (url, options) => {
    requests.push({ url, options })
    return { ok: !conflict, json: async () => conflict ? { detail: { code: 'execution_busy', message: '其他页面持有资格' } } : { operation: {}, execution: {} } }
  })
  const signal = new AbortController().signal
  await api.claimExecution('account/uuid', { executor_id: 'page-two', takeover: true }, { signal })
  await api.createOperation('conversation/uuid', { client_operation_id: 'new-id', executor_id: 'page-two', revision: 4, text: '好的，谢谢' }, { signal })
  await api.cancelOperation('operation/uuid', { reason: '切换会话' })
  assert.equal(requests[0].url, '/api/career/external-chat/accounts/account%2Fuuid/execution')
  assert.deepEqual(JSON.parse(requests[0].options.body), { executor_id: 'page-two', takeover: true })
  assert.equal(requests[1].url, '/api/career/external-chat/conversations/conversation%2Fuuid/operations')
  assert.deepEqual(JSON.parse(requests[1].options.body), { client_operation_id: 'new-id', executor_id: 'page-two', revision: 4, text: '好的，谢谢' })
  assert.equal(requests[1].options.signal, signal)
  assert.equal(requests[1].options.credentials, 'same-origin')
  assert.equal(requests[2].url, '/api/career/external-chat/operations/operation%2Fuuid/cancel')
  assert.deepEqual(JSON.parse(requests[2].options.body), { reason: '切换会话' })
  conflict = true
  await assert.rejects(api.claimExecution(ACCOUNT.id, { executor_id: 'page-three' }), { code: 'execution_busy', message: '其他页面持有资格' })
})
