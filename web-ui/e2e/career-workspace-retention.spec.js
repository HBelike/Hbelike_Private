import { test, expect } from '@playwright/test'
import { installPlatformFixtures, openFixtureConversation, mobileUserAgent } from './support/platform-fixtures.js'

const draft = '尚未发送的求职问题：请结合已有简历分析。'
async function prepare(page) {
  await installPlatformFixtures(page)
  await page.goto('/career')
  await openFixtureConversation(page)
  await expect(page.getByRole('textbox', { name: '求职咨询内容' })).toBeEnabled()
  await page.getByRole('textbox', { name: '求职咨询内容' }).fill(draft)
}
async function navigate(page, name) {
  const toggle = page.getByRole('button', { name: '打开导航', exact: true })
  if (await toggle.isVisible()) await toggle.click()
  await page.locator('#app-navigation .nav-item').filter({ hasText: name }).click()
}
async function retained(page) {
  await expect(page).toHaveURL(/\/career$/)
  await expect(page.locator('.chat-header h2')).toContainText('Agent 工程师求职准备')
  await expect(page.getByRole('textbox', { name: '求职咨询内容' })).toBeEnabled()
  await expect(page.getByRole('textbox', { name: '求职咨询内容' })).toHaveValue(draft)
  await expect(page.locator('.career-workspace')).toHaveClass(/has-context-rail/)
}

async function checkRetainedAttachment(page) {
  await prepare(page)
  async function openMaterials() {
    const tools = page.getByRole('button', { name: '资料与工具', exact: true })
    if (await tools.isVisible()) await tools.click()
    await page.locator('.active-context-chip').click()
    return page.getByRole('dialog', { name: '建立求职上下文', exact: true })
  }
  let sheet = await openMaterials()
  await sheet.getByRole('button', { name: '上传新简历', exact: true }).click()
  const bytes = '%PDF-1.4 draft-file-bytes-retained'
  await sheet.locator('input[type=file]').first().setInputFiles({ name: 'pending-resume.pdf', mimeType: 'application/pdf', buffer: Buffer.from(bytes) })
  await sheet.getByRole('button', { name: '关闭', exact: true }).click()
  await navigate(page, '技能库')
  await navigate(page, '求职助手')
  await retained(page)
  sheet = await openMaterials()
  await expect(sheet.locator('.file-drop')).toContainText('pending-resume.pdf')
  let uploaded = ''
  await page.route('**/api/career/candidate-profiles/parse', route => {
    uploaded = route.request().postDataBuffer().toString()
    return route.fulfill({ json: { resume_outline: '原始简历内容', source_character_count: 8, sections: [] } })
  })
  await sheet.getByRole('button', { name: '解析简历', exact: true }).click()
  await expect(sheet.getByRole('textbox', { name: '基准简历解析预览', exact: true })).toHaveValue('原始简历内容')
  expect(uploaded).toContain('pending-resume.pdf')
  expect(uploaded).toContain(bytes)
}

test('PC 求职工作区跨模块返回保留当前会话、资料与草稿', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await prepare(page)
  await navigate(page, '技能库')
  await expect(page).toHaveURL(/\/skills$/)
  await navigate(page, '求职助手')
  await retained(page)
  await navigate(page, '面经库')
  await page.goBack()
  await retained(page)
})

test('PC 待解析附件跨模块返回保留文件与原始字节', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await checkRetainedAttachment(page)
})

test('退出登录后重新登录不恢复上一登录会话的草稿', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await prepare(page)
  await navigate(page, '技能库')
  await page.locator('.account-breadcrumb').click()
  await page.getByRole('menuitem', { name: '个人信息 查看账户与权限', exact: true }).click()
  await page.getByRole('button', { name: '退出登录', exact: true }).click()
  await expect(page).toHaveURL(/\/login$/)
  await page.route('**/api/auth/login', route => route.fulfill({ json: { user: { id: 'second-user', display_name: '新登录用户', role: 'admin' } } }))
  await page.getByRole('tab', { name: '密码登录', exact: true }).click()
  await page.getByLabel('邮箱', { exact: true }).fill('second@example.com')
  await page.getByLabel('密码', { exact: true }).fill('fixture-password')
  await page.locator('.login-submit').click()
  await expect(page.locator('.career-workspace')).toBeVisible()
  await expect(page.getByRole('textbox', { name: '求职咨询内容' })).toHaveValue('')
  await expect(page.getByRole('textbox', { name: '求职咨询内容' })).toBeDisabled()
})

test.describe('手机求职工作区保留', () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, userAgent: mobileUserAgent })
  for (const feature of ['online-assessment', 'interview-master']) {
    test(`${feature} 说明页站内返回及系统返回均保留草稿`, async ({ page }) => {
      await prepare(page)
      let documentRequests = 0
      page.on('request', request => { if (request.isNavigationRequest() && request.resourceType() === 'document') documentRequests += 1 })
      const entry = page.locator(`.chat-material-button.${feature}`)
      await entry.scrollIntoViewIfNeeded()
      await entry.click()
      await expect(page.locator('.desktop-capability-notice')).toBeVisible()
      await page.getByRole('link', { name: '返回求职助手', exact: true }).click()
      await retained(page)
      expect(documentRequests).toBe(0)
      await page.locator(`.chat-material-button.${feature}`).click()
      await expect(page.locator('.desktop-capability-notice')).toBeVisible()
      await page.goBack()
      await retained(page)
    })
  }
  test('会话恢复失败仍保留当前草稿与资料', async ({ page }) => {
    await prepare(page)
    await navigate(page, '技能库')
    await page.route('**/api/career/conversations/conversation-1', route => route.fulfill({ status: 503, json: { detail: '暂时无法校准会话' } }))
    await navigate(page, '求职助手')
    await retained(page)
    await expect(page.locator('.career-error-toast')).toContainText('暂时无法校准会话')
  })
  test('待解析附件跨模块返回保留文件名和原始字节', async ({ page }) => {
    await checkRetainedAttachment(page)
  })
})
