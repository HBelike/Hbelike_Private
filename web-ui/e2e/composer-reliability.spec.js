import { test, expect } from '@playwright/test'
import { installApiFixtures } from './support/api-fixtures.js'

export async function installReadyModel(page) {
  await page.route('**/api/career/model-profiles', route => route.fulfill({ json: { items: [{
    readiness: 'ready', profile: { id: 'model-1', provider_key: 'deepseek', model_id: 'deepseek-v4-pro',
      display_name: '输入验收模型', capabilities: ['text'], cost_tier: 'paid' }
  }] } }))
}

test('PC 中文组词与换行不发送，Enter 只提交一次', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await installApiFixtures(page)
  await installReadyModel(page)
  let requests = 0
  await page.route('**/api/career/conversations/conversation-1/turns', route => {
    requests += 1
    return route.fulfill({ status: 503, json: { detail: '模拟发送失败' } })
  })
  await page.goto('/responsive-layout-preview.html?scenario=career')
  await page.locator('.conversation-item-main').first().click()
  const input = page.getByRole('textbox', { name: '求职咨询内容' })
  await expect(input).toBeEnabled()
  await input.fill('输入法组合内容')
  await input.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true })
  await input.dispatchEvent('keydown', { key: 'Enter', keyCode: 229 })
  await input.press('Shift+Enter')
  await expect(input).toHaveValue('输入法组合内容\n')
  expect(requests).toBe(0)
  await input.press('Enter')
  await expect.poll(() => requests).toBe(1)
  await expect(page.locator('.career-error-toast')).toContainText('模拟发送失败')
  await expect(input).toBeEnabled()
  await input.press('ArrowUp')
  await expect(input).toHaveValue('输入法组合内容')
  await input.dispatchEvent('keydown', { key: 'Enter', repeat: true })
  await expect(input).toHaveValue('输入法组合内容')
  expect(requests).toBe(1)
  await input.press('Enter')
  await expect.poll(() => requests).toBe(2)
})

const conversation = { id: 'conversation-1', title: '历史输入验收', updated_at: '2026-11-09T10:00:00+08:00' }
const historyMessages = [
  { id: 'user-1', role: 'user', content: '第一条问题' },
  { id: 'assistant-1', role: 'assistant', content: '助手回答不应被回填' },
  { id: 'user-2', role: 'user', content: '第二条问题\n补充内容' },
  { id: 'review-1', role: 'user', source: 'resume_review', content: '后台简历分析不应被回填' }
]

async function openHistoryComposer(page, messages = historyMessages) {
  await page.setViewportSize({ width: 1440, height: 900 })
  await installApiFixtures(page)
  await installReadyModel(page)
  await page.route('**/api/career/conversations?page=*', route => route.fulfill({
    json: { items: [conversation, { ...conversation, id: 'conversation-2', title: '另一会话' }], total: 2 }
  }))
  await page.route('**/api/career/conversations/conversation-1', route => route.fulfill({
    json: { conversation, context: null, messages, latest_turn: null }
  }))
  await page.goto('/responsive-layout-preview.html?scenario=career')
  await page.locator('.conversation-item-main').first().click()
  const input = page.getByRole('textbox', { name: '求职咨询内容' })
  await expect(input).toBeEnabled()
  return input
}

test('PC ↑ 逐条回填当前会话历史，长按切换、到头停留，↓ 恢复原草稿', async ({ page }) => {
  const input = await openHistoryComposer(page)
  let requests = 0
  page.on('request', request => { if (request.method() === 'POST') requests += 1 })
  await input.fill('尚未发送的草稿')
  await input.press('ArrowUp')
  await expect(input).toHaveValue('第二条问题\n补充内容')
  await expect(page.locator('.composer-highlight-layer')).toHaveText('第二条问题\n补充内容')
  await input.dispatchEvent('keydown', { key: 'ArrowUp', repeat: true })
  await expect(input).toHaveValue('第一条问题')
  await input.press('ArrowUp')
  await expect(input).toHaveValue('第一条问题')
  await input.press('ArrowDown')
  await expect(input).toHaveValue('第二条问题\n补充内容')
  await input.press('ArrowDown')
  await expect(input).toHaveValue('尚未发送的草稿')
  await input.press('ArrowDown')
  await expect(input).toHaveValue('尚未发送的草稿')
  expect(requests).toBe(0)
})

test('PC 多行、选区、中文选词不覆盖草稿，编辑回填内容后重新开始浏览', async ({ page }) => {
  const input = await openHistoryComposer(page)
  await input.fill('第一行草稿\n第二行草稿')
  await input.press('ArrowUp')
  await expect(input).toHaveValue('第一行草稿\n第二行草稿')
  await input.fill('中文草稿')
  await input.dispatchEvent('keydown', { key: 'ArrowUp', isComposing: true })
  await input.dispatchEvent('keydown', { key: 'ArrowUp', keyCode: 229 })
  await expect(input).toHaveValue('中文草稿')
  await input.selectText()
  await input.press('ArrowUp')
  await expect(input).toHaveValue('中文草稿')
  await input.press('ArrowUp')
  await expect(input).toHaveValue('第二条问题\n补充内容')
  await input.fill('回填后编辑的草稿')
  await input.press('ArrowUp')
  await expect(input).toHaveValue('第二条问题\n补充内容')
  await input.press('ArrowDown')
  await expect(input).toHaveValue('回填后编辑的草稿')
})

test('PC 无历史保持输入；成功切换会话后只回填新会话的输入', async ({ page }) => {
  const input = await openHistoryComposer(page, [])
  await input.fill('没有历史的草稿')
  await input.press('ArrowUp')
  await expect(input).toHaveValue('没有历史的草稿')
  await page.route('**/api/career/conversations/conversation-2', route => route.fulfill({
    json: { conversation: { ...conversation, id: 'conversation-2', title: '另一会话' }, context: null,
      messages: [{ id: 'another-user', role: 'user', content: '另一会话的问题' }], latest_turn: null }
  }))
  await page.locator('.conversation-item-main').filter({ hasText: '另一会话' }).click()
  await expect(input).toHaveValue('')
  await input.press('ArrowUp')
  await expect(input).toHaveValue('另一会话的问题')
  await page.locator('.conversation-item-main').filter({ hasText: conversation.title }).click()
  await expect(input).toHaveValue('')
  await input.press('ArrowUp')
  await expect(input).toHaveValue('')
})

test('PC 回填后 Enter 成功发送，草稿的 Skill 与面经引用可随 ↓ 恢复', async ({ page }) => {
  const messages = [...historyMessages]
  const input = await openHistoryComposer(page, messages)
  await page.route('**/api/career/skills/mentions?query=*', route => route.fulfill({ json: { items: [
    { id: 'skill-1', name: 'resume', description_zh: '简历技能' }
  ] } }))
  await page.route('**/api/career/interview-library/mentions?query=*', route => route.fulfill({ json: { items: [
    { id: 'interview-1', company_name: '示例科技', role_name: '工程师' }
  ] } }))
  const submissions = []
  await page.route('**/api/career/conversations/conversation-1/turns', route => {
    const payload = route.request().postDataJSON()
    submissions.push(payload)
    messages.push({ id: 'sent-user', role: 'user', content: payload.text })
    return route.fulfill({ status: 202, json: { turn: { id: 'turn-1', status: 'queued' } } })
  })
  await page.route('**/api/career/turns/turn-1/events', route => route.fulfill({ contentType: 'text/event-stream',
    body: `event: done\ndata: ${JSON.stringify({ turn: { id: 'turn-1', status: 'succeeded' }, message: messages.at(-1),
      assistant_message: { id: 'sent-assistant', role: 'assistant', content: '已收到问题。' } })}\n\n` }))
  await input.fill('/resume')
  await page.getByRole('option').filter({ hasText: 'resume' }).click()
  await input.press('End')
  await input.pressSequentially('@示例')
  await page.getByRole('option').filter({ hasText: '示例科技' }).click()
  const draft = await input.inputValue()
  await expect(page.locator('.composer-highlight-skill')).toHaveCount(1)
  await expect(page.locator('.composer-highlight-interview')).toHaveCount(1)
  await input.press('ArrowUp')
  await expect(input).toHaveValue('第二条问题\n补充内容')
  await expect(page.locator('.composer-highlight-skill')).toHaveCount(0)
  await input.press('ArrowDown')
  await expect(input).toHaveValue(draft)
  await expect(page.locator('.composer-highlight-skill')).toHaveCount(1)
  await expect(page.locator('.composer-highlight-interview')).toHaveCount(1)
  await input.press('ArrowUp')
  await input.press('Enter')
  await expect.poll(() => submissions.length).toBe(1)
  expect(submissions[0]).toMatchObject({ text: '第二条问题\n补充内容', selected_skill_ids: [], interview_experience_ids: [] })
  await expect(page.getByText('已收到问题。', { exact: true })).toBeVisible()
  await expect(input).toBeEnabled()
  await expect(input).toHaveValue('')
  await input.press('ArrowUp')
  await expect(input).toHaveValue('第二条问题\n补充内容')
  await input.press('ArrowDown')
  await expect(input).toHaveValue('')
})
