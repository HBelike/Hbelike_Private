import test from 'node:test'
import assert from 'node:assert/strict'

import { formatWeekRange } from './article-time.js'

test('周榜结束日期转换为完整七天数据周期', () => {
  assert.equal(formatWeekRange('2026-08-14'), '2026-08-08 至 2026-08-14')
})

test('周榜周期兼容缺失和旧格式值', () => {
  assert.equal(formatWeekRange(null), '周期未知')
  assert.equal(formatWeekRange('旧周期'), '旧周期')
})
