import assert from 'node:assert/strict'
import test from 'node:test'
import { readFile } from 'node:fs/promises'
import { runInNewContext } from 'node:vm'

const channel = 'find-job-job-library-web-v1'
let sequence = 0

async function dispatcherFixture(t) {
  const oldChrome = globalThis.chrome
  t.after(() => { globalThis.chrome = oldChrome })
  const event = () => ({ addListener() {}, removeListener() {} })
  const existing = [{ id: 5, url: 'https://www.zhipin.com/web/geek/jobs', status: 'complete' }]
  const account = { platform: 'boss', platform_account_id: 'me-1', display_name: '求职者' }
  const execution = { account_id: 'cloud-account', executor_id: 'page-1', revision: 1 }
  const storage = {}
  const calls = { script: [], created: [], query: 0 }
  let listener
  let override = null
  globalThis.chrome = {
    runtime: { getManifest: () => ({ version: '0.5.0' }), onMessage: { addListener(value) { listener = value } }, onInstalled: event(), onStartup: event() },
    storage: { local: {
      async get(key) { return { [key]: storage[key] } },
      async set(value) { Object.assign(storage, value) },
      async remove(key) { delete storage[key] }
    } },
    tabs: {
      async query() { calls.query++; return existing },
      async get(id) { return existing.find((tab) => tab.id === id) },
      async create(value) { calls.created.push(value); const tab = { id: 11, ...value, status: 'complete' }; existing.push(tab); return tab },
      onUpdated: event()
    },
    scripting: { async executeScript(value) {
      if (value.func.name === 'checkBossChatApplication') return [{ result: { ok: true, data: { execution } } }]
      calls.script.push(value)
      if (override) return [{ result: override }]
      const action = value.args[0].action
      const response = action === 'list'
        ? { code: 0, zpData: { result: [{ uid: 'hr-1', name: '王女士', securityId: 'fresh-token', lastTS: Date.now(), lastMsg: '方便沟通吗？', unreadMsgCount: 1 }] } }
        : { code: 0, zpData: { messages: [{ mid: '90071992547409931', from: { uid: 'hr-1' }, to: { uid: 'me-1' }, time: Date.now(), body: { type: 1, text: '方便沟通吗？' } }] } }
      return [{ result: { ok: true, data: { account, observed_at: new Date().toISOString(), ...(action === 'state' ? {} : { response }) } } }]
    } },
    windows: { onRemoved: event() }, action: { onClicked: event() }
  }
  await import(`../service-worker.js?boss-chat-dispatch=${++sequence}`)
  const request = (action, payload = {}, url = 'http://localhost:5173/career/messages') => new Promise((resolve) => listener({ channel, action, payload }, { url, tab: { id: 99 } }, resolve))
  return { request, calls, execution, storage, setResult(value) { override = value } }
}

test('真实后台入口声明收件能力并分发三项只读动作，保持用户岗位页', async (t) => {
  const { request, calls, execution } = await dispatcherFixture(t)
  assert.equal((await request('ping')).data.capabilities.includes('boss_chat_read'), true)
  assert.equal((await request('boss_chat_state')).data.account.platform_account_id, 'me-1')
  const list = await request('boss_chat_list', { accountId: 'me-1', execution, page: 2 })
  assert.equal(list.data.conversations[0].peer_id, 'hr-1')
  const messages = await request('boss_chat_messages', { accountId: 'me-1', peerId: 'hr-1', securityId: 'fresh-token', execution, page: 3, count: 80 })
  assert.equal(messages.data.messages[0].direction, 'incoming')
  assert.equal(messages.data.messages[0].platform_message_id, '90071992547409931')
  assert.deepEqual(calls.created, [{ url: 'https://www.zhipin.com/web/geek/chat', active: false }])
  assert.equal(calls.script.length, 3)
  assert.equal(calls.script.every((call) => call.target.tabId === 11 && call.world === 'MAIN'), true)
  assert.equal(calls.script.some((call) => /friend\/add|\.click\(/.test(call.func.toString())), false)
  assert.equal(calls.script[1].args[0].page, 2)
  assert.equal(calls.script[2].args[0].page, 3)
  assert.equal(calls.script[2].args[0].count, 80)
  assert.equal(list.data.has_more, null)
})

test('缺失会话身份和非法来源在访问BOSS前拒绝，平台错误和账号变化返回暂停原因', async (t) => {
  const { request, calls, execution, storage, setResult } = await dispatcherFixture(t)
  assert.equal((await request('boss_chat_messages', {})).error.code, 'execution_required')
  assert.equal((await request('boss_chat_messages', { accountId: 'me-1', execution })).error.code, 'chat_identity_missing')
  assert.equal((await request('boss_chat_list', {}, 'https://other.example/')).error.code, 'forbidden_sender')
  assert.equal(calls.query, 0)
  setResult({ ok: false, error: { code: 'rate_limited', message: '请稍后继续' } })
  const limited = await request('boss_chat_list', { accountId: 'me-1', execution })
  assert.equal(limited.error.code, 'rate_limited')
  assert.equal(limited.error.stopBatch, true)
  assert.equal(storage['boss-chat-pause-v1:me-1'].code, 'rate_limited')
  const before = calls.script.length
  assert.equal((await request('boss_chat_list', { accountId: 'me-1', execution: { ...execution, executor_id: 'page-2' } })).error.code, 'account_paused')
  assert.equal(calls.script.length, before)
  await request('boss_chat_resume', { accountId: 'me-1', execution })
  setResult({ ok: true, data: { account: { platform_account_id: 'other' } } })
  const changed = await request('boss_chat_messages', { accountId: 'me-1', peerId: 'hr-1', securityId: 'fresh-token', execution })
  assert.equal(changed.error.code, 'account_changed')
})

test('Content Script 将三项收件动作真实转发至runtime', async () => {
  const source = await readFile(new URL('../content-script.js', import.meta.url), 'utf8')
  const listeners = new Set()
  const calls = []
  const window = { location: { origin: 'http://localhost:5173' }, addEventListener(type, listener) { listeners.add(listener) }, removeEventListener(type, listener) { listeners.delete(listener) }, postMessage() {} }
  runInNewContext(source, { window, chrome: { runtime: { id: 'helper', getManifest: () => ({ version: '0.5.0' }), sendMessage(message) { calls.push(message); return Promise.resolve({ ok: true, data: {} }) } } } })
  for (const action of ['boss_chat_state', 'boss_chat_list', 'boss_chat_messages']) {
    for (const listener of listeners) listener({ source: window, origin: window.location.origin, data: { channel, requestId: action, action, payload: {} } })
  }
  await Promise.resolve()
  assert.deepEqual(calls.map((call) => call.action), ['boss_chat_state', 'boss_chat_list', 'boss_chat_messages'])
})
