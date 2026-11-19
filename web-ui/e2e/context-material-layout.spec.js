import { test, expect } from '@playwright/test'
import { installPlatformFixtures, openFixtureConversation, mobileUserAgent } from './support/platform-fixtures.js'

async function openTarget(page) {
  await installPlatformFixtures(page)
  await page.goto('/career')
  await openFixtureConversation(page)
  const tools = page.getByRole('button', { name: '资料与工具', exact: true })
  if (await tools.isVisible()) await tools.click()
  await page.locator('.active-context-chip').click()
  const dialog = page.getByRole('dialog', { name: '建立求职上下文', exact: true })
  await dialog.getByRole('button', { name: '保留当前简历', exact: true }).click()
  return dialog
}

test('PC 资料表单仍保留双列和可见操作', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const dialog = await openTarget(page)
  expect(await dialog.locator('.target-meta-grid').evaluate(el => getComputedStyle(el).gridTemplateColumns.split(' ').length)).toBe(2)
  await expect(dialog.getByRole('button', { name: '确认岗位并继续', exact: true })).toBeInViewport({ ratio: 1 })
})

test.describe('紧凑资料 Sheet', () => {
  test.use({ isMobile: true, hasTouch: true, userAgent: mobileUserAgent })
  for (const [width, height] of [[360,800],[390,844],[390,430],[666,1828]]) {
    test(`${width}×${height} 正文高度受限，次操作对齐，主操作独占一行`, async ({ page }) => {
      await page.setViewportSize({ width, height })
      const dialog = await openTarget(page)
      expect(await dialog.locator('.jd-editor textarea').evaluate(el => el.getBoundingClientRect().height)).toBeLessThanOrEqual(240)
      const previous = await dialog.getByRole('button', { name: '上一步', exact: true }).boundingBox()
      const skip = await dialog.getByRole('button', { name: '保留当前岗位', exact: true }).boundingBox()
      expect(Math.abs(previous.y - skip.y)).toBeLessThan(2)
      expect(Math.abs(previous.width - skip.width)).toBeLessThan(2)
      const primary = dialog.getByRole('button', { name: '确认岗位并继续', exact: true })
      await expect(primary).toBeInViewport({ ratio: 1 })
      expect((await primary.boundingBox()).width).toBeGreaterThan(previous.width * 1.9)
      expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true)
      await page.screenshot({ path: `../tmp/regression-20261109/material-layout-${width}x${height}.png`, animations: 'disabled' })
    })
  }
})
