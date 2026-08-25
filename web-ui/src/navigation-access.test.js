import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

import {
  DEFAULT_APP_ROUTE,
  buildNavigationFallback,
  canAccessNavigationItem,
  normalizeAppRoute
} from './navigation-access.js'

const careerItem = {
  moduleKey: 'career_assistant',
  path: '/career',
  enabled: true
}

test('求职助手是默认入口，未知地址也回到求职助手', () => {
  assert.equal(DEFAULT_APP_ROUTE, '/career')
  assert.equal(normalizeAppRoute('/', ['/review']), '/career')
  assert.equal(normalizeAppRoute('/missing', ['/review']), '/career')
})

test('应用侧栏和登录成功流程都以求职助手为第一入口', async () => {
  const source = await readFile(new URL('./App.vue', import.meta.url), 'utf8')
  const careerIndex = source.indexOf("{ moduleKey: 'career_assistant'")
  const workbenchIndex = source.indexOf("{ moduleKey: 'workbench'")

  assert.ok(careerIndex >= 0)
  assert.ok(workbenchIndex > careerIndex)
  assert.match(source, /isAuthRoute\(currentRoute\.value\) \? DEFAULT_APP_ROUTE : currentRoute\.value/)
})

test('工作台子路由仍按现有目录规范化', () => {
  assert.equal(normalizeAppRoute('/review/article', ['/review', '/review/article']), '/review/article')
  assert.equal(normalizeAppRoute('/review/missing', ['/review', '/review/article']), '/review')
})

test('管理员无视模块开关并始终拥有页面访问权', () => {
  assert.equal(
    canAccessNavigationItem(careerItem, { key: 'career_assistant', enabled: false, accessible: false }, 'admin'),
    true
  )
})

test('普通用户继续服从管理员保存的模块开关', () => {
  assert.equal(
    canAccessNavigationItem(careerItem, { key: 'career_assistant', enabled: false, accessible: false }, 'user'),
    false
  )
  assert.equal(
    canAccessNavigationItem(careerItem, { key: 'career_assistant', enabled: true, accessible: true }, 'user'),
    true
  )
})

test('配置读取失败时管理员保留全部页面，普通用户保持拒绝访问', () => {
  const items = [careerItem, { moduleKey: 'admin_console', path: '/admin/modules', enabled: true }]
  assert.deepEqual(buildNavigationFallback(items, 'admin'), [
    { key: 'career_assistant', enabled: true, accessible: true },
    { key: 'admin_console', enabled: true, accessible: true }
  ])
  assert.deepEqual(buildNavigationFallback(items, 'user'), [
    { key: 'career_assistant', enabled: false, accessible: false },
    { key: 'admin_console', enabled: false, accessible: false }
  ])
})

test('导航配置失败时展示可重试错误，不伪装成管理员关闭全部模块', async () => {
  const source = await readFile(new URL('./App.vue', import.meta.url), 'utf8')

  assert.match(source, /const navigationError = ref\(''\)/)
  assert.match(source, /无法读取可用模块，请检查网络后重试/)
  assert.match(source, /重新加载模块/)
  assert.match(source, /retryNavigationConfig/)
})
