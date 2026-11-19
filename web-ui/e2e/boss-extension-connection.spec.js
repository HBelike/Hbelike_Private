import { test, expect } from '@playwright/test'

const fixtureUrl = '/e2e/fixtures/job-search-card.html'

async function installConnectionHarness(page, options = {}) {
  await page.addInitScript((options) => {
    const state = window.extensionHarness = {
      mode: options.mode || 'disconnected', delay: options.delay || 0,
      pingRequests: [], searchRequests: [], heldResponses: [], holdFirst: Boolean(options.holdFirst)
    }
    const reply = (request, ok, data, error) => window.postMessage({
      channel: 'find-job-job-library-extension-v1', requestId: request.requestId, ok, data, error
    }, window.location.origin)
    window.addEventListener('message', (event) => {
      const request = event.data
      if (request?.channel !== 'find-job-job-library-web-v1') return
      if (request.action === 'ping') {
        state.pingRequests.push(request.requestId)
        if (state.mode === 'silent') return
        const mode = state.mode
        const respond = () => mode === 'error'
          ? reply(request, false, null, { code: 'login_required', message: 'BOSS 页面需要登录，请先完成登录。' })
          : reply(request, true, { connected: mode === 'connected', version: '0.4.0' })
        if (state.holdFirst && state.pingRequests.length === 1) state.heldResponses.push(respond)
        else setTimeout(respond, state.delay)
        return
      }
      if (request.action === 'list_cities') return reply(request, true, { cities: [{ code: '101210100', name: '杭州' }] })
      if (request.action === 'search_jobs') {
        state.searchRequests.push(request.payload)
        return reply(request, true, { jobs: [], hasMore: false })
      }
      return reply(request, true, {})
    })
  }, options)
}

async function noAutomaticInstallation(page) {
  await expect(page.locator('[aria-modal="true"]')).toHaveCount(0)
}

test('真实检测超时与手动搜索失败都不会自动弹出安装说明', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await installConnectionHarness(page, { mode: 'silent' })
  await page.goto(fixtureUrl)
  await expect(page.getByRole('button', { name: '重新检测', exact: true })).toBeVisible({ timeout: 12000 })
  await noAutomaticInstallation(page)
  expect(await page.evaluate(() => window.extensionHarness.pingRequests.length)).toBeGreaterThanOrEqual(2)
  await page.getByRole('button', { name: '搜索真实岗位', exact: true }).click()
  await expect(page.getByRole('button', { name: '重新检测', exact: true })).toBeEnabled({ timeout: 12000 })
  await noAutomaticInstallation(page)
  expect(await page.evaluate(() => window.extensionHarness.searchRequests.length)).toBe(0)
})

test('已安装助手的业务错误与延迟不被当作需要安装', async ({ page }) => {
  await installConnectionHarness(page, { mode: 'error', delay: 120 })
  await page.goto(fixtureUrl)
  await expect(page.getByRole('button', { name: '重新检测', exact: true })).toBeVisible()
  await expect(page.locator('.job-alert')).toContainText('BOSS 页面需要登录')
  await noAutomaticInstallation(page)
  await page.getByRole('button', { name: '搜索真实岗位', exact: true }).click()
  await expect(page.getByRole('button', { name: '重新检测', exact: true })).toBeEnabled()
  await noAutomaticInstallation(page)
})

test('初始未响应后收到ready通知会重新检测并恢复连接', async ({ page }) => {
  await installConnectionHarness(page, { mode: 'silent' })
  await page.goto(fixtureUrl)
  await expect(page.getByRole('button', { name: '重新检测', exact: true })).toBeVisible({ timeout: 12000 })
  const before = await page.evaluate(() => window.extensionHarness.pingRequests.length)
  await page.evaluate(() => {
    window.extensionHarness.mode = 'connected'
    window.postMessage({ channel: 'find-job-job-library-extension-v1', type: 'ready' }, window.location.origin)
  })
  await expect(page.locator('.connection-badge')).toContainText('已连接')
  expect(await page.evaluate(() => window.extensionHarness.pingRequests.length)).toBeGreaterThan(before)
  await noAutomaticInstallation(page)
})

test('旧检测的迟到失败不会覆盖新连接成功', async ({ page }) => {
  await installConnectionHarness(page, { mode: 'disconnected', holdFirst: true })
  await page.goto(fixtureUrl)
  await expect.poll(() => page.evaluate(() => window.extensionHarness.heldResponses.length)).toBe(1)
  await page.evaluate(() => {
    window.extensionHarness.mode = 'connected'
    window.postMessage({ channel: 'find-job-job-library-extension-v1', type: 'ready' }, window.location.origin)
  })
  await expect(page.locator('.connection-badge')).toContainText('已连接')
  await page.evaluate(async () => {
    window.extensionHarness.heldResponses.splice(0).forEach((respond) => respond())
    // 等待消息事件及渲染结算，避免在迟到回复真正被处理前就断言。
    await new Promise(requestAnimationFrame)
    await new Promise(requestAnimationFrame)
  })
  await expect(page.locator('.connection-badge')).toContainText('已连接')
  await noAutomaticInstallation(page)
})

test('连接断开只显示重新检测，恢复后不重放已执行的岗位搜索', async ({ page }) => {
  await installConnectionHarness(page, { mode: 'connected' })
  await page.goto(fixtureUrl)
  await expect.poll(() => page.evaluate(() => window.extensionHarness.searchRequests.length)).toBe(1)
  await page.evaluate(() => {
    window.extensionHarness.mode = 'disconnected'
    window.postMessage({ channel: 'find-job-job-library-extension-v1', type: 'disconnected' }, window.location.origin)
  })
  await expect(page.getByRole('button', { name: '重新检测', exact: true })).toBeVisible()
  await noAutomaticInstallation(page)
  await page.evaluate(() => {
    window.extensionHarness.mode = 'connected'
    window.postMessage({ channel: 'find-job-job-library-extension-v1', type: 'ready' }, window.location.origin)
  })
  await expect(page.locator('.connection-badge')).toContainText('已连接')
  expect(await page.evaluate(() => window.extensionHarness.searchRequests.length)).toBe(1)
  await noAutomaticInstallation(page)
})

test('重复重新检测合并请求，顶部状态按钮只检测不打开安装', async ({ page }) => {
  await installConnectionHarness(page)
  await page.goto(fixtureUrl)
  await expect(page.getByRole('button', { name: '重新检测', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '重新检测', exact: true })).toBeEnabled()
  await page.evaluate(() => {
    window.extensionHarness.mode = 'connected'
    window.extensionHarness.delay = 300
    window.extensionHarness.pingRequests = []
    const retry = [...document.querySelectorAll('button')].find((button) => button.textContent.trim() === '重新检测')
    retry.click(); retry.click(); retry.click()
    document.querySelector('.connection-badge').click()
  })
  await expect(page.locator('.connection-badge')).toContainText('已连接')
  expect(await page.evaluate(() => window.extensionHarness.pingRequests.length)).toBe(1)
  await noAutomaticInstallation(page)
})

test('只有用户主动查看安装说明才打开窗口，PC与手机无横向溢出', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await installConnectionHarness(page)
  await page.goto(fixtureUrl)
  await expect(page.getByRole('button', { name: '查看安装说明', exact: true })).toBeVisible()
  await noAutomaticInstallation(page)
  await page.getByRole('button', { name: '查看安装说明', exact: true }).click()
  await expect(page.locator('[aria-modal="true"]')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.getByRole('button', { name: '关闭安装说明', exact: true }).click()
  await noAutomaticInstallation(page)
})
