import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { checkBossChatApplication } from '../boss-chat-control.js'

const execution = { account_id: 'account-uuid', executor_id: 'browser-page', revision: 2 }
const operation = { client_operation_id: 'operation-uuid', account_id: execution.account_id, conversation_id: 'conversation-uuid', executor_id: execution.executor_id, revision: 2, text: '确认回复', status: 'pending' }
function fixture({ hidden = false, pathname = '/career/messages', failedPath = '', observed = execution, sourcePeer = 'hr-1' } = {}) {
  const calls = []
  const context = { document: { hidden, visibilityState: hidden ? 'hidden' : 'visible' }, location: { pathname }, AbortController, setTimeout, clearTimeout,
    fetch: async (path, options) => {
      calls.push({ path, options })
      if (path === failedPath) return { ok: false, json: async () => ({ detail: { code: 'execution_lost', message: '资格已被接管' } }) }
      const data = path.endsWith('/execution/check') ? { execution: observed }
        : path.endsWith('/accounts') ? { accounts: [{ id: execution.account_id, platform: 'boss', platform_account_id: 'me-1' }] }
          : path.endsWith('/conversations') ? { conversations: [{ id: operation.conversation_id, peer_id: sourcePeer }] }
            : path.endsWith('/operations/operation-uuid') ? { operation } : { operation, transitioned: true }
      return { ok: true, json: async () => data }
    } }
  vm.createContext(context)
  return { calls, context, run: (payload) => vm.runInContext(`(${checkBossChatApplication.toString()})(${JSON.stringify(payload)})`, context) }
}
const payload = { execution, accountId: 'me-1', peerId: 'hr-1', clientOperationId: 'operation-uuid', text: operation.text }

test('发起页序列化检查同源资格、冻结目标与正文，凭据不出浏览器', async () => {
  const f = fixture()
  const result = await f.run(payload)
  assert.equal(result.ok, true)
  assert.equal(result.data.operation.status, 'pending')
  assert.equal(f.calls.length, 4)
  assert.equal(f.calls.every(({ path, options }) => path.startsWith('/api/career/external-chat/') && options.credentials === 'same-origin'), true)
})

test('后台、离路由、无资格与切会话均拒绝，调用者不能用allowHidden绕过', async () => {
  for (const config of [{ hidden: true }, { pathname: '/career' }, { sourcePeer: 'other' }, { observed: { ...execution, revision: 3 } }]) {
    const result = await fixture(config).run({ ...payload, allowHidden: true })
    assert.equal(result.ok, false)
  }
  assert.equal((await fixture().run({ ...payload, execution: null })).error.code, 'execution_required')
  assert.equal((await fixture().run({ ...payload, text: '改了正文' })).error.code, 'operation_mismatch')
})

test('服务端拒绝或不可达不能被本地页面状态替代', async () => {
  assert.equal((await fixture({ failedPath: '/api/career/external-chat/accounts/account-uuid/execution/check' }).run(payload)).error.code, 'execution_lost')
})

test('取消与已提交结果收尾允许页面隐藏及离路由，不能启动新操作', async () => {
  const f = fixture({ hidden: true, pathname: '/career' })
  assert.equal((await f.run({ ...payload, kind: 'cancel' })).ok, true)
  assert.equal((await f.run({ ...payload, kind: 'transition', status: 'submitted' })).ok, true)
  assert.equal((await f.run({ ...payload, kind: 'transition', status: 'executing' })).ok, false)
})
