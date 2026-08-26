import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

test('我的求职记忆是独立路由并展示六个中文分组', async () => {
  const app = await readFile(new URL('./App.vue', import.meta.url), 'utf8')
  const page = await readFile(new URL('./components/CareerMemoryPage.vue', import.meta.url), 'utf8')
  assert.match(app, /\/career\/memories/)
  assert.match(page, /我的求职记忆/)
  assert.match(page, /CAREER_MEMORY_TYPES/)
  assert.match(page, /确认/)
  assert.match(page, /修正/)
  assert.match(page, /停用/)
  assert.match(page, /删除/)
})

test('会话页提供来源抽屉和两种删除语义', async () => {
  const page = await readFile(new URL('./components/CareerAssistantPage.vue', import.meta.url), 'utf8')
  const drawer = await readFile(new URL('./components/CareerMemoryUsageDrawer.vue', import.meta.url), 'utf8')
  assert.match(page, /查看本回答使用的求职记忆/)
  assert.match(page, /仅删除对话/)
  assert.match(page, /删除并遗忘本对话记忆/)
  assert.match(drawer, /本回答实际使用的长期求职信息/)
})
