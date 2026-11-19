import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const jobCanvasVue = readFileSync(new URL('./components/CareerJobCanvas.vue', import.meta.url), 'utf8')

test('职业匹配度说明保留在指标卡内部且不会覆盖相邻卡片', () => {
  assert.match(jobCanvasVue, /\.job-match-grid em\{[^}]*position:static/)
  assert.match(jobCanvasVue, /\.job-match-grid em\{[^}]*display:-webkit-box/)
  assert.match(jobCanvasVue, /\.job-match-grid em\{[^}]*-webkit-line-clamp:2/)
  assert.doesNotMatch(jobCanvasVue, /\.job-match-grid em\{[^}]*top:calc\(100%/)
})
