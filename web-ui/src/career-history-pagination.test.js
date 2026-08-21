import test from 'node:test'
import assert from 'node:assert/strict'
import {
  DEFAULT_HISTORY_PAGE_SIZE,
  HISTORY_PAGE_SIZE_OPTIONS,
  historyPageRange,
  normalizeHistoryPage,
  normalizeHistoryPageTarget,
  pageRequestUrl
} from './career-history-pagination.js'

test('会话列表默认每页十五条并提供四档页容量', () => {
  assert.equal(DEFAULT_HISTORY_PAGE_SIZE, 15)
  assert.deepEqual(HISTORY_PAGE_SIZE_OPTIONS, [10, 15, 20, 25])
  assert.equal(pageRequestUrl(1, DEFAULT_HISTORY_PAGE_SIZE), '/api/career/conversations?page=1&page_size=15')
})

test('分页响应修正非法页码并计算区间', () => {
  const page = normalizeHistoryPage({ items: [], page: 8, page_size: 10, total: 32, total_pages: 4 })
  assert.equal(page.page, 4)
  assert.deepEqual(historyPageRange({ page: 3, pageSize: 10, total: 32 }), { start: 21, end: 30 })
})

test('空列表区间保持为零', () => {
  assert.deepEqual(historyPageRange({ page: 1, pageSize: 10, total: 0 }), { start: 0, end: 0 })
})

test('指定页跳转会修正空值和越界页码', () => {
  assert.equal(normalizeHistoryPageTarget('', 4), 1)
  assert.equal(normalizeHistoryPageTarget(3, 4), 3)
  assert.equal(normalizeHistoryPageTarget(99, 4), 4)
  assert.equal(normalizeHistoryPageTarget(-2, 4), 1)
})
