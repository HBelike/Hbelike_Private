import assert from 'node:assert/strict'
import test from 'node:test'
import { jobLibraryBridge } from './job-library-bridge.js'

test('三个 BOSS 收件动作通过同源桥发送，消息标识转成可克隆的纯数据', async (t) => {
  const previous = globalThis.window
  const listeners = new Set()
  const requests = []
  const timers = new Set()
  globalThis.window = {
    location: { origin: 'http://localhost:5173' },
    addEventListener(type, listener) { listeners.add(listener) },
    removeEventListener(type, listener) { listeners.delete(listener) },
    setTimeout(callback, delay) { assert.equal(delay, 45000); const timer = {}; timers.add(timer); return timer },
    clearTimeout(timer) { timers.delete(timer) },
    postMessage(request, origin) {
      assert.equal(origin, window.location.origin)
      requests.push(structuredClone(request))
      queueMicrotask(() => {
        const event = { source: window, origin, data: { channel: 'find-job-job-library-extension-v1', requestId: request.requestId, ok: true, data: { action: request.action } } }
        for (const listener of [...listeners]) listener(event)
      })
    }
  }
  t.after(() => { globalThis.window = previous })
  await jobLibraryBridge.getBossChatState()
  await jobLibraryBridge.listBossConversations()
  await jobLibraryBridge.getBossMessages(new Proxy({ accountId: 123, peerId: ' boss-1 ', securityId: ' fresh-token ', cookie: 'never-export' }, {}))
  assert.deepEqual(requests.map(({ action }) => action), ['boss_chat_state', 'boss_chat_list', 'boss_chat_messages'])
  assert.deepEqual(requests[2].payload, { accountId: '123', peerId: 'boss-1', securityId: 'fresh-token', page: 1, count: 50, execution: { account_id: '', executor_id: '', revision: 0 } })
  assert.equal(listeners.size, 0)
  assert.equal(timers.size, 0)
})

test('会话账号或临时标识缺失时本地拒绝，不调用扩展', () => {
  for (const payload of [undefined, { accountId: 'me', peerId: 'boss' }, { accountId: '', peerId: 'boss', securityId: 'token' }]) {
    assert.throws(() => jobLibraryBridge.getBossMessages(payload), (error) => error.code === 'chat_identity_missing')
  }
})
