import { test, expect } from '@playwright/test'
import { installPlatformFixtures } from './support/platform-fixtures.js'

test('PC 求职助手真实应用入口进入招聘沟通，刷新子路由仍可查看历史空态', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await installPlatformFixtures(page, { role: 'member' })
  await page.route('**/api/career/external-chat/accounts', (route) => route.fulfill({ json: { accounts: [] } }))
  await page.addInitScript(() => {
    window.addEventListener('message', (event) => {
      const message = event.data
      if (message?.channel !== 'find-job-job-library-web-v1') return
      window.postMessage({ channel: 'find-job-job-library-extension-v1', requestId: message.requestId, ok: true, data: { connected: false, capabilities: [] } }, window.location.origin)
    })
  })
  await page.goto('/career')
  await page.getByRole('button', { name: '招聘沟通', exact: true }).click()
  await expect(page).toHaveURL(/\/career\/messages$/)
  await expect(page.locator('#career-inbox-title')).toHaveText('招聘沟通')
  await expect(page.getByText('还没有已保存的会话')).toBeVisible()
  await expect(page.getByText('浏览器助手未连接', { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.locator('#career-inbox-title')).toBeVisible()
  await expect(page.getByText('还没有已保存的会话')).toBeVisible()
  await page.getByRole('button', { name: '返回求职助手' }).click()
  await expect(page).toHaveURL(/\/career$/)
  await expect(page.getByRole('button', { name: '招聘沟通', exact: true })).toBeVisible()
})
