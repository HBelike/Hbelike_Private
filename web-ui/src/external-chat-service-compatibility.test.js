import assert from 'node:assert/strict'
import test from 'node:test'
import { createExternalChatClient, createExternalChatInbox } from './external-chat-client.js'

const ROOT = '/api/career/external-chat'
const account = { id: 'cloud-account', platform: 'boss', platform_account_id: 'test-geek', display_name: '测试账号', last_synced_at: '2026-11-09T09:00:00Z' }
const conversation = { id: 'saved-conversation', account_id: account.id, platform_conversation_id: 'test-peer', peer_id: 'test-peer', peer_name: '已保存招聘者' }
const response = (status, payload) => ({ status, ok: status >= 200 && status < 300, json: async () => payload })

test('裸路由404携带方法和路径诊断，不记录提交正文或请求凭据', async () => {
  const api = createExternalChatClient(async () => response(404, { detail: 'Not Found' }))
  for (const [invoke, method, path] of [
    [() => api.operations(account.id), 'GET', `${ROOT}/accounts/cloud-account/operations`],
    [() => api.claimExecution(account.id, { executor_id: 'private-executor', revision: 1 }), 'POST', `${ROOT}/accounts/cloud-account/execution`]
  ]) {
    await assert.rejects(invoke, (error) => {
      assert.equal(error.code, 'backend_route_unavailable')
      assert.equal(error.status, 404)
      assert.equal(error.method, method)
      assert.equal(error.path, path)
      assert.match(error.message, /更新或重启后端/)
      assert.equal(JSON.stringify(error).includes('private-executor'), false)
      return true
    })
  }
})

test('业务资源不存在的结构化404保留原始业务错误，不能误判为版本不匹配', async () => {
  const api = createExternalChatClient(async () => response(404, { detail: { code: 'external_chat_not_found', message: '外部聊天会话不存在或无访问权限' } }))
  await assert.rejects(() => api.messages('missing'), (error) => error.code === 'external_chat_not_found'
    && error.message === '外部聊天会话不存在或无访问权限' && error.status === 404)
})

test('旧API缺执行层时显示服务未就绪，不探测BOSS；后端更新后手动恢复并保留历史', async () => {
  let upgraded = false
  let state
  const calls = []
  const lease = { executor_id: 'compatibility-page', revision: 1, paused: false }
  const api = createExternalChatClient(async (url, options) => {
    calls.push(`${options.method || 'GET'} ${url}`)
    if (url.endsWith('/accounts')) return response(200, { accounts: [account] })
    if (url.endsWith('/conversations')) return response(200, { conversations: [conversation] })
    if (url.includes('/messages?')) return response(200, { messages: [], has_more: false })
    if (url.endsWith('/operations')) return upgraded ? response(200, { operations: [] }) : response(404, { detail: 'Not Found' })
    if (url.endsWith('/execution') || url.endsWith('/execution/check')) return upgraded ? response(200, { execution: lease }) : response(404, { detail: 'Not Found' })
    if (url.endsWith('/sync')) return response(200, { account, imported_messages: 0, imported_conversations: 0 })
    assert.fail(`未预期的平台请求：${url}`)
  })
  const bridge = {
    ping: async () => { calls.push('bridge:ping'); return { connected: true, capabilities: ['boss_chat_execute_v1'] } },
    getBossChatState: async () => { calls.push('bridge:state'); return { account } },
    listBossConversations: async () => { calls.push('bridge:list'); return { account, conversations: [], has_more: false } }
  }
  const inbox = createExternalChatInbox({ api, bridge, executorId: 'compatibility-page', onChange(value) { state = value } })
  await inbox.start()
  assert.equal(state.connection, 'service_unavailable')
  assert.equal(state.conversations[0].id, conversation.id)
  assert.equal(state.lastSyncedAt, account.last_synced_at)
  assert.equal(state.canReply, false)
  assert.equal(calls.some((call) => call.startsWith('bridge:')), false)
  await inbox.refresh()
  await inbox.refresh({ manual: true })
  assert.equal(state.connection, 'service_unavailable')
  assert.equal(calls.some((call) => call.startsWith('bridge:')), false)
  upgraded = true
  await inbox.refresh({ manual: true })
  assert.equal(state.connection, 'ready')
  assert.equal(state.error, '')
  assert.equal(state.conversations[0].id, conversation.id)
  assert.ok(calls.includes('bridge:list'))
})

test('连账号历史路由也缺失时，首次和手动重新检测都先停止在平台接口', async () => {
  let state
  let bridgeCalls = 0
  const api = createExternalChatClient(async () => response(404, { detail: 'Not Found' }))
  const inbox = createExternalChatInbox({ api, bridge: { ping: async () => { bridgeCalls++; return {} } }, executorId: 'compatibility-page', onChange(value) { state = value } })
  await inbox.start()
  await inbox.refresh({ manual: true })
  assert.equal(state.connection, 'service_unavailable')
  assert.equal(state.loading, false)
  assert.equal(bridgeCalls, 0)
})
