import { test, expect } from '@playwright/test'
import { installPlatformFixtures, openFixtureConversation } from './support/platform-fixtures.js'

async function prepare(page, options) {
  await page.setViewportSize({ width: 1440, height: 900 })
  await installPlatformFixtures(page, options)
  await page.goto('/career')
  await openFixtureConversation(page)
  await expect(page.getByRole('textbox', { name: '求职咨询内容' })).toBeEnabled()
}

test('PC 输入卡片的两个资料按钮进入对应表单，模型设置继续可用', async ({ page }) => {
  await prepare(page)
  const card = page.locator('.composer-card')
  await expect(card.getByRole('button', { name: '简历已添加', exact: true })).toBeVisible()
  await card.getByRole('button', { name: '目标岗位已添加', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '建立求职上下文', exact: true })
  await expect(dialog.getByRole('textbox', { name: '职位信息正文', exact: true })).toBeVisible()
  await dialog.getByRole('button', { name: '关闭', exact: true }).click()
  await card.getByRole('button', { name: '简历已添加', exact: true }).click()
  await expect(dialog.getByRole('button', { name: '保留当前简历', exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
  const target = await card.getByRole('button', { name: '目标岗位已添加', exact: true }).boundingBox()
  const config = await card.getByRole('button', { name: '模型配置', exact: true }).boundingBox()
  expect(config.x).toBeGreaterThanOrEqual(target.x + target.width)
  expect(Math.abs(config.y - target.y)).toBeLessThan(2)
  await expect(card.locator('.composer-submit-tools .composer-settings')).toHaveCount(0)
  await card.getByRole('button', { name: '模型配置', exact: true }).click()
  await card.getByRole('button', { name: '模型连接', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '模型与连接管理', exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
  const bounds = await card.boundingBox()
  for (const selector of ['textarea', '.model-select', '.send-button']) {
    const box = await card.locator(selector).boundingBox()
    expect(box.x).toBeGreaterThanOrEqual(bounds.x)
    expect(box.x + box.width).toBeLessThanOrEqual(bounds.x + bounds.width)
    expect(box.y + box.height).toBeLessThanOrEqual(bounds.y + bounds.height)
  }
})

test('快捷提问只填入文本，保留附件并通过原入口发送', async ({ page }) => {
  await prepare(page)
  let requests = 0
  let body = ''
  await page.route('**/api/career/conversations/conversation-1/turns-with-materials', route => {
    requests += 1
    body = route.request().postDataBuffer().toString()
    return route.fulfill({ status: 503, json: { detail: '模拟发送失败' } })
  })
  const chooser = page.waitForEvent('filechooser')
  await page.getByRole('button', { name: '添加附件', exact: true }).click()
  await (await chooser).setFiles({ name: 'career-draft.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 composer attachment') })
  await expect(page.locator('.composer-attachment')).toContainText('career-draft.pdf')
  await page.getByRole('button', { name: '准备一场面试', exact: true }).click()
  const input = page.getByRole('textbox', { name: '求职咨询内容' })
  await expect(input).toHaveValue('请结合目标岗位，帮我准备一场技术面试。')
  await expect(input).toBeFocused()
  expect(requests).toBe(0)
  await expect(page.locator('.composer-attachment')).toContainText('career-draft.pdf')
  await page.getByRole('button', { name: '发送', exact: true }).click()
  await expect.poll(() => requests).toBe(1)
  expect(body).toContain('career-draft.pdf')
  expect(body).toContain('%PDF-1.4 composer attachment')
  await expect(page.locator('.career-error-toast')).toContainText('模拟发送失败')
})

test('导航采用统一图标并收纳低频模块，普通用户保持权限边界', async ({ page }) => {
  await prepare(page)
  const nav = page.locator('#app-navigation')
  await expect(nav.getByRole('button', { name: '工作台', exact: true })).toBeHidden()
  await expect(nav.getByRole('button', { name: '技能库', exact: true }).locator('svg')).toBeVisible()
  await nav.getByRole('button', { name: '更多模块', exact: true }).click()
  await expect(nav.getByRole('button', { name: '工作台', exact: true })).toBeVisible()
  await expect(nav.getByRole('button', { name: '管理台', exact: true })).toBeVisible()
  await nav.getByRole('button', { name: '更多模块', exact: true }).click()
  await expect(nav.getByRole('button', { name: '工作台', exact: true })).toBeHidden()
  await installPlatformFixtures(page, { role: 'user' })
  await page.reload()
  await openFixtureConversation(page)
  await expect(page.getByRole('button', { name: '模型配置', exact: true })).toBeHidden()
  await nav.getByRole('button', { name: '更多模块', exact: true }).click()
  await expect(nav.getByRole('button', { name: '工作台', exact: true })).toBeVisible()
  await expect(nav.getByRole('button', { name: '管理台', exact: true })).toBeHidden()
})
