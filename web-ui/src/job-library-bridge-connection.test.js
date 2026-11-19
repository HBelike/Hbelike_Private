import test from 'node:test'
import assert from 'node:assert/strict'

const EXTENSION_CHANNEL = 'find-job-job-library-extension-v1'
let moduleSequence = 0

async function setup(t) {
  const originalWindow = globalThis.window
  const listeners = new Set()
  const timers = new Map()
  const requests = []
  let timerId = 0
  const window = {
    location: { origin: 'http://localhost:5173' },
    addEventListener(type, listener) { assert.equal(type, 'message'); listeners.add(listener) },
    removeEventListener(type, listener) { listeners.delete(listener) },
    setTimeout(callback, delay) { timers.set(++timerId, { callback, delay }); return timerId },
    clearTimeout(id) { timers.delete(id) },
    postMessage(message, origin) { assert.equal(origin, window.location.origin); requests.push(message) }
  }
  globalThis.window = window
  t.after(() => { globalThis.window = originalWindow })
  const bridge = await import(`./job-library-bridge.js?connection-test=${++moduleSequence}`)
  function emit(data, overrides = {}) {
    const event = { source: window, origin: window.location.origin, data: { channel: EXTENSION_CHANNEL, ...data }, ...overrides }
    for (const listener of [...listeners]) listener(event)
  }
  function respond(request, data = { connected: true, version: '0.4.1' }) {
    emit({ requestId: request.requestId, ok: true, data })
  }
  function fireTimer(delay) {
    const entry = [...timers].find(([, timer]) => timer.delay === delay)
    assert.ok(entry, `应存在 ${delay}ms 定时器`)
    timers.delete(entry[0])
    entry[1].callback()
  }
  return { ...bridge, window, listeners, requests, timers, emit, respond, fireTimer }
}

async function flush() {
  for (let index = 0; index < 10; index += 1) await Promise.resolve()
}

test('并发ping共用同一个在途Promise，完成后允许重新握手', async (t) => {
  const { jobLibraryBridge, requests, respond, listeners, timers } = await setup(t)
  const first = jobLibraryBridge.ping()
  assert.equal(jobLibraryBridge.ping(), first)
  assert.equal(requests.length, 1)
  respond(requests[0])
  assert.equal((await first).connected, true)
  assert.equal(listeners.size, 0)
  assert.equal(timers.size, 0)
  const next = jobLibraryBridge.ping()
  assert.notEqual(next, first)
  assert.equal(requests.length, 2)
  respond(requests[1])
  await next
})

test('首次握手超时重试一次，最终连接成功不判断为未安装', async (t) => {
  const { jobLibraryBridge, requests, fireTimer, respond } = await setup(t)
  const pending = jobLibraryBridge.ping()
  fireTimer(1800)
  await flush()
  fireTimer(160)
  await flush()
  assert.equal(requests.length, 2)
  respond(requests[1])
  assert.equal((await pending).connected, true)
})

test('两次握手超时返回bridge_timeout而非extension_unavailable', async (t) => {
  const { jobLibraryBridge, fireTimer, listeners, timers } = await setup(t)
  const pending = jobLibraryBridge.ping()
  const assertion = assert.rejects(pending, (error) => error.code === 'bridge_timeout' && /连接超时/.test(error.message))
  fireTimer(1800)
  await flush()
  fireTimer(160)
  await flush()
  fireTimer(1800)
  await assertion
  assert.equal(listeners.size, 0)
  assert.equal(timers.size, 0)
})

test('业务请求超时返回request_timeout且不触发连接重试', async (t) => {
  const { jobLibraryBridge, fireTimer, requests, listeners } = await setup(t)
  const pending = jobLibraryBridge.searchJobs('Java', 1, { code: '101210100', name: '杭州' })
  const assertion = assert.rejects(pending, (error) => error.code === 'request_timeout')
  fireTimer(22000)
  await assertion
  assert.equal(requests.length, 1)
  assert.equal(listeners.size, 0)
})

test('连接订阅过滤来源和频道，ready只发重检测通知且最后退订移除监听', async (t) => {
  const { subscribeJobLibraryConnection, emit, window, listeners, requests } = await setup(t)
  const updates = []
  const unsubscribe = subscribeJobLibraryConnection((update) => updates.push(update))
  assert.equal(listeners.size, 1)
  emit({ type: 'ready', version: 'wrong-source' }, { source: {} })
  emit({ type: 'ready', version: 'wrong-origin' }, { origin: 'https://other.example' })
  emit({ type: 'ready', channel: 'other-channel' })
  emit({ type: 'reply', connected: true })
  assert.deepEqual(updates, [])
  emit({ type: 'ready', version: '0.4.1' })
  emit({ type: 'disconnected' })
  assert.deepEqual(updates.map(({ type, version }) => ({ type, version })), [
    { type: 'ready', version: '0.4.1' }, { type: 'disconnected', version: '' }
  ])
  assert.equal(updates[1].epoch, updates[0].epoch + 1)
  assert.equal(requests.length, 0)
  assert.equal(window.location.origin, 'http://localhost:5173')
  unsubscribe()
  unsubscribe()
  assert.equal(listeners.size, 0)
})

test('ready期间旧ping立即失效，新probe不复用旧pending且旧回包被忽略', async (t) => {
  const { jobLibraryBridge, subscribeJobLibraryConnection, emit, respond, requests, fireTimer, listeners } = await setup(t)
  let newProbe
  const unsubscribe = subscribeJobLibraryConnection(() => { newProbe = jobLibraryBridge.ping() })
  const oldProbe = jobLibraryBridge.ping()
  emit({ type: 'ready', version: '0.4.1' })
  assert.notEqual(newProbe, oldProbe)
  assert.equal(requests.length, 2)
  respond(requests[0], { connected: true, version: 'stale' })
  await flush()
  fireTimer(160)
  await flush()
  assert.equal(requests.length, 2)
  assert.equal(jobLibraryBridge.ping(), newProbe)
  respond(requests[1], { connected: true, version: '0.4.1' })
  assert.equal((await newProbe).version, '0.4.1')
  assert.equal((await oldProbe).version, '0.4.1')
  unsubscribe()
  assert.equal(listeners.size, 0)
})

test('没有连接订阅时在途ping仍能接收ready并重新探测', async (t) => {
  const { jobLibraryBridge, emit, requests, fireTimer, respond } = await setup(t)
  const pending = jobLibraryBridge.ping()
  emit({ type: 'ready', version: '0.4.1' })
  await flush()
  fireTimer(160)
  await flush()
  assert.equal(requests.length, 2)
  respond(requests[1])
  assert.equal((await pending).connected, true)
})

test('旧响应已到达但finally尚未执行时，ready创建的新probe不会被旧finally清除', async (t) => {
  const { jobLibraryBridge, subscribeJobLibraryConnection, emit, respond, requests } = await setup(t)
  let newProbe
  const unsubscribe = subscribeJobLibraryConnection(() => { newProbe = jobLibraryBridge.ping() })
  const oldProbe = jobLibraryBridge.ping()
  respond(requests[0], { connected: true, version: '0.4.0' })
  // 不让Promise微任务执行，模拟响应与扩展重连广播紧邻到达。
  emit({ type: 'ready', version: '0.4.1' })
  assert.equal((await oldProbe).version, '0.4.0')
  assert.equal(jobLibraryBridge.ping(), newProbe)
  assert.equal(requests.length, 2)
  respond(requests[1])
  await newProbe
  unsubscribe()
})

test('disconnected广播使在途握手失效，旧返回不能覆盖重连后的版本', async (t) => {
  const { jobLibraryBridge, emit, requests, fireTimer, respond } = await setup(t)
  const pending = jobLibraryBridge.ping()
  emit({ type: 'disconnected' })
  respond(requests[0], { connected: true, version: 'stale' })
  await flush()
  fireTimer(160)
  await flush()
  respond(requests[1], { connected: true, version: '0.4.1' })
  assert.equal((await pending).version, '0.4.1')
})

test('同一回调多次订阅可独立退订，订阅异常不阻断其他监听', async (t) => {
  const { subscribeJobLibraryConnection, emit, listeners } = await setup(t)
  let count = 0
  const listener = () => { count += 1 }
  const off1 = subscribeJobLibraryConnection(listener)
  const off2 = subscribeJobLibraryConnection(listener)
  const off3 = subscribeJobLibraryConnection(() => { throw new Error('视图卸载') })
  emit({ type: 'ready' })
  assert.equal(count, 2)
  off1()
  emit({ type: 'disconnected' })
  assert.equal(count, 3)
  off2()
  off3()
  assert.equal(listeners.size, 0)
})

test('连接类错误支持重试，业务类错误只调用一次', async (t) => {
  const { pingExtensionWithRetry, JobLibraryBridgeError } = await setup(t)
  for (const code of ['extension_unavailable', 'bridge_timeout', 'bridge_disconnected', 'request_timeout', 'bridge_error']) {
    let attempts = 0
    const result = pingExtensionWithRetry(async () => {
      attempts += 1
      if (attempts === 1) throw new JobLibraryBridgeError(code, code)
      return 'ready'
    }, { wait: async () => {} })
    if (['request_timeout', 'bridge_error'].includes(code)) {
      await assert.rejects(result, (error) => error.code === code)
      assert.equal(attempts, 1)
    } else {
      assert.equal(await result, 'ready')
      assert.equal(attempts, 2)
    }
  }
})
