import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

import { normalizeAppRoute } from './navigation-access.js'

test('模型上下文页面只出现在管理台并展示不可修改的95%硬限制', async () => {
  const admin = await readFile(new URL('./components/AdminConsolePage.vue', import.meta.url), 'utf8')
  const panel = await readFile(new URL('./components/AdminModelContextPanel.vue', import.meta.url), 'utf8')
  assert.match(admin, /\/admin\/model-context/)
  assert.match(panel, /上下文容量/)
  assert.match(panel, /压缩触发比例/)
  assert.match(panel, /压缩目标比例/)
  assert.match(panel, /95%/)
  assert.equal(normalizeAppRoute('/admin/model-context'), '/admin/model-context')
})

test('策略面板在浏览器和服务端同时校验核心大小关系', async () => {
  const panel = await readFile(new URL('./components/AdminModelContextPanel.vue', import.meta.url), 'utf8')
  assert.match(panel, /outputTokens \* 2 > windowTokens/)
  assert.match(panel, /target >= trigger/)
  assert.match(panel, /context_window_source: 'admin'/)
})
