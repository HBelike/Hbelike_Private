import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { replyBossChatPage } from '../boss-chat-reply-page.js'

function fixture({ uid = 'me-1', peer = 'hr-1', draft = '', challenge = false } = {}) {
  const calls = []
  const node = { getAttribute: (name) => name === 'data-peer-uid' ? peer : null }
  const input = { textContent: draft, focus() { calls.push('focus') }, closest: () => node, dispatchEvent() {}, getBoundingClientRect: () => ({ width: 100, height: 40 }) }
  const button = { click() { calls.push('click') }, getAttribute: () => null, getBoundingClientRect: input.getBoundingClientRect }
  const context = { window: { _PAGE: { uid }, getComputedStyle: () => ({ display: 'block', visibility: 'visible' }) }, location: new URL('https://www.zhipin.com/web/geek/chat?id=hr-1'),
    document: { querySelector: (selector) => selector === '#chat-input' ? input : selector === '.chat-op .btn-send' ? button : null, querySelectorAll: () => challenge ? [input] : [] },
    InputEvent: class {}, Event: class {}, Date }
  vm.createContext(context)
  return { calls, input, context, run: (payload) => JSON.parse(JSON.stringify(vm.runInContext(`(${replyBossChatPage.toString()})(${JSON.stringify(payload)})`, context))) }
}
const payload = { clientOperationId: 'confirmed-uuid', accountId: 'me-1', peerId: 'hr-1', text: '用户确认的回复', expiresAt: Date.now() + 30000 }

test('持续回复准备与提交分离，重复操作只返回状态，不按相同历史正文阻挡新操作', () => {
  const f = fixture()
  assert.equal(f.run({ ...payload, phase: 'prepare' }).data.status, 'prepared')
  assert.equal(f.calls.includes('click'), false)
  assert.equal(f.run({ ...payload, phase: 'submit' }).data.submissionState, 'submitted')
  const tombstone = f.context.window.__findJobBossChatOperationsV1[payload.clientOperationId]
  assert.equal('text' in tombstone, false)
  assert.equal('input' in tombstone, false)
  assert.equal(f.run({ ...payload, phase: 'submit' }).data.duplicate, true)
  assert.equal(f.calls.filter((x) => x === 'click').length, 1)
  f.input.textContent = ''
  assert.equal(f.run({ ...payload, clientOperationId: 'second-uuid', phase: 'prepare' }).ok, true)
  assert.equal(f.run({ ...payload, clientOperationId: 'second-uuid', phase: 'submit' }).ok, true)
  assert.equal(f.calls.filter((x) => x === 'click').length, 2)
})

test('取消先于准备保留短期墓碑，终止后清理正文引用且过期墓碑会清除', () => {
  const f = fixture()
  f.run({ clientOperationId: payload.clientOperationId, phase: 'cancel' })
  assert.equal(f.run({ ...payload, phase: 'prepare' }).ok, false)
  assert.equal(f.calls.length, 0)
  const key = '__findJobBossChatOperationsV1'
  assert.equal('text' in f.context.window[key][payload.clientOperationId], false)
  f.context.window[key].old = { expiresAt: Date.now() - 1, text: '过期正文', input: {} }
  f.run({ clientOperationId: 'another', phase: 'cancel' })
  assert.equal(f.context.window[key].old, undefined)
})

test('人工草稿、缺稳定招聘者身份、切号及验证码在填写前拒绝', () => {
  for (const config of [{ draft: '人工草稿' }, { peer: '' }, { peer: 'other' }, { uid: 'other' }, { challenge: true }]) {
    const f = fixture(config)
    assert.equal(f.run({ ...payload, phase: 'prepare' }).ok, false)
    assert.equal(f.calls.length, 0)
    assert.equal(f.input.textContent, config.draft || '')
  }
})

test('inspect只观察当前会话并保护草稿，URL中的目标不能取代当前peer身份', () => {
  const other = fixture({ peer: 'hr-A' })
  const inspected = other.run({ ...payload, phase: 'inspect' })
  assert.equal(inspected.ok, true)
  assert.equal(inspected.data.peer_id, 'hr-A')
  assert.equal(other.calls.length, 0)
  assert.equal(other.run({ ...payload, phase: 'prepare' }).error.code, 'conversation_changed')
  const drafted = fixture({ peer: 'hr-A', draft: '当前A的草稿' })
  assert.equal(drafted.run({ ...payload, phase: 'inspect' }).error.code, 'draft_exists')
  assert.equal(drafted.input.textContent, '当前A的草稿')
  assert.equal(drafted.calls.length, 0)
})

test('填写后取消、过期、人工编辑或切账号都不点击提交', () => {
  for (const mode of ['cancel', 'expired', 'edit', 'account']) {
    const f = fixture()
    f.run({ ...payload, phase: 'prepare' })
    if (mode === 'cancel') f.run({ clientOperationId: payload.clientOperationId, phase: 'cancel' })
    if (mode === 'edit') f.input.textContent = '人工修改后的草稿'
    if (mode === 'account') f.context.window._PAGE.uid = 'other'
    const result = f.run({ ...payload, expiresAt: mode === 'expired' ? Date.now() - 1 : payload.expiresAt, phase: 'submit' })
    assert.equal(result.ok, false)
    assert.equal(f.calls.includes('click'), false)
  }
})
