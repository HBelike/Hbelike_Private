import { test, expect } from '@playwright/test'
import { installPlatformFixtures, openFixtureConversation, mobileUserAgent } from './support/platform-fixtures.js'

const text = '岗位职责：负责业务系统开发、接口设计、服务维护与团队协作。任职要求：熟悉 Python、SQL 和自动化测试，能够定位线上问题。'
const preview = { job_text: text, company_name: '导入公司', role_name: 'Python 工程师', source_kind: 'url', source_label: 'jobs.example.org', source_url: 'https://jobs.example.org/python', fetched_at: '2026-11-09T06:00:00Z', availability: 'unknown', notices: ['请核对是否仍在招。'] }

async function openSheet(page) {
  await installPlatformFixtures(page)
  page.on('dialog', dialog => dialog.accept())
  await page.goto('/career')
  await openFixtureConversation(page)
  const tools = page.getByRole('button', { name: '资料与工具', exact: true })
  if (await tools.isVisible()) await tools.click()
  await page.locator('.active-context-chip').click()
  return page.getByRole('dialog', { name: '建立求职上下文', exact: true })
}
async function openJob(page) {
  const sheet = await openSheet(page)
  await sheet.getByRole('button', { name: '保留当前简历', exact: true }).click()
  return sheet
}
async function parseLink(sheet) {
  await sheet.getByRole('button', { name: '职位链接', exact: true }).click()
  await sheet.getByLabel('职位详情链接').fill(preview.source_url)
  await sheet.getByRole('button', { name: '解析链接', exact: true }).click()
}

test('PC 链接导入先预览，修改后显式确认才保存', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const sheet = await openJob(page)
  let saved = null
  await page.route('**/api/career/target-role-profiles/parse-url', route => route.fulfill({ json: preview }))
  await page.route('**/api/career/target-role-profiles', route => {
    saved = route.request().postDataJSON()
    return route.fulfill({ json: { ...saved, id: 'new-role', version: 1, requirements: [] } })
  })
  await parseLink(sheet)
  await expect(sheet.getByRole('textbox', { name: '职位信息正文', exact: true })).toHaveValue(text)
  await expect(sheet.locator('.job-import-source')).toContainText('待核实')
  expect(saved).toBeNull()
  const edited = text+'\n人工核对：不接受虚构经历。'
  await sheet.getByRole('textbox', { name: '职位信息正文', exact: true }).fill(edited)
  await sheet.getByRole('button', { name: '确认岗位并继续', exact: true }).click()
  await expect(sheet.locator('.context-review')).toBeVisible()
  expect(saved.job_text).toBe(edited)
  expect(saved.company_name).toBe('导入公司')
  expect(saved.source_kind).toBe('url')
})

test('PC 解析失败保留正文和链接，可切换截图入口', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const sheet = await openJob(page)
  await sheet.getByRole('textbox', { name: '职位信息正文', exact: true }).fill(text)
  let requests = 0
  await page.route('**/api/career/target-role-profiles/parse-url', route => {
    requests += 1
    return route.fulfill({ status: 422, json: { detail: '页面需要登录，请上传岗位截图或粘贴正文。' } })
  })
  await parseLink(sheet)
  await expect(sheet.getByRole('alert')).toContainText('上传岗位截图')
  await expect(sheet.getByRole('textbox', { name: '职位信息正文', exact: true })).toHaveValue(text)
  await expect(sheet.getByLabel('职位详情链接')).toHaveValue(preview.source_url)
  expect(requests).toBe(1)
  await sheet.getByRole('button', { name: '截图或文件', exact: true }).click()
  await expect(sheet.getByRole('button', { name: '上传岗位截图或文件', exact: true })).toBeVisible()
  await expect(sheet.getByRole('textbox', { name: '职位信息正文', exact: true })).toHaveValue(text)
})

test('PC 无法提取新岗位名称时清空旧绑定名称，等待用户补充', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const sheet = await openJob(page)
  await page.route('**/api/career/target-role-profiles/parse-url', route => route.fulfill({ json: { ...preview, company_name: '', role_name: '' } }))
  await parseLink(sheet)
  await expect(sheet.locator('.target-meta-grid input').first()).toHaveValue('')
  await expect(sheet.locator('.target-meta-grid input').last()).toHaveValue('')
  await expect(sheet.getByRole('button', { name: '确认岗位并继续', exact: true })).toBeDisabled()
})

test('PC 链接等待期间的新编辑不被迟到响应覆盖', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const sheet = await openJob(page)
  let release
  const hold = new Promise(resolve => { release = resolve })
  await page.route('**/api/career/target-role-profiles/parse-url', async route => { await hold; await route.fulfill({ json: preview }) })
  await parseLink(sheet)
  await expect(sheet.getByRole('button', { name: '正在读取…', exact: true })).toBeDisabled()
  const draft = text+' 用户在等待时补充的新要求。'
  await sheet.getByRole('textbox', { name: '职位信息正文', exact: true }).fill(draft)
  release()
  await expect(sheet.getByRole('status')).toContainText('未覆盖当前内容')
  await expect(sheet.getByRole('textbox', { name: '职位信息正文', exact: true })).toHaveValue(draft)
})

test('PC 岗位截图导入与正文切换共用编辑预览', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const sheet = await openJob(page)
  await page.route('**/api/career/target-role-profiles/parse-file', route => route.fulfill({ json: { job_text: text, source_filename: 'job.png', notices: ['请检查截图内容是否完整。'] } }))
  await sheet.getByRole('button', { name: '截图或文件', exact: true }).click()
  await sheet.locator('.job-file-row input[type=file]').setInputFiles({ name: 'job.png', mimeType: 'image/png', buffer: Buffer.from('fixture-png') })
  await expect(sheet.getByRole('textbox', { name: '职位信息正文', exact: true })).toHaveValue(text)
  await sheet.getByRole('button', { name: '粘贴正文', exact: true }).click()
  await expect(sheet.getByRole('textbox', { name: '职位信息正文', exact: true })).toHaveValue(text)
})

test('PC 简历图片入口使用原解析与可编辑确认', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const sheet = await openSheet(page)
  await page.route('**/api/career/candidate-profiles/parse', route => route.fulfill({ json: { source_filename: 'resume.png', resume_outline: '真实项目经历', source_character_count: 6, sections: [] } }))
  await sheet.getByRole('button', { name: '图片识别', exact: true }).click()
  await expect(sheet.locator('.upload-workbench input[type=file]')).toHaveAttribute('accept', 'image/jpeg,image/png,image/webp')
  await sheet.locator('.upload-workbench input[type=file]').setInputFiles({ name: 'resume.png', mimeType: 'image/png', buffer: Buffer.from('fixture-png') })
  await sheet.getByRole('button', { name: '解析简历', exact: true }).click()
  await expect(sheet.getByRole('textbox', { name: '基准简历解析预览', exact: true })).toHaveValue('真实项目经历')
})

test.describe('手机无插件导入', () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, userAgent: mobileUserAgent })
  test('链接预览可读，切换与短视口不丢正文', async ({ page }) => {
    const sheet = await openJob(page)
    await page.route('**/api/career/target-role-profiles/parse-url', route => route.fulfill({ json: preview }))
    await parseLink(sheet)
    await expect(sheet.getByRole('textbox', { name: '职位信息正文', exact: true })).toHaveValue(text)
    await sheet.getByRole('button', { name: '截图或文件', exact: true }).click()
    await sheet.getByRole('button', { name: '职位链接', exact: true }).click()
    await expect(sheet.getByLabel('职位详情链接')).toHaveValue(preview.source_url)
    await page.setViewportSize({ width: 390, height: 430 })
    await expect(sheet.getByRole('textbox', { name: '职位信息正文', exact: true })).toHaveValue(text)
    expect(await sheet.evaluate(el => el.scrollWidth <= el.clientWidth+1)).toBe(true)
    await expect(sheet.getByRole('button', { name: '确认岗位并继续', exact: true })).toBeInViewport({ ratio: 1 })
    await page.setViewportSize({ width: 390, height: 844 })
    await sheet.getByRole('button', { name: '关闭', exact: true }).click()
    for (const name of ['技能库', '求职助手']) {
      await page.getByRole('button', { name: '打开导航', exact: true }).click()
      await page.locator('#app-navigation .nav-item').filter({ hasText: name }).click()
    }
    await page.locator('.composer-material-button').filter({ hasText: '目标岗位' }).click()
    await expect(sheet.getByLabel('职位详情链接')).toHaveValue(preview.source_url)
    await expect(sheet.getByRole('textbox', { name: '职位信息正文', exact: true })).toHaveValue(text)
    await expect(sheet.locator('.job-import-source')).toContainText('待核实')
  })
})
