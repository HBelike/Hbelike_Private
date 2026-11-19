import { test, expect } from '@playwright/test'
import { installPlatformFixtures, openFixtureConversation } from './support/platform-fixtures.js'

async function expectFits(page) {
  for (const selector of ['.shell', '.main', '.career-workspace', '.career-chat-panel', '.history-pagination-shell']) {
    if (selector === '.history-pagination-shell' && !await page.locator(selector).count()) continue
    await expect.poll(() => page.locator(selector).evaluate(el => el.scrollWidth <= el.clientWidth + 1), { message: selector }).toBe(true)
  }
  await expect(page.locator('.send-button')).toBeInViewport({ ratio: 1 })
}

test('应用壳层 PC 导航与历史独立收起，跨模块、尺寸及刷新保留导航偏好', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await installPlatformFixtures(page)
  await page.goto('/career')
  await openFixtureConversation(page)
  const input = page.getByRole('textbox', { name: '求职咨询内容', exact: true })
  await input.fill('收起导航后继续编辑的草稿')
  await expect(page.locator('.sidebar')).toHaveCSS('width', '136px')
  await expect(page.locator('.career-history-panel')).toHaveCSS('width', '200px')
  const before = (await page.locator('.career-chat-panel').boundingBox()).width
  const toggle = page.getByRole('button', { name: '收起导航栏', exact: true })
  const sidebar = await page.locator('.sidebar').boundingBox()
  const button = await toggle.boundingBox()
  expect(button.x).toBeGreaterThanOrEqual(sidebar.x)
  expect(button.x + button.width).toBeLessThanOrEqual(sidebar.x + sidebar.width)
  expect(button.y + button.height).toBeLessThanOrEqual(sidebar.y + sidebar.height)
  expect(button.y).toBeGreaterThan(sidebar.height / 2)
  const gaps = await page.evaluate(() => {
    const history = document.querySelector('.career-history-panel').getBoundingClientRect()
    const chat = document.querySelector('.career-chat-panel').getBoundingClientRect()
    const nav = document.querySelector('.sidebar').getBoundingClientRect()
    return { navigation: history.x - nav.right, conversation: chat.x - history.right }
  })
  expect(gaps.navigation).toBeLessThanOrEqual(10)
  expect(gaps.conversation).toBeLessThanOrEqual(8)
  await toggle.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('button', { name: '展开导航栏', exact: true })).toBeFocused()
  await expect(page.locator('.sidebar')).toHaveCSS('width', '56px')
  await expect(page.locator('.sidebar-toggle')).toBeInViewport({ ratio: 1 })
  expect((await page.locator('.career-chat-panel').boundingBox()).width).toBeGreaterThan(before + 70)
  await page.getByRole('button', { name: '折叠会话历史', exact: true }).click()
  expect((await page.locator('.career-chat-panel').boundingBox()).width).toBeGreaterThan(before + 200)
  await expect(input).toHaveValue('收起导航后继续编辑的草稿')
  await expectFits(page)
  await page.getByRole('button', { name: '展开会话历史', exact: true }).click()
  await page.locator('#app-navigation').getByRole('button', { name: '技能库', exact: true }).click()
  await expect(page).toHaveURL(/\/skills$/)
  await page.locator('#app-navigation').getByRole('button', { name: '求职助手', exact: true }).click()
  await expect(input).toHaveValue('收起导航后继续编辑的草稿')
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.locator('.sidebar-toggle')).toBeHidden()
  await page.getByRole('button', { name: '打开导航', exact: true }).click()
  await expect(page.locator('#app-navigation .nav-copy').first()).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(input).toHaveValue('收起导航后继续编辑的草稿')
  await page.setViewportSize({ width: 1440, height: 900 })
  await expect(page.locator('.sidebar')).toHaveCSS('width', '56px')
  await expectFits(page)
  await page.reload()
  await expect(page.locator('.sidebar')).toHaveCSS('width', '56px')
  await page.getByRole('button', { name: '展开导航栏', exact: true }).click()
  await expect(page.locator('.sidebar')).toHaveCSS('width', '136px')
})

test('PC 多尺寸及双主题下三栏不会挤出聊天和发送按钮', async ({ page }) => {
  await installPlatformFixtures(page)
  await page.addInitScript(() => localStorage.setItem('career-assistant-context-rail-width', '760'))
  await page.goto('/career')
  await openFixtureConversation(page)
  await expect(page.locator('.career-workspace')).toHaveClass(/has-context-rail/)
  for (const theme of ['blue', 'green']) {
    await page.evaluate(theme => { document.documentElement.dataset.uiTheme = theme }, theme)
    for (const [width, height] of [[900,420],[1024,600],[1093,614],[1280,720],[1440,900],[1584,900],[1920,1080],[2560,1440]]) {
      await page.setViewportSize({ width, height })
      await expectFits(page)
      const title = await page.locator('.chat-header h2').evaluate(el => ({ height: el.getBoundingClientRect().height, lineHeight: Number.parseFloat(getComputedStyle(el).lineHeight) }))
      expect(title.height).toBeLessThanOrEqual(title.lineHeight + 1)
      if (width >= 1584) expect((await page.locator('.career-chat-panel').boundingBox()).width).toBeGreaterThanOrEqual(639)
    }
  }
})

test('窄历史栏在大量会话下支持前后翻页和直接跳页', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 })
  await installPlatformFixtures(page)
  await page.route('**/api/career/conversations?*', route => {
    const url = new URL(route.request().url())
    const current = Number(url.searchParams.get('page')) || 1
    const size = Number(url.searchParams.get('page_size')) || 5
    return route.fulfill({ json: { items: [{ id: 'conversation-1', title: `第 ${current} 页的求职咨询`, updated_at: '2026-11-09T06:00:00Z' }], page: current, page_size: size, total: 1000, total_pages: Math.ceil(1000 / size) } })
  })
  await page.goto('/career')
  await expect(page.locator('.conversation-item-main')).toContainText('第 1 页')
  await expectFits(page)
  await page.locator('.history-pagination .btn-next').click()
  await expect(page.locator('.conversation-item-main')).toContainText('第 2 页')
  const jump = page.locator('.history-pagination .el-pagination__jump input')
  await jump.fill('99')
  await jump.press('Enter')
  await expect(page.locator('.conversation-item-main')).toContainText('第 99 页')
  await expectFits(page)
})
