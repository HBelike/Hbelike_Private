import test from 'node:test'
import assert from 'node:assert/strict'
import { createJobLibraryConnection } from './job-library-connection.js'

function deferred() {
  let resolve, reject
  const promise = new Promise((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}

test('同一组件的并发检测合并，连接失败仅为暂不可用', async () => {
  const task = deferred()
  let calls = 0
  const states = []
  const connection = createJobLibraryConnection({ ping: () => { calls++; return task.promise }, onChange: (state) => states.push(state) })
  const first = connection.check()
  const second = connection.check()
  assert.equal(first, second)
  await Promise.resolve()
  assert.equal(calls, 1)
  task.reject(new Error('连接超时'))
  assert.equal(await first, false)
  assert.equal(states.at(-1).status, 'unavailable')
  assert.match(states.at(-1).error, /重新检测/)
})

test('旧失败不能覆盖新桥接的成功结果', async () => {
  const old = deferred()
  const states = []
  let calls = 0
  const connection = createJobLibraryConnection({ ping: () => ++calls === 1 ? old.promise : Promise.resolve({ connected: true, version: '0.4.1' }), onChange: (state) => states.push(state) })
  const first = connection.check()
  await Promise.resolve()
  assert.equal(await connection.handleSignal({ type: 'ready' }), true)
  old.reject(new Error('旧探测超时'))
  assert.equal(await first, false)
  assert.equal(states.at(-1).status, 'ready')
  assert.equal(states.at(-1).version, '0.4.1')
})

test('桥接断开后不接受旧成功，重新检测可恢复', async () => {
  const old = deferred()
  const states = []
  let calls = 0
  const connection = createJobLibraryConnection({ ping: () => ++calls === 1 ? old.promise : Promise.resolve({ connected: true }), onChange: (state) => states.push(state) })
  const first = connection.check()
  await Promise.resolve()
  await connection.handleSignal({ type: 'disconnected' })
  old.resolve({ connected: true })
  assert.equal(await first, false)
  assert.equal(states.at(-1).status, 'unavailable')
  assert.equal(await connection.check(), true)
  assert.equal(states.at(-1).status, 'ready')
})

test('ready只触发验证，后台仍不可用时不能显示已连接', async () => {
  const states = []
  const connection = createJobLibraryConnection({ ping: async () => { throw new Error('后台不可达') }, onChange: (state) => states.push(state) })
  assert.equal(await connection.handleSignal({ type: 'ready', version: '0.4.1' }), false)
  assert.equal(states.at(-1).status, 'unavailable')
})

test('卸载后不更新界面且不再发起检测', async () => {
  const task = deferred()
  const states = []
  let calls = 0
  const connection = createJobLibraryConnection({ ping: () => { calls++; return task.promise }, onChange: (state) => states.push(state) })
  const first = connection.check()
  await Promise.resolve()
  connection.dispose()
  const count = states.length
  task.resolve({ connected: true })
  assert.equal(await first, false)
  assert.equal(states.length, count)
  assert.equal(await connection.handleSignal({ type: 'ready' }), false)
  assert.equal(await connection.check(), false)
  assert.equal(calls, 1)
})
