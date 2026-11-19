import { test, expect } from '@playwright/test'
import { installPlatformFixtures, openFixtureConversation } from './support/platform-fixtures.js'

async function openResume(page) {
  await page.setViewportSize({ width: 1440, height: 900 })
  await installPlatformFixtures(page)
  await page.goto('/career')
  await openFixtureConversation(page)
  const tools = page.getByRole('button', { name: '资料与工具', exact: true })
  if (await tools.isVisible()) await tools.click()
  await page.locator('.active-context-chip').click()
  const sheet = page.getByRole('dialog', { name: '建立求职上下文', exact: true })
  await sheet.getByRole('button', { name: '上传新简历', exact: true }).click()
  await sheet.locator('.upload-workbench input[type=file]').setInputFiles({
    name: 'resume.pdf', mimeType: 'application/pdf', buffer: Buffer.from('fixture-pdf')
  })
  return sheet
}

const warning = '文档仅部分识别成功，可能缺少页面或区块；请对照原简历补全。'
const partial = {
  source_filename: 'resume.pdf', resume_outline: '已识别的项目经历',
  source_character_count: 9, sections: [], parsing_status: 'needs_review', notices: [warning]
}

test('PC 部分识别展示具体警告，补全并核对后才可保存', async ({ page }) => {
  const sheet = await openResume(page)
  await page.route('**/api/career/candidate-profiles/parse', route => route.fulfill({ json: partial }))
  let saved
  await page.route('**/api/career/candidate-profiles', route => {
    saved = route.request().postDataJSON()
    return route.fulfill({ json: { ...saved, id: 'new-candidate', version: 1 } })
  })
  await sheet.getByRole('button', { name: '解析简历', exact: true }).click()
  await expect(sheet.getByRole('status')).toContainText('简历内容待核对')
  await expect(sheet.getByRole('alert')).toContainText(warning)
  const confirm = sheet.getByRole('button', { name: '确认简历并继续', exact: true })
  await expect(confirm).toBeDisabled()
  const text = '项目经历\n' + '真实经历原文。'.repeat(500) + '\n论文成果\n末尾论文：第一作者，已发表。'
  await sheet.getByRole('textbox', { name: '基准简历解析预览', exact: true }).fill(text)
  await sheet.getByLabel('我已对照原简历核对并补全缺失内容').check()
  await expect(confirm).toBeEnabled()
  await confirm.click()
  await expect(sheet.getByRole('heading', { name: '添加目标岗位', exact: true })).toBeVisible()
  expect(saved.resume_outline).toBe(text)
})

test('PC 重新解析成功后清除旧警告，完整预览使用中性说明', async ({ page }) => {
  const sheet = await openResume(page)
  let requests = 0
  await page.route('**/api/career/candidate-profiles/parse', route => route.fulfill({ json: ++requests === 1
    ? partial
    : { ...partial, parsing_status: 'ready', notices: [], resume_outline: '完整经历与末尾成果' }
  }))
  await sheet.getByRole('button', { name: '解析简历', exact: true }).click()
  await expect(sheet.getByRole('alert')).toBeVisible()
  await sheet.getByRole('button', { name: '重新解析', exact: true }).click()
  await expect(sheet.getByRole('textbox', { name: '基准简历解析预览', exact: true })).toHaveValue('完整经历与末尾成果')
  await expect(sheet.getByRole('alert')).toHaveCount(0)
  await expect(sheet.getByRole('button', { name: '确认简历并继续', exact: true })).toBeEnabled()
  await expect(sheet).not.toContainText('请修正解析错误')
})

test('PC OCR 失败展示实际原因，原文件可直接重试', async ({ page }) => {
  const sheet = await openResume(page)
  await page.route('**/api/career/candidate-profiles/parse', route => route.fulfill({ status: 422, json: { detail: '文档解析服务处理超时，请稍后重试' } }))
  await sheet.getByRole('button', { name: '解析简历', exact: true }).click()
  await expect(sheet.getByRole('alert')).toContainText('处理超时')
  await expect(sheet.getByRole('textbox', { name: '基准简历解析预览', exact: true })).toHaveCount(0)
  await expect(sheet.getByRole('button', { name: '解析简历', exact: true })).toBeEnabled()
  await expect(sheet.getByRole('button', { name: '确认简历并继续', exact: true })).toBeDisabled()
})
