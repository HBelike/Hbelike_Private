import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { runInNewContext } from 'node:vm'
import { JobLibraryBridgeError } from './job-library-bridge.js'
import { greetingMessageError, recordGreetingAttempt, updateGreetingItemStatus, stopGreetingItems, queueGreetingItems, retryGreetingItems } from './career-greeting-preview.js'

const source = await readFile(new URL('./components/CareerGreetingDialog.vue', import.meta.url), 'utf8')
const queueBody = source.slice(source.indexOf('async function runSerialQueue()'), source.indexOf('async function retryFailedItem('))
const startBody = source.slice(source.indexOf('function validateIncludedMessages()'), source.indexOf('async function runSerialQueue()'))
const retryBody = source.slice(source.indexOf('async function retryFailedItem('), source.indexOf('async function ensureGreetingExtensionReady('))

function state(message, simulationMode = false, nextAttemptMode = 'full') {
  const calls = []
  const value = {
    props: { simulationMode }, Error,
    items: { value: [{ id: 'test-job', message, job: {}, status: 'queued', included: true, nextAttemptMode }] },
    calls, stopRequested: { value: false }, stopped: { value: false }, sending: { value: true }, activeItemId: { value: '' }, sendError: { value: '' },
    greetingMessageError, recordGreetingAttempt, updateGreetingItemStatus, stopGreetingItems, retryGreetingItems, JobLibraryBridgeError,
    previewDelay: async () => calls.push('simulation-delay'),
    jobLibraryBridge: {
      sendGreeting: async () => { calls.push('send'); return {} },
      retryGreetingMessage: async () => { calls.push('retry'); return {} }
    }
  }
  runInNewContext(queueBody, value)
  return value
}

test('真正串行发送入口的普通发送、仅重发和模拟分支均允许超过建议字数', async () => {
  for (const [simulation, mode] of [[false, 'full'], [false, 'message'], [true, 'full']]) {
    const value = state('😀'.repeat(201), simulation, mode)
    await value.runSerialQueue()
    assert.deepEqual(value.calls, simulation ? ['simulation-delay', 'simulation-delay'] : [mode === 'message' ? 'retry' : 'send'])
    assert.equal(value.items.value[0].status, 'sent')
    assert.equal(value.items.value[0].message, '😀'.repeat(201))
    assert.equal(value.sendError.value, '')
  }
})

test('串行入口允许200个emoji，使用UTF16长度不会误拒绝', async () => {
  const value = state('😀'.repeat(200))
  await value.runSerialQueue()
  assert.deepEqual(value.calls, ['send'])
  assert.equal(value.items.value[0].status, 'sent')
})

test('开始发送入口允许超过建议字数的文案进入发送', async () => {
  const value = state('中'.repeat(201))
  value.sending.value = false
  Object.assign(value, {
    invalidIncludedItems: { value: [] }, includedItems: { value: value.items.value }, riskOpen: { value: false },
    stage: { value: 'review' }, queueGreetingItems,
    ensureGreetingExtensionReady: async () => { value.calls.push('connect'); return true }
  })
  runInNewContext(startBody, value)
  await value.startSend()
  assert.deepEqual(value.calls, ['connect', 'send'])
  assert.equal(value.stage.value, 'sending')
  assert.equal(value.items.value[0].status, 'sent')
})

test('重新发送失败项允许被编辑为超过建议字数的文案', async () => {
  const value = state('中'.repeat(201))
  value.items.value[0] = { ...value.items.value[0], status: 'failed', retryable: true }
  value.sending.value = false
  value.ensureGreetingExtensionReady = async () => { value.calls.push('connect'); return true }
  runInNewContext(retryBody, value)
  await value.retryFailedItem(value.items.value[0])
  assert.deepEqual(value.calls, ['connect', 'send'])
  assert.equal(value.items.value[0].status, 'sent')
  assert.equal(value.sendError.value, '')
})

test('串行发送和失败重发仍阻止空白消息', async () => {
  const value = state('  ')
  await value.runSerialQueue()
  assert.deepEqual(value.calls, [])
  assert.equal(value.items.value[0].status, 'failed')
  assert.match(value.sendError.value, /不能为空/)
  value.sending.value = false
  value.ensureGreetingExtensionReady = async () => { value.calls.push('connect'); return true }
  runInNewContext(retryBody, value)
  await value.retryFailedItem({ ...value.items.value[0], retryable: true })
  assert.deepEqual(value.calls, [])
  assert.match(value.sendError.value, /不能为空/)
})
