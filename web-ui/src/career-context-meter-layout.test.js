import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

test('上下文余量圆环位于模型选择器旁并提供无障碍状态', async () => {
  const page = await readFile(new URL('./components/CareerAssistantPage.vue', import.meta.url), 'utf8')
  const meter = await readFile(new URL('./components/CareerContextMeter.vue', import.meta.url), 'utf8')
  const meterIndex = page.indexOf('<CareerContextMeter')
  const modelIndex = page.indexOf('<select class="model-select"')
  assert.ok(meterIndex >= 0 && meterIndex < modelIndex)
  assert.match(meter, /role="status"/)
  assert.match(meter, /stroke-dasharray/)
  assert.match(meter, /正在整理上下文|正在估算上下文/)
})

test('页面切换会话与模型时使用递增请求号重新估算', async () => {
  const page = await readFile(new URL('./components/CareerAssistantPage.vue', import.meta.url), 'utf8')
  assert.match(page, /contextUsageRequestId/)
  assert.match(page, /loadContextUsage/)
  assert.match(page, /model_profile_id=/)
})
