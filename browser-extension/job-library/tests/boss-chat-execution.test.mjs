import test from 'node:test'
import assert from 'node:assert/strict'
import { createBossChatDispatcher } from '../boss-chat-dispatch.js'

const execution = { account_id: 'cloud-account', executor_id: 'page-1', revision: 1 }
const payload = { accountId: 'me-1', peerId: 'hr-1', securityId: 'current-token', text: '确认回复', clientOperationId: 'operation-1', execution }
const sender = { url: 'http://localhost:5173/career/messages', tab: { id: 99 } }
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done }); return { promise, resolve } }
let sequence = 0

function setup(t, { afterPrepare, submitError, lostBeforeSubmit = false, transitionWon = true, sourcePeer = payload.peerId, sourceDraft = '', navigatedPeer = payload.peerId } = {}) {
  const oldChrome = globalThis.chrome
  t.after(() => { globalThis.chrome = oldChrome })
  const storage = {}
  const calls = []
  const operation = { client_operation_id: payload.clientOperationId, account_id: execution.account_id, conversation_id: 'cloud-conversation', executor_id: execution.executor_id, revision: 1, text: payload.text, status: 'pending', cancel_requested: false, expires_at: new Date(Date.now() + 30000).toISOString() }
  let preparing = false
  let currentPeer = sourcePeer
  globalThis.chrome = {
    storage: { local: { get: async (key) => ({ [key]: storage[key] }), set: async (value) => Object.assign(storage, value), remove: async (key) => { delete storage[key] } } },
    scripting: { executeScript: async ({ func, args: [request], target }) => {
      calls.push({ name: func.name, request, tabId: target.tabId })
      if (func.name === 'checkBossChatApplication') {
        if (request.kind === 'cancel') { operation.cancel_requested = true; operation.status = operation.status === 'pending' ? 'cancelled' : 'unknown'; return [{ result: { ok: true, data: { operation: { ...operation } } } }] }
        if (request.kind === 'transition') {
          const transitioned = transitionWon && operation.status !== request.status
          if (transitioned) operation.status = request.status
          return [{ result: { ok: true, data: { operation: { ...operation }, transitioned } } }]
        }
        if (lostBeforeSubmit && preparing) return [{ result: { ok: false, error: { code: 'execution_lost', message: '另一端已接管' } } }]
        return [{ result: { ok: true, data: { execution, operation: { ...operation } } } }]
      }
      if (func.name === 'replyBossChatPage') {
        if (request.phase === 'inspect') return [{ result: sourceDraft
          ? { ok: false, error: { code: 'draft_exists', message: '源页已有人工草稿', submissionState: 'not_submitted' } }
          : { ok: true, data: { peer_id: currentPeer, has_draft: false } } }]
        if (request.phase === 'prepare' && currentPeer !== request.peerId) return [{ result: { ok: false, error: { code: 'conversation_changed', message: '导航后仍不是目标招聘者', submissionState: 'not_submitted' } } }]
        if (request.phase === 'prepare') { preparing = true; await afterPrepare?.() }
        if (request.phase === 'submit' && submitError) throw new Error('端口断开')
        return [{ result: { ok: true, data: { status: request.phase === 'submit' ? 'submitted' : 'prepared', submissionState: request.phase === 'submit' ? 'submitted' : 'not_submitted' } } }]
      }
      return [{ result: { ok: true, data: { account: { platform: 'boss', platform_account_id: 'me-1' }, response: { code: 0, zpData: { result: [] } } } } }]
    } }
  }
  const dispatch = createBossChatDispatcher({ getChatTab: async () => ({ id: 11 }), navigateChat: async (tabId, peerId) => { calls.push({ name: 'navigateChat', tabId, peerId }); currentPeer = navigatedPeer } })
  return { calls, storage, operation, dispatch, setPeer(value) { currentPeer = value } }
}

test('独立回复仅CAS胜者准备与点击，重复请求只返回持久状态', async (t) => {
  const f = setup(t)
  const first = await f.dispatch('boss_chat_reply', payload, sender)
  assert.equal(first.data.submissionState, 'submitted')
  const second = await f.dispatch('boss_chat_reply', payload, sender)
  assert.equal(second.data.duplicate, true)
  assert.deepEqual(f.calls.filter((x) => x.name === 'replyBossChatPage').map((x) => x.request.phase), ['inspect', 'prepare', 'submit'])
  const loser = setup(t, { transitionWon: false })
  assert.equal((await loser.dispatch('boss_chat_reply', payload, sender)).data.duplicate, true)
  assert.equal(loser.calls.some((x) => x.name === 'replyBossChatPage'), false)
})

test('当前会话A无草稿时定位到B，导航后仍以稳定UID校验再准备与提交', async (t) => {
  const f = setup(t, { sourcePeer: 'hr-A' })
  const result = await f.dispatch('boss_chat_reply', payload, sender)
  assert.equal(result.data.submissionState, 'submitted')
  assert.deepEqual(f.calls.filter((x) => x.name === 'navigateChat'), [{ name: 'navigateChat', tabId: 11, peerId: payload.peerId }])
  const phases = f.calls.filter((x) => x.name === 'replyBossChatPage').map((x) => x.request.phase)
  assert.deepEqual(phases, ['inspect', 'inspect', 'prepare', 'submit'])
  const refused = setup(t, { sourcePeer: 'hr-A', navigatedPeer: 'hr-A' })
  assert.equal((await refused.dispatch('boss_chat_reply', payload, sender)).error.code, 'conversation_changed')
  assert.equal(refused.calls.some((x) => x.name === 'replyBossChatPage' && x.request.phase === 'submit'), false)
})

test('当前会话A有人工草稿时不导航、不填写、不提交目标B', async (t) => {
  const f = setup(t, { sourcePeer: 'hr-A', sourceDraft: '人工未发草稿' })
  assert.equal((await f.dispatch('boss_chat_reply', payload, sender)).error.code, 'draft_exists')
  assert.equal(f.calls.some((x) => x.name === 'navigateChat'), false)
  assert.equal(f.calls.some((x) => x.name === 'replyBossChatPage' && ['prepare', 'submit'].includes(x.request.phase)), false)
})

test('真实SW使用官方聊天路由定位目标并等待加载，路由不替代源身份核对', async (t) => {
  const f = setup(t, { sourcePeer: 'hr-A' })
  const event = () => ({ addListener() {}, removeListener() {} })
  const updated = new Set()
  const navigations = []
  let listener
  chrome.runtime = { getManifest: () => ({ version: '0.6.0' }), onMessage: { addListener(fn) { listener = fn } }, onInstalled: event(), onStartup: event() }
  chrome.tabs = {
    query: async () => [{ id: 11, url: 'https://www.zhipin.com/web/geek/chat?id=hr-A', status: 'complete' }],
    get: async () => ({ id: 11, status: 'complete' }),
    update: async (id, options) => {
      navigations.push({ id, ...options })
      f.setPeer(new URL(options.url).searchParams.get('id'))
      queueMicrotask(() => { for (const notify of updated) notify(id, { status: 'complete' }, { id, url: options.url, status: 'complete' }) })
      return { id, url: options.url, status: 'complete' }
    },
    onUpdated: { addListener(fn) { updated.add(fn) }, removeListener(fn) { updated.delete(fn) } }
  }
  chrome.windows = { onRemoved: event() }
  chrome.action = { onClicked: event() }
  await import(`../service-worker.js?chat-navigate-execution=${++sequence}`)
  const result = await new Promise((resolve) => listener({ channel: 'find-job-job-library-web-v1', action: 'boss_chat_reply', payload }, sender, resolve))
  assert.equal(result.data.submissionState, 'submitted')
  assert.deepEqual(navigations, [{ id: 11, url: 'https://www.zhipin.com/web/geek/chat?id=hr-1', active: false }])
  assert.equal(updated.size, 0)
  assert.deepEqual(f.calls.filter((x) => x.name === 'replyBossChatPage').map((x) => x.request.phase), ['inspect', 'inspect', 'prepare', 'submit'])
})

test('准备中取消立刻使操作失效，不等待原业务，返回后不点击', async (t) => {
  const entered = deferred(), released = deferred()
  const f = setup(t, { afterPrepare: async () => { entered.resolve(); await released.promise } })
  const sourceCancellation = deferred()
  const script = chrome.scripting.executeScript
  chrome.scripting.executeScript = async (options) => {
    if (options.func.name === 'replyBossChatPage' && options.args[0].phase === 'cancel') await sourceCancellation.promise
    return script(options)
  }
  const sending = f.dispatch('boss_chat_reply', payload, sender)
  await entered.promise
  const cancel = await Promise.race([f.dispatch('boss_chat_cancel', payload, sender), new Promise((_, reject) => setTimeout(() => reject(new Error('源页延迟阻塞了服务端取消')), 500))])
  assert.equal(cancel.data.operation.status, 'unknown')
  sourceCancellation.resolve()
  released.resolve()
  const result = await sending
  assert.equal(result.data.operation.status, 'cancelled')
  assert.equal(f.calls.some((x) => x.name === 'replyBossChatPage' && x.request.phase === 'submit'), false)
})

test('填写后执行权丢失明确未提交，提交注入断线则留unknown且不重发', async (t) => {
  const lost = setup(t, { lostBeforeSubmit: true })
  const rejected = await lost.dispatch('boss_chat_reply', payload, sender)
  assert.equal(rejected.error.code, 'execution_lost')
  assert.equal(lost.calls.some((x) => x.name === 'replyBossChatPage' && x.request.phase === 'submit'), false)
  const unknown = setup(t, { submitError: true })
  assert.equal((await unknown.dispatch('boss_chat_reply', payload, sender)).data.operation.status, 'unknown')
  assert.equal((await unknown.dispatch('boss_chat_reply', payload, sender)).data.duplicate, true)
  assert.equal(unknown.calls.filter((x) => x.name === 'replyBossChatPage' && x.request.phase === 'submit').length, 1)
})

test('扩展重建后账号暂停仍生效，非owner与暂停端不打开源标签页', async (t) => {
  const f = setup(t)
  f.storage['boss-chat-pause-v1:me-1'] = { code: 'rate_limited', message: '账号暂停' }
  let tabs = 0
  const rebuilt = createBossChatDispatcher({ getChatTab: async () => { tabs++; return { id: 11 } } })
  assert.equal((await rebuilt('boss_chat_list', payload, sender)).error.code, 'account_paused')
  assert.equal(tabs, 0)
  await rebuilt('boss_chat_resume', payload, sender)
  const script = chrome.scripting.executeScript
  chrome.scripting.executeScript = async (options) => options.func.name === 'checkBossChatApplication'
    ? [{ result: { ok: false, error: { code: 'execution_lost', message: '已接管' } } }] : script(options)
  assert.equal((await rebuilt('boss_chat_list', payload, sender)).error.code, 'execution_lost')
  assert.equal(tabs, 0)
})

test('真实SW入口取消越过被阻塞读取和排队发送，ping与取消不推进业务队列', async (t) => {
  const f = setup(t)
  const entered = deferred(), released = deferred()
  const originalScript = chrome.scripting.executeScript
  chrome.scripting.executeScript = async (options) => {
    if (options.func.name === 'readBossChatPage') { entered.resolve(); await released.promise }
    return originalScript(options)
  }
  const event = () => ({ addListener() {}, removeListener() {} })
  let listener
  chrome.runtime = { getManifest: () => ({ version: '0.6.0' }), onMessage: { addListener(fn) { listener = fn } }, onInstalled: event(), onStartup: event() }
  chrome.tabs = { query: async () => [{ id: 11, url: 'https://www.zhipin.com/web/geek/chat', status: 'complete' }], get: async () => ({ id: 11, status: 'complete' }) }
  chrome.windows = { onRemoved: event() }
  chrome.action = { onClicked: event() }
  await import(`../service-worker.js?chat-cancel-execution=${++sequence}`)
  const request = (action, body = payload) => new Promise((resolve) => listener({ channel: 'find-job-job-library-web-v1', action, payload: body }, sender, resolve))
  const reading = request('boss_chat_list')
  await entered.promise
  const sending = request('boss_chat_reply')
  const cancel = await Promise.race([request('boss_chat_cancel'), new Promise((_, reject) => setTimeout(() => reject(new Error('取消被业务队列阻塞')), 500))])
  assert.equal(cancel.ok, true)
  assert.equal(cancel.data.operation.status, 'cancelled')
  released.resolve()
  await reading
  assert.equal((await sending).error.code, 'operation_cancelled')
  assert.equal(f.calls.some((x) => x.name === 'replyBossChatPage' && x.request.phase === 'submit'), false)
})
