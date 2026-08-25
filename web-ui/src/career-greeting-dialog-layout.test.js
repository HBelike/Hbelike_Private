import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const componentUrl = new URL('./components/CareerGreetingDialog.vue', import.meta.url)

test('一键打招呼桌面弹窗使用百分之八十视口高度', async () => {
  const source = await readFile(componentUrl, 'utf8')

  assert.match(source, /height: 80dvh/)
  assert.match(source, /place-items: center/)
})

test('发送进度记录失败详情并支持选择后重发', async () => {
  const source = await readFile(componentUrl, 'utf8')

  assert.match(source, /尝试次数/)
  assert.match(source, /最近处理/)
  assert.match(source, /重新发送失败项/)
  assert.match(source, /retryGreetingMessage/)
  assert.match(source, /findGreetingFailureAction/)
  assert.match(source, /failure-action-bar/)
  assert.match(source, /重新发送失败项/)
  assert.match(source, /ensureGreetingExtensionReady/)
  assert.match(source, /requireRetryCapability/)
  assert.match(source, /installDialogOpen\.value = true/)
})

test('审核弹窗调用真实招呼语接口并移除虚假 humanizer 标签', async () => {
  const source = await readFile(componentUrl, 'utf8')

  assert.match(source, /requestGreeting/)
  assert.match(source, /generateGreetingBatch/)
  assert.match(source, /previousMessage:\s*previousMessage/)
  assert.match(source, /事实证据已校验/)
  assert.match(source, /DeepSeek V4 Pro/)
  assert.doesNotMatch(source, /humanizer 已检查|<strong>humanizer<\/strong>/)
})
