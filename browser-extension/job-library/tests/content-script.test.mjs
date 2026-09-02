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
        getManifest: () => ({ version: '0.3.4' }),
        sendMessage
      }
    }
  }

  runInNewContext(bridgeSource, sandbox)

  return { listeners, posted, removed, window }
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
})

test('异步报告扩展上下文失效时同样静默断开旧监听器', async () => {
  const bridge = loadBridge(() => Promise.reject(new Error('Extension context invalidated.')))
  const listener = bridge.listeners.get('message')

  listener(assessmentRequest(bridge))
  await new Promise((resolve) => setImmediate(resolve))

  assert.equal(bridge.listeners.has('message'), false)
  assert.equal(bridge.posted.some((item) => item.payload.requestId === 'request-1'), false)
})

test('普通连接错误仍返回可读的桥接错误', async () => {
  const bridge = loadBridge(() => Promise.reject(new Error('Receiving end does not exist.')))
  const listener = bridge.listeners.get('message')

  listener(assessmentRequest(bridge))
  await new Promise((resolve) => setImmediate(resolve))

  const response = bridge.posted.find((item) => item.payload.requestId === 'request-1')
  assert.equal(response?.payload?.ok, false)
  assert.equal(response?.payload?.error?.code, 'extension_unavailable')
  assert.match(response?.payload?.error?.message ?? '', /Receiving end does not exist/)
})
