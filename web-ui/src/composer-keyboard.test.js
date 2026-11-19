import assert from 'node:assert/strict'
import test from 'node:test'
import { createComposerHistory, shouldNavigateComposerHistory, shouldSubmitComposer } from './composer-keyboard.js'
import { isMobileBrowser } from './platform-capabilities.js'

test('中文组合输入和候选确认不发送消息', () => {
  assert.equal(shouldSubmitComposer({ key: 'Enter', isComposing: true }), false)
  assert.equal(shouldSubmitComposer({ key: 'Enter', keyCode: 229 }), false)
})

test('手机 Enter 换行，PC Enter 发送，修饰键不误发', () => {
  assert.equal(shouldSubmitComposer({ key: 'Enter' }, true), false)
  assert.equal(shouldSubmitComposer({ key: 'Enter' }, false), true)
  for (const modifier of ['shiftKey', 'ctrlKey', 'altKey', 'metaKey', 'defaultPrevented']) {
    assert.equal(shouldSubmitComposer({ key: 'Enter', [modifier]: true }), false)
  }
  assert.equal(shouldSubmitComposer({ key: 'a' }), false)
})

test('桌面能力取决于移动浏览器而不是窄窗口或触屏', () => {
  assert.equal(isMobileBrowser({ userAgent: 'Mozilla/5.0 Windows NT 10.0 Chrome/140.0', maxTouchPoints: 10 }), false)
  assert.equal(isMobileBrowser({ userAgent: 'Mozilla/5.0 iPhone Mobile/15E148 Safari/604.1' }), true)
  assert.equal(isMobileBrowser({ userAgent: 'Mozilla/5.0 Android 15 Chrome/140.0 Mobile' }), true)
  assert.equal(isMobileBrowser({ userAgent: '', userAgentData: { mobile: true } }), true)
})

function arrowEvent(key, value = '', caret = value.length, extra = {}) {
  return { key, currentTarget: { value, selectionStart: caret, selectionEnd: caret }, ...extra }
}

test('首行向上进入历史，多行编辑、选区和普通向下保留光标行为', () => {
  assert.equal(shouldNavigateComposerHistory(arrowEvent('ArrowUp', '草稿')), true)
  assert.equal(shouldNavigateComposerHistory(arrowEvent('ArrowUp', '第一行\n第二行')), false)
  assert.equal(shouldNavigateComposerHistory(arrowEvent('ArrowUp', '第一行\n第二行', 2)), true)
  assert.equal(shouldNavigateComposerHistory(arrowEvent('ArrowDown')), false)
  assert.equal(shouldNavigateComposerHistory(arrowEvent('ArrowDown'), false, true), true)
  assert.equal(shouldNavigateComposerHistory({ key: 'ArrowUp', currentTarget: { value: '选区', selectionStart: 0, selectionEnd: 2 } }), false)
})

test('历史切换避开手机、中文组合输入和修饰键，允许长按方向键', () => {
  assert.equal(shouldNavigateComposerHistory(arrowEvent('ArrowUp'), true), false)
  for (const property of ['isComposing', 'shiftKey', 'ctrlKey', 'altKey', 'metaKey', 'defaultPrevented']) {
    assert.equal(shouldNavigateComposerHistory(arrowEvent('ArrowUp', '', 0, { [property]: true })), false)
  }
  assert.equal(shouldNavigateComposerHistory(arrowEvent('ArrowUp', '', 0, { keyCode: 229 })), false)
  assert.equal(shouldNavigateComposerHistory(arrowEvent('ArrowUp', '', 0, { repeat: true })), true)
})

test('逐条回填、重复输入不去重、到头停留、向下恢复原草稿', () => {
  const history = createComposerHistory()
  const entries = ['第一条', '重复', '重复', '最后一条\n第二行']
  const move = key => history.move(key, '未发送草稿', entries)
  assert.equal(move('ArrowDown'), null)
  for (const text of [...entries].reverse()) assert.deepEqual(move('ArrowUp'), { text, atDraft: false })
  assert.deepEqual(move('ArrowUp'), { text: '第一条', atDraft: false })
  for (const text of [...entries.slice(1), '未发送草稿']) assert.equal(move('ArrowDown').text, text)
  assert.deepEqual(move('ArrowDown'), { text: '未发送草稿', atDraft: true })
})

test('无历史保持输入；编辑或切换会话重置后从最新历史重新开始', () => {
  const history = createComposerHistory()
  assert.equal(history.move('ArrowUp', '草稿', ['', '  ', null]), null)
  assert.equal(history.browsing, false)
  assert.equal(history.move('ArrowUp', '', ['旧输入']).text, '旧输入')
  history.reset()
  assert.equal(history.browsing, false)
  assert.equal(history.move('ArrowUp', '修改后的草稿', ['另一会话输入']).text, '另一会话输入')
  assert.deepEqual(history.move('ArrowDown', '', []), { text: '修改后的草稿', atDraft: true })
})
