import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { runInNewContext } from 'node:vm'

const bridgeSource = await readFile(new URL('../content-script.js', import.meta.url), 'utf8')
const WEB_CHANNEL = 'find-job-job-library-web-v1'

function loadBridge(sendMessage) {
  const listeners = new Map()
  const posted = []
  const removed = []
  const window = {
    location: { origin: 'http://127.0.0.1:5173' },
    addEventListener(type, listener) {
      listeners.set(type, listener)
    },
    removeEventListener(type, listener) {
      removed.push({ type, listener })
      if (listeners.get(type) === listener) listeners.delete(type)
    },
    postMessage(payload, origin) {
      posted.push({ payload, origin })
    }
  }
  const sandbox = {
    window,
    chrome: {
      runtime: {
        id: 'extension-id',
        getManifest: () => ({ version: '0.4.1' }),
        sendMessage
      }
    }
  }

  runInNewContext(bridgeSource, sandbox)

  return {
    listeners, posted, removed, window, sandbox,
    reinject: () => runInNewContext(bridgeSource, sandbox)
  }
}

function assessmentRequest(bridge, requestId = 'request-1') {
  return {
    source: bridge.window,
    origin: bridge.window.location.origin,
    data: {
      channel: WEB_CHANNEL,
      requestId,
      action: 'refresh_assessment_problem',
      payload: {}
    }
  }
}

test('扩展上下文失效时移除旧监听器且不抛出未捕获异常', () => {
  const bridge = loadBridge(() => {
    throw new Error('Extension context invalidated.')
  })
  const listener = bridge.listeners.get('message')

  assert.doesNotThrow(() => listener(assessmentRequest(bridge)))
  assert.equal(bridge.listeners.has('message'), false)
  assert.equal(bridge.removed.some((item) => item.type === 'message' && item.listener === listener), true)
  assert.equal(bridge.posted.some((item) => item.payload.requestId === 'request-1'), false)
  assert.equal(bridge.posted.filter((item) => item.payload.type === 'disconnected').length, 1)
  assert.equal(bridge.posted.at(-1).payload.version, '0.4.1')
})

test('异步报告扩展上下文失效时同样静默断开旧监听器', async () => {
  const bridge = loadBridge(() => Promise.reject(new Error('Extension context invalidated.')))
  const listener = bridge.listeners.get('message')

  listener(assessmentRequest(bridge))
  await new Promise((resolve) => setImmediate(resolve))

  assert.equal(bridge.listeners.has('message'), false)
  assert.equal(bridge.posted.some((item) => item.payload.requestId === 'request-1'), false)
  assert.equal(bridge.posted.at(-1).payload.type, 'disconnected')
})

test('普通连接错误仍返回可读的桥接错误', async () => {
  const bridge = loadBridge(() => Promise.reject(new Error('Receiving end does not exist.')))
  const listener = bridge.listeners.get('message')

  listener(assessmentRequest(bridge))
  await new Promise((resolve) => setImmediate(resolve))

  const response = bridge.posted.find((item) => item.payload.requestId === 'request-1')
  assert.equal(response?.payload?.ok, false)
  assert.equal(response?.payload?.error?.code, 'bridge_disconnected')
  assert.match(response?.payload?.error?.message ?? '', /Receiving end does not exist/)
  assert.equal(bridge.listeners.has('message'), true)
})

test('普通同步异常返回 bridge_error 而不是未安装', () => {
  const bridge = loadBridge(() => { throw new Error('Message port closed.') })
  bridge.listeners.get('message')(assessmentRequest(bridge))
  const response = bridge.posted.find((item) => item.payload.requestId === 'request-1')
  assert.equal(response.payload.error.code, 'bridge_error')
  assert.equal(bridge.posted.some((item) => item.payload.type === 'disconnected'), false)
})

test('就绪通知包含版本且只发往当前来源', () => {
  const bridge = loadBridge(() => Promise.resolve({ ok: true }))
  assert.equal(bridge.posted[0].payload.type, 'ready')
  assert.equal(bridge.posted[0].payload.version, '0.4.1')
  assert.equal(bridge.posted[0].origin, bridge.window.location.origin)
})

test('runtime 已失效时通知断线并允许新脚本重新接管', async () => {
  const bridge = loadBridge(() => Promise.resolve({ ok: true, data: { connected: true } }))
  delete bridge.sandbox.chrome.runtime.id
  bridge.listeners.get('message')(assessmentRequest(bridge))
  assert.equal(bridge.listeners.has('message'), false)
  assert.equal(bridge.sandbox.__findJobBrowserBridge, undefined)
  assert.equal(bridge.posted.at(-1).payload.type, 'disconnected')

  bridge.sandbox.chrome.runtime.id = 'new-extension-id'
  bridge.sandbox.chrome.runtime.getManifest = () => ({ version: '0.4.2' })
  bridge.reinject()
  assert.equal(bridge.listeners.has('message'), true)
  assert.equal(bridge.posted.at(-1).payload.type, 'ready')
  assert.equal(bridge.posted.at(-1).payload.version, '0.4.2')
  bridge.listeners.get('message')(assessmentRequest(bridge, 'new-request'))
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(bridge.posted.find((item) => item.payload.requestId === 'new-request').payload.ok, true)
})

test('新脚本接管后旧请求迟到的失效不能断开新监听器', async () => {
  let rejectOld
  const bridge = loadBridge(() => new Promise((resolve, reject) => { rejectOld = reject }))
  bridge.listeners.get('message')(assessmentRequest(bridge))
  bridge.reinject()
  const newListener = bridge.listeners.get('message')
  rejectOld(new Error('Extension context invalidated.'))
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(bridge.listeners.get('message'), newListener)
  assert.equal(bridge.posted.some((item) => item.payload.type === 'disconnected'), false)
  assert.equal(bridge.posted.some((item) => item.payload.requestId === 'request-1'), false)
})

test('桥接仍拒绝其他窗口、来源、通道和未知动作', () => {
  let calls = 0
  const bridge = loadBridge(() => { calls += 1 })
  const request = assessmentRequest(bridge)
  const listener = bridge.listeners.get('message')
  listener({ ...request, source: {} })
  listener({ ...request, origin: 'https://other.example' })
  listener({ ...request, data: { ...request.data, channel: 'other-channel' } })
  listener({ ...request, data: { ...request.data, action: 'unknown-action' } })
  assert.equal(calls, 0)
})
