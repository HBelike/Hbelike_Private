import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(
  new URL('./components/CareerAssistantPage.vue', import.meta.url),
  'utf8'
)

test('Kimi 预设使用官方中国区地址和精确模型 ID', () => {
  assert.match(source, /key: 'kimi'/)
  assert.match(source, /defaultModelId: 'kimi-k3'/)
  assert.match(source, /apiBaseUrl: 'https:\/\/api\.moonshot\.cn\/v1'/)
  assert.match(source, /websiteUrl: 'https:\/\/platform\.kimi\.com'/)
})

test('Kimi 预设默认开启官方支持的视觉与 Tool Calling 能力', () => {
  const option = source.match(/\{ key: 'kimi'[^\n]+\}/)?.[0]

  assert.ok(option, '应能定位 Kimi 服务商预设')
  assert.match(option, /vision: true/)
  assert.match(option, /tools: true/)
})

test('自定义 Moonshot 配置会提示并规范化 Kimi Model ID', () => {
  assert.match(source, /function normalizedModelId\(/)
  assert.match(source, /Kimi 模型 ID 将按官方标识保存为/)
})
