import { test, expect } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { installApiFixtures } from './support/api-fixtures.js'

const report = JSON.parse(readFileSync(new URL('./fixtures/job-match-assessment.json', import.meta.url), 'utf8'))
const conversation = { id: 'conversation-1', title: '运营岗位匹配验收', updated_at: '2026-11-04T12:00:00+08:00' }

async function openReport(page, assessment = report) {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await installApiFixtures(page)
  await page.route(url => url.pathname === '/api/career/conversations', route => route.fulfill({ json: {
    items: [conversation], page: 1, page_size: 5, total: 1, total_pages: 1
  } }))
  const context = {
    binding_id: 'binding-1', candidate_profile: { id: 'candidate-1', display_name: '运营示例简历', version: 1 },
    target_role: { id: 'role-1', role_name: '用户运营专员', company_name: '示例品牌', version: 1,
      job_text: Object.values(report.source_lines.jd).join('\n') }, assessment
  }
  await page.route('**/api/career/conversations/conversation-1', route => route.fulfill({ json: {
    conversation, context, messages: [], latest_turn: null, last_model_selection: null
  } }))
  await page.goto('/responsive-layout-preview.html?scenario=career')
  await page.locator('.conversation-item-main').filter({ hasText: conversation.title }).click()
  const trigger = page.locator('.job-match-grid button').filter({ hasText: assessment.algorithm_version === 'jd-evidence-rules-v2' ? '核心要求' : '活动与复盘' })
  await expect(trigger).toBeAttached()
  const panelToggle = page.getByRole('button', { name: '职位信息', exact: true })
  if (await panelToggle.isVisible()) await panelToggle.click()
  await trigger.click()
  const dialog = page.getByRole('dialog', { name: '岗位匹配分析', exact: true })
  await expect(dialog).toBeVisible()
  return { dialog, trigger, context }
}

test('PC指标卡打开通用岗位弹窗并定位、筛选、核对原文与计算，关闭恢复焦点', async ({ page }) => {
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const { dialog, trigger } = await openReport(page)
  await expect(dialog.locator('.match-summaries')).toContainText('75–100%')
  await expect(dialog.locator('.match-table tbody tr')).toHaveCount(2)
  await expect(dialog.locator('.match-evidence')).toContainText('独立策划活动并复盘')
  await expect(dialog.locator('.match-evidence')).toContainText('数据复盘')
  await dialog.getByRole('button', { name: '定位简历原文 →' }).click()
  await expect(dialog.locator('.match-original.is-highlighted')).toHaveCount(1)
  await expect(dialog.locator('.match-original.is-highlighted')).toContainText('跨团队协调')
  await dialog.getByRole('button', { name: '计算规则', exact: true }).click()
  await expect(dialog.locator('.match-formula').first()).toContainText('1.5 ÷ 2 × 100 = 75%')
  await dialog.getByRole('button', { name: '要求对照', exact: true }).click()
  await dialog.locator('.match-filters').getByRole('button', { name: '硬性条件', exact: true }).click()
  await dialog.getByRole('combobox', { name: '筛选匹配状态' }).selectOption('needs_confirmation')
  await expect(dialog.locator('.match-table tbody tr')).toHaveCount(1)
  await expect(dialog.locator('.match-table')).toContainText('接受短期出差')
  await expect(dialog.locator('.match-evidence')).toContainText('需要补充的事实')
  await dialog.getByRole('combobox', { name: '筛选匹配状态' }).selectOption('conflict')
  await expect(dialog.locator('.match-table tbody tr')).toHaveCount(0)
  await expect(dialog.locator('.match-primary .match-empty')).toContainText('当前筛选下没有要求')
  await dialog.getByRole('combobox', { name: '筛选匹配状态' }).selectOption('all')
  await dialog.locator('.match-filters').getByRole('button', { name: '全部', exact: true }).click()
  await dialog.locator('.match-dimensions').getByRole('button', { name: /活动与复盘/ }).click()
  await expect(dialog.locator('.match-table tbody tr')).toHaveCount(1)
  await page.screenshot({ path: 'test-results/job-match-dialog-pc.png', fullPage: true })
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  await expect(trigger).toBeFocused()
  expect(errors).toEqual([])
})

test('旧版报告保留旧分数并提示重新分析，不伪装成新版三层结果', async ({ page }) => {
  const legacy = { status: 'ready', algorithm_version: 'llm-judge-v1',
    dimensions: [{ id: 'activity', label: '活动与复盘', status: 'ready', score: 50, item_count: 1, description: '旧版活动证据' }],
    items: [{ id: 'old', dimension_id: 'activity', requirement_type: 'required', factor: 0.5, verdict: 'partial', jd_evidence: ['完成策划并复盘'], cv_evidence: ['独立策划会员主题活动'], reason: '旧版部分证据' }],
    job_sections: [], disclaimer: '旧版报告' }
  const { dialog } = await openReport(page, legacy)
  await expect(dialog.locator('.match-notice')).toContainText('分数沿用旧算法')
  await expect(dialog.locator('.match-table')).toContainText('独立策划会员主题活动')
  await dialog.getByRole('button', { name: '计算规则', exact: true }).click()
  await expect(dialog.locator('.match-rules')).toContainText('部分证据按 0.5 折算')
  await dialog.getByRole('button', { name: '关闭岗位匹配分析' }).click()
  await expect(dialog).toHaveCount(0)
})

test('重新分析复用现有排队和轮询链路，弹窗等待后恢复新结果', async ({ page }) => {
  const { dialog, context } = await openReport(page)
  let requests = 0
  await page.route('**/api/career/conversations/conversation-1/assessment/retry', route => {
    requests += 1
    return route.fulfill({ json: { ...context, assessment: { status: 'queued' } } })
  })
  await dialog.getByRole('button', { name: '重新分析', exact: true }).click()
  await expect(dialog.getByRole('status')).toContainText('正在分析')
  await expect(dialog.getByRole('button', { name: '分析中…' })).toBeDisabled()
  await expect(dialog.locator('.match-summaries')).toContainText('75–100%', { timeout: 8000 })
  expect(requests).toBe(1)
  await dialog.getByRole('button', { name: '关闭岗位匹配分析' }).click()
  await expect(dialog).toHaveCount(0)
})

test('重复点击重新分析只提交一次，提交失败时弹窗保留报告并展示原因', async ({ page }) => {
  const { dialog } = await openReport(page)
  let requests = 0
  await page.route('**/api/career/conversations/conversation-1/assessment/retry', async route => {
    requests += 1
    await new Promise(resolve => setTimeout(resolve, 120))
    return route.fulfill({ status: 503, json: { detail: '模拟重试失败' } })
  })
  await dialog.getByRole('button', { name: '重新分析', exact: true }).evaluate(el => { el.click(); el.click() })
  await expect(dialog.getByRole('alert')).toContainText('模拟重试失败')
  await expect(dialog.locator('.match-summaries')).toContainText('75–100%')
  expect(requests).toBe(1)
})
