import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { runInNewContext } from 'node:vm'
import { normalizeBossExtensionConnection } from './boss-extension-onboarding.js'

const source = await readFile(new URL('./components/CareerGreetingDialog.vue', import.meta.url), 'utf8')
const body = source.slice(source.indexOf('async function ensureGreetingExtensionReady('), source.indexOf('async function handleFailureAction('))

function load(ping) {
  const state = { props: { simulationMode: false }, jobLibraryBridge: { ping }, normalizeBossExtensionConnection,
    BOSS_EXTENSION_VERSION: '0.4.1', Error,
    connectionIssue: { value: false }, installDialogOpen: { value: false },
    sendError: { value: '' }, extensionGateError: { value: '' }, extensionGateMode: { value: 'install' } }
  runInNewContext(body, state)
  return state
}

test('发送前检测失败仅展示连接提示，不自动弹安装窗口', async () => {
  for (const ping of [async () => ({ connected: false }), async () => { throw new Error('连接超时') }]) {
    const state = load(ping)
    assert.equal(await state.ensureGreetingExtensionReady({ requireDispatchCapability: true }), false)
    assert.equal(state.installDialogOpen.value, false)
    assert.equal(state.connectionIssue.value, true)
    assert.match(state.sendError.value, /重新检测/)
  }
})

test('确认连接成功但缺少发送能力时保留升级提示', async () => {
  const state = load(async () => ({ connected: true, version: '0.2.3', capabilities: [] }))
  assert.equal(await state.ensureGreetingExtensionReady({ requireDispatchCapability: true }), false)
  assert.equal(state.extensionGateMode.value, 'update')
  assert.equal(state.installDialogOpen.value, true)
  assert.equal(state.connectionIssue.value, false)
})

test('重新检测成功会清除连接提示，不自动触发发送', async () => {
  let calls = 0
  const state = load(async () => ++calls === 1 ? { connected: false } : { connected: true, capabilities: ['greeting_fire_and_continue'] })
  assert.equal(await state.ensureGreetingExtensionReady({ requireDispatchCapability: true }), false)
  assert.equal(await state.ensureGreetingExtensionReady({ requireDispatchCapability: true }), true)
  assert.equal(state.connectionIssue.value, false)
  assert.equal(state.sendError.value, '')
  assert.equal(state.installDialogOpen.value, false)
  assert.equal(calls, 2)
})
