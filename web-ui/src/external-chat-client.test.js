import test from 'node:test'
import assert from 'node:assert/strict'
import { bossChatSourceUrl, createExternalChatClient, createExternalChatInbox, ExternalChatError, externalChatSyncPayload, recentExternalChatMessages } from './external-chat-client.js'
import { normalizeAppRoute } from './navigation-access.js'

const NOW = Date.parse('2026-11-09T08:00:00Z')
const account = { id: 'cloud-account-uuid', platform: 'boss', platform_account_id: '9007199254740993123', display_name: '我的 BOSS' }
const sourceAccount = { platform: 'boss', platform_account_id: account.platform_account_id, display_name: account.display_name }
const conversation = (suffix = '1') => ({ id: `cloud-conversation-${suffix}`, account_id: account.id, platform_conversation_id: `boss-conversation-${suffix}`, peer_id: `peer-${suffix}`, peer_name: `招聘者${suffix}`, last_message_at: new Date(NOW - 1000).toISOString(), last_message_preview: '已保存的回复', unread_count: null })
const message = (id = '1', conversationId = 'boss-conversation-1') => ({ id: `cloud-message-${id}`, platform_message_id: id, platform_conversation_id: conversationId, direction: 'incoming', message_type: 'text', text: `消息${id}`, sent_at: new Date(NOW - 1000).toISOString() })
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done }); return { promise, resolve } }

function setup({ api: apiOverrides = {}, bridge: bridgeOverrides = {}, initialAccounts = [account], onChange = () => {} } = {}) {
  const calls = []
  let state
  const records = [message()]
  const conversations = [conversation(), conversation('2')]
  const held = { executor_id: 'test-executor', revision: 1, paused: false }
  const api = {
    claimExecution: async () => ({ execution: held }),
    checkExecution: async () => ({ execution: held }),
    releaseExecution: async () => ({}),
    pauseAccount: async () => ({}),
    resumeAccount: async () => ({ execution: held }),
    operations: async () => ({ operations: [] }),
    cancelOperation: async () => ({}),
    accounts: async () => { calls.push('history:accounts'); return { accounts: initialAccounts } },
    conversations: async () => { calls.push('history:conversations'); return { conversations } },
    messages: async (id, options) => { calls.push(`history:messages:${id}`); return { messages: records.filter((item) => item.platform_conversation_id === conversations.find((item) => item.id === id)?.platform_conversation_id), has_more: false } },
    sync: async (payload) => {
      calls.push(payload.messages.length ? 'sync:messages' : 'sync:list')
      for (const item of payload.messages) if (!records.some((existing) => existing.platform_message_id === item.platform_message_id)) records.push({ ...item, id: `cloud-message-${item.platform_message_id}` })
      return { account }
    },
    ...apiOverrides
  }
  const bridge = {
    ping: async () => { calls.push('bridge:ping'); return { connected: true, capabilities: ['boss_chat_read', 'boss_chat_execute_v1', 'boss_chat_reply'] } },
    resumeBossChat: async () => ({}),
    cancelBossChatOperation: async () => ({}),
    getBossChatState: async () => ({ account: sourceAccount }),
    listBossConversations: async () => ({ account: sourceAccount, conversations: conversations.map((item) => ({ ...item, security_id: 'browser-only-secret' })), has_more: false }),
    getBossMessages: async ({ accountId, peerId }) => { calls.push(`source:messages:${peerId}`); assert.equal(accountId, account.platform_account_id); return { account: sourceAccount, conversation: { platform_conversation_id: `boss-conversation-${peerId.slice(-1)}`, peer_id: peerId }, messages: [message(`new-${peerId}`, `boss-conversation-${peerId.slice(-1)}`)] } },
    ...bridgeOverrides
  }
  const inbox = createExternalChatInbox({ api, bridge, executorId: 'test-executor', now: () => NOW, onChange: (value) => { state = value; onChange(value) } })
  return { inbox, calls, records, get state() { return state } }
}

test('招聘沟通子路由保留自身路径', () => assert.equal(normalizeAppRoute('/career/messages'), '/career/messages'))

test('同步正文明确剔除动态标识和完整原始响应', () => {
  const payload = externalChatSyncPayload({ account: { ...account, cookie: 'never-upload' }, conversations: [{ ...conversation(), security_id: 'secret', raw: 'raw-response' }], messages: [{ ...message(), raw: 'private' }] })
  assert.deepEqual(payload.account, sourceAccount)
  assert.equal('security_id' in payload.conversations[0], false)
  assert.equal('account_id' in payload.conversations[0], false)
  assert.equal('raw' in payload.messages[0], false)
})

test('历史请求带同源凭证，翻页使用内部消息 UUID', async () => {
  const calls = []
  const api = createExternalChatClient(async (url, options) => { calls.push({ url, options }); return { ok: true, json: async () => ({ messages: [] }) } })
  await api.messages('cloud/conversation', { before: 'message-uuid' })
  assert.equal(calls[0].url, '/api/career/external-chat/conversations/cloud%2Fconversation/messages?limit=100&before=message-uuid')
  assert.equal(calls[0].options.credentials, 'same-origin')
  assert.equal(calls[0].options.cache, 'no-store')
})

test('30天边界按原始时间筛选，无效时间不冒充今天，同文不同ID保留', () => {
  const cutoff = NOW - 30 * 24 * 60 * 60 * 1000
  const records = [
    { ...message('boundary'), sent_at: new Date(cutoff).toISOString() },
    { ...message('expired'), sent_at: new Date(cutoff - 1).toISOString() },
    { ...message('invalid'), sent_at: 'unknown' },
    { ...message('a'), text: '相同正文' }, { ...message('b'), text: '相同正文' }, message('a')
  ]
  assert.deepEqual(recentExternalChatMessages(records, NOW).map((item) => item.platform_message_id), ['boundary', 'a', 'b'])
})

test('来源链接仅允许BOSS聊天路径，异常链接回到官方聊天入口', () => {
  assert.equal(bossChatSourceUrl('javascript:alert(1)'), 'https://www.zhipin.com/web/geek/chat')
  assert.equal(bossChatSourceUrl('https://www.zhipin.com.evil.test/web/geek/chat'), 'https://www.zhipin.com/web/geek/chat')
  assert.equal(bossChatSourceUrl('https://www.zhipin.com/web/geek/chat?id=peer'), 'https://www.zhipin.com/web/geek/chat?id=peer')
})

test('进入先读取历史再检测扩展，仅为选中会话补正文', async () => {
  const context = setup()
  await context.inbox.start()
  assert.ok(context.calls.indexOf('history:conversations') < context.calls.indexOf('bridge:ping'))
  assert.equal(context.state.connection, 'ready')
  assert.equal(context.state.selectedConversationId, '')
  assert.equal(context.calls.some((item) => item.startsWith('source:')), false)
  await context.inbox.refresh()
  assert.equal(context.calls.some((item) => item.startsWith('source:')), false)
  await context.inbox.selectConversation('cloud-conversation-1')
  assert.deepEqual(context.calls.filter((item) => item.startsWith('source:')), ['source:messages:peer-1'])
  assert.equal(context.state.messages.length, 2)
  context.inbox.dispose()
})

test('未连接与旧扩展保留历史，不执行任何会话同步', async () => {
  for (const ping of [async () => { throw Object.assign(new Error('助手未连接'), { code: 'extension_unavailable' }) }, async () => ({ connected: true, capabilities: [] })]) {
    const context = setup({ bridge: { ping } })
    await context.inbox.start()
    await context.inbox.selectConversation('cloud-conversation-1')
    assert.equal(context.state.messages[0].text, '消息1')
    assert.ok(['offline', 'needs_update'].includes(context.state.connection))
    assert.equal(context.calls.some((item) => item.startsWith('sync:')), false)
    context.inbox.dispose()
  }
})

test('首次使用无云端账号时按浏览器稳定UID建立历史，不自动读取正文', async () => {
  let created = false
  const context = setup({ api: {
    accounts: async () => ({ accounts: created ? [account] : [] }),
    sync: async () => { created = true; return { account } }
  } })
  await context.inbox.start()
  assert.equal(context.state.selectedAccountId, account.id)
  assert.equal(context.state.browserAccount.platform_account_id, account.platform_account_id)
  assert.equal(context.state.selectedConversationId, '')
  assert.equal(context.state.connection, 'ready')
  assert.equal(context.calls.some((item) => item.startsWith('source:messages')), false)
  context.inbox.dispose()
})

test('浏览器 UID 与历史账号不符时暂停，不能按显示名或云端UUID匹配', async () => {
  const context = setup({ bridge: { getBossChatState: async () => ({ account: { ...sourceAccount, platform_account_id: account.id } }) } })
  await context.inbox.start()
  assert.equal(context.state.connection, 'paused')
  assert.match(context.state.pauseReason, /账号不一致/)
  assert.equal(context.calls.includes('sync:list'), false)
  await context.inbox.selectConversation('cloud-conversation-1')
  assert.equal(context.state.messages[0].text, '消息1')
  context.inbox.dispose()
})

test('列表读取期间账号变化，不将另一账号会话同步到平台', async () => {
  const context = setup({ bridge: { listBossConversations: async () => ({ account: { ...sourceAccount, platform_account_id: 'different' }, conversations: [conversation()] }) } })
  await context.inbox.start()
  assert.equal(context.state.connection, 'paused')
  assert.equal(context.calls.includes('sync:list'), false)
  context.inbox.dispose()
})

test('正文会话或账号不符时不上传消息，暂停后不会自动轮询，手动恢复不重放旧结果', async () => {
  let blocked = true
  const context = setup({ bridge: { getBossMessages: async () => blocked
    ? { account: sourceAccount, conversation: { platform_conversation_id: 'other', peer_id: 'peer-1' }, messages: [message()] }
    : { account: sourceAccount, conversation: { platform_conversation_id: 'boss-conversation-1', peer_id: 'peer-1' }, messages: [message('restored')] }
  } })
  await context.inbox.start()
  await context.inbox.selectConversation('cloud-conversation-1')
  assert.equal(context.state.connection, 'paused')
  assert.equal(context.calls.includes('sync:messages'), false)
  const count = context.calls.length
  await context.inbox.refresh()
  assert.equal(context.calls.length, count)
  blocked = false
  await context.inbox.refresh({ manual: true })
  assert.equal(context.state.connection, 'ready')
  assert.equal(context.state.messages.at(-1).platform_message_id, 'restored')
  context.inbox.dispose()
})

test('HR新回复同步后通过历史GET显示，重复刷新不增加重复消息', async () => {
  let reply = false
  const context = setup({ bridge: { getBossMessages: async () => ({ account: sourceAccount, conversation: { platform_conversation_id: 'boss-conversation-1', peer_id: 'peer-1' }, messages: reply ? [message(), message('hr-new')] : [message()] }) } })
  await context.inbox.start()
  await context.inbox.selectConversation('cloud-conversation-1')
  assert.equal(context.state.messages.length, 1)
  reply = true
  await context.inbox.refresh()
  await context.inbox.refresh()
  assert.equal(context.state.messages.length, 2)
  assert.ok(context.state.messages.some((item) => item.platform_message_id === 'hr-new'))
  context.inbox.dispose()
})

test('切换会话后旧请求晚到，不上传旧正文或覆盖当前会话', async () => {
  const pending = deferred()
  const entered = deferred()
  const context = setup({ bridge: { getBossMessages: async ({ peerId }) => {
    if (peerId === 'peer-1') { entered.resolve(); return pending.promise }
    return { account: sourceAccount, conversation: { platform_conversation_id: 'boss-conversation-2', peer_id: 'peer-2' }, messages: [message('second', 'boss-conversation-2')] }
  } } })
  await context.inbox.start()
  const selection = context.inbox.selectConversation('cloud-conversation-1')
  await entered.promise
  await context.inbox.selectConversation('cloud-conversation-2')
  pending.resolve({ account: sourceAccount, conversation: { platform_conversation_id: 'boss-conversation-1', peer_id: 'peer-1' }, messages: [message('late')] })
  await selection
  assert.equal(context.state.selectedConversationId, 'cloud-conversation-2')
  assert.deepEqual(context.state.messages.map((item) => item.platform_message_id), ['second'])
  assert.equal(context.records.some((item) => item.platform_message_id === 'late'), false)
  context.inbox.dispose()
})

test('页面隐藏时不再读取源消息，卸载后迟到结果无上传与状态更新', async () => {
  const context = setup()
  await context.inbox.start()
  await context.inbox.setVisible(false)
  const count = context.calls.length
  await context.inbox.refresh({ manual: true })
  assert.equal(context.calls.length, count)
  context.inbox.dispose()
  await context.inbox.refresh({ manual: true })
  assert.equal(context.calls.length, count)

  const pending = deferred()
  const entered = deferred()
  let changes = 0
  const late = setup({ onChange: () => { changes += 1 }, bridge: { getBossMessages: async () => { entered.resolve(); return pending.promise } } })
  await late.inbox.start()
  const selection = late.inbox.selectConversation('cloud-conversation-1')
  await entered.promise
  late.inbox.dispose()
  const before = changes
  pending.resolve({ account: sourceAccount, conversation: { platform_conversation_id: 'boss-conversation-1', peer_id: 'peer-1' }, messages: [message('late')] })
  await selection
  assert.equal(changes, before)
  assert.equal(late.calls.includes('sync:messages'), false)
})

test('会话变化及stopBatch读取错误暂停，切账号不会自动打开第一会话', async () => {
  for (const error of [new ExternalChatError('conversation_changed', '会话已变化'), Object.assign(new Error('源页面结构发生变化'), { code: 'chat_schema_changed', stopBatch: true })]) {
    const context = setup({ bridge: { getBossMessages: async () => { throw error } } })
    await context.inbox.start()
    await context.inbox.selectConversation('cloud-conversation-1')
    assert.equal(context.state.connection, 'paused')
    const count = context.calls.length
    await context.inbox.refresh()
    assert.equal(context.calls.length, count)
    await context.inbox.selectAccount(account.id, { synchronize: false })
    assert.equal(context.state.selectedConversationId, '')
    assert.equal(context.state.messages.length, 0)
    context.inbox.dispose()
  }
})

test('同一源时间多页历史按内部UUID排序，翻页不漏条且到末页停止', async () => {
  const records = ['z', 'y', 'x', 'a', 'b', 'c'].map((platformId, index) => ({ ...message(platformId), id: `uuid-0${index + 1}` }))
  const cursors = []
  const context = setup({ bridge: { ping: async () => ({ connected: true, capabilities: [] }) }, api: { messages: async (_id, { before }) => {
    cursors.push(before)
    const end = before ? records.findIndex((item) => item.id === before) : records.length
    const start = Math.max(0, end - 2)
    return { messages: records.slice(start, end).reverse(), has_more: start > 0 }
  } } })
  await context.inbox.start()
  await context.inbox.selectConversation('cloud-conversation-1')
  await context.inbox.loadOlder()
  await context.inbox.loadOlder()
  await context.inbox.loadOlder()
  assert.deepEqual(cursors, ['', 'uuid-05', 'uuid-03'])
  assert.deepEqual(context.state.messages.map((item) => item.id), records.map((item) => item.id))
  assert.equal(context.state.hasMore, false)
  context.inbox.dispose()
})

test('切会话后旧请求的限流或stopBatch错误仍暂停同账号，且保留当前正文', async () => {
  for (const error of [new ExternalChatError('rate_limited', '沟通读取已限流'), Object.assign(new Error('需要人工处理'), { code: 'chat_schema_changed', stopBatch: true })]) {
    const pending = deferred()
    const entered = deferred()
    const context = setup({ bridge: { getBossMessages: async ({ peerId }) => {
      if (peerId === 'peer-1') { entered.resolve(); await pending.promise; throw error }
      return { account: sourceAccount, conversation: { platform_conversation_id: 'boss-conversation-2', peer_id: 'peer-2' }, messages: [message('second', 'boss-conversation-2')] }
    } } })
    await context.inbox.start()
    const first = context.inbox.selectConversation('cloud-conversation-1')
    await entered.promise
    await context.inbox.selectConversation('cloud-conversation-2')
    pending.resolve()
    await first
    assert.equal(context.state.connection, 'paused')
    assert.equal(context.state.selectedConversationId, 'cloud-conversation-2')
    assert.equal(context.state.messages[0].text, '消息second')
    const count = context.calls.length
    await context.inbox.refresh()
    assert.equal(context.calls.length, count)
    context.inbox.dispose()
  }
})

test('旧账号在途错误不污染切换后的新账号', async () => {
  const other = { ...account, id: 'other-cloud-account', platform_account_id: 'other-stable-uid' }
  const otherConversation = { ...conversation('2'), account_id: other.id }
  let browserAccount = sourceAccount
  const pending = deferred()
  const entered = deferred()
  const context = setup({ api: {
    accounts: async () => ({ accounts: [account, other] }),
    conversations: async (id) => ({ conversations: id === other.id ? [otherConversation] : [conversation()] }),
    sync: async ({ account: item }) => ({ account: item.platform_account_id === other.platform_account_id ? other : account })
  }, bridge: {
    getBossChatState: async () => ({ account: browserAccount }),
    listBossConversations: async () => ({ account: browserAccount, conversations: [{ ...(browserAccount === sourceAccount ? conversation() : otherConversation), security_id: 'browser-only' }] }),
    getBossMessages: async () => { entered.resolve(); await pending.promise; throw new ExternalChatError('rate_limited', '旧账号已限流') }
  } })
  await context.inbox.start()
  const first = context.inbox.selectConversation('cloud-conversation-1')
  await entered.promise
  browserAccount = other
  await context.inbox.selectAccount(other.id)
  pending.resolve()
  await first
  assert.equal(context.state.selectedAccountId, other.id)
  assert.equal(context.state.connection, 'ready')
  assert.equal(context.state.pauseReason, '')
  context.inbox.dispose()
})

test('在途列表刷新不能解除后来发生的账号暂停，只有手动继续可恢复', async () => {
  const pendingList = deferred()
  const entered = deferred()
  let listReads = 0
  let messageReads = 0
  const context = setup({ api: { conversations: async () => {
    listReads += 1
    if (listReads === 3) { entered.resolve(); await pendingList.promise }
    return { conversations: [conversation(), conversation('2')] }
  } }, bridge: { getBossMessages: async () => {
    messageReads += 1
    if (messageReads === 1) throw new ExternalChatError('rate_limited', '已限流，请手动继续')
    return { account: sourceAccount, conversation: { platform_conversation_id: 'boss-conversation-1', peer_id: 'peer-1' }, messages: [] }
  } } })
  await context.inbox.start()
  const refresh = context.inbox.refresh()
  await entered.promise
  await context.inbox.selectConversation('cloud-conversation-1')
  assert.equal(context.state.connection, 'paused')
  pendingList.resolve()
  await refresh
  assert.equal(context.state.connection, 'paused')
  assert.equal(messageReads, 1)
  await context.inbox.refresh()
  assert.equal(messageReads, 1)
  await context.inbox.refresh({ manual: true })
  assert.equal(context.state.connection, 'ready')
  assert.equal(messageReads, 2)
  context.inbox.dispose()
})

test('自动刷新合并新消息并保留更早已加载页，has_more仍对应最早页', async () => {
  const records = ['z', 'y', 'x', 'a', 'b', 'c'].map((id, index) => ({ ...message(id), id: `uuid-0${index + 1}` }))
  const cursors = []
  const context = setup({ api: { messages: async (_id, { before }) => {
    cursors.push(before)
    const end = before ? records.findIndex((item) => item.id === before) : records.length
    const start = Math.max(0, end - 2)
    return { messages: records.slice(start, end), has_more: start > 0 }
  } }, bridge: { getBossMessages: async () => ({ account: sourceAccount, conversation: { platform_conversation_id: 'boss-conversation-1', peer_id: 'peer-1' }, messages: [] }) } })
  await context.inbox.start()
  await context.inbox.selectConversation('cloud-conversation-1')
  await context.inbox.loadOlder()
  assert.equal(context.state.messages.length, 4)
  assert.equal(context.state.hasMore, true)
  await context.inbox.refresh()
  assert.equal(context.state.messages.length, 4)
  assert.equal(context.state.hasMore, true)
  await context.inbox.loadOlder()
  assert.equal(context.state.messages.length, 6)
  assert.equal(context.state.hasMore, false)
  records.push({ ...message('new'), id: 'uuid-07' })
  await context.inbox.refresh()
  assert.equal(context.state.messages.length, 7)
  assert.equal(context.state.hasMore, false)
  const count = cursors.length
  await context.inbox.loadOlder()
  assert.equal(cursors.length, count)
  assert.deepEqual(cursors.filter(Boolean), ['uuid-05', 'uuid-03'])
  context.inbox.dispose()
})

test('切账号时旧列表在途不占用新刷新，迟到结果也不覆盖新执行资格', async () => {
  const entered = deferred()
  const blocked = deferred()
  let listReads = 0
  const context = setup({ bridge: { listBossConversations: async () => {
    listReads += 1
    if (listReads === 2) { entered.resolve(); await blocked.promise }
    return { account: sourceAccount, conversations: [{ ...conversation(), security_id: 'current-token' }], has_more: false }
  } } })
  await context.inbox.start()
  const oldRefresh = context.inbox.refresh()
  await entered.promise
  await context.inbox.selectAccount(account.id)
  assert.equal(listReads, 3)
  assert.equal(context.state.connection, 'ready')
  blocked.resolve()
  await oldRefresh
  assert.equal(context.state.connection, 'ready')
  context.inbox.dispose()
})

test('暂停时仍清理到期操作正文，采用源时间校准后的更早保留截止点', async () => {
  const operation = { client_operation_id: 'old-operation', created_at: new Date(NOW - 1000).toISOString(), retain_until: new Date(NOW - 1).toISOString(), text: '到期后不可继续展示' }
  const context = setup({ api: { operations: async () => ({ operations: [operation] }) } })
  await context.inbox.start()
  context.inbox.prune()
  assert.equal(context.state.operations.length, 0)
  context.inbox.dispose()
})
