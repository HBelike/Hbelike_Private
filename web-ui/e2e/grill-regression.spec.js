import { test, expect } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { installPlatformFixtures, openFixtureConversation, mobileUserAgent } from './support/platform-fixtures.js'

async function career(page) {
  await page.goto('/career')
  await page.locator('.career-workspace').waitFor()
  await openFixtureConversation(page)
  await expect(page.getByRole('textbox', { name: '求职咨询内容' })).toBeEnabled()
}

async function navigate(page, label) {
  const menu = page.getByRole('button', { name: '打开导航', exact: true })
  if (await menu.isVisible()) await menu.click()
  await page.locator('#app-navigation .nav-item').filter({ hasText: label }).click()
}

test('PC短屏保留四个求职快捷入口', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 420 })
  await installPlatformFixtures(page)
  await career(page)
  for (const name of ['线上笔试','面试大师','一键打招呼','职位检索']) {
    await expect(page.locator('.chat-material-button').filter({ hasText: name })).toBeVisible()
  }
  await expect(page.locator('.send-button')).toBeInViewport({ ratio: 1 })
})

test('会话读取失败与当前会话重选均保留草稿和资料', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await installPlatformFixtures(page)
  await page.route(url => url.pathname === '/api/career/conversations', route => route.fulfill({ json: { items: [
    { id: 'conversation-1', title: '当前会话' }, { id: 'broken', title: '读取失败的会话' }
  ], total: 2, page: 1, total_pages: 1 } }))
  await page.route('**/api/career/conversations/broken', route => route.fulfill({ status: 503, json: { detail: '模拟会话失败' } }))
  await page.goto('/career')
  await page.locator('.conversation-item-main').filter({ hasText: '当前会话' }).click()
  const input = page.getByRole('textbox', { name: '求职咨询内容' })
  await expect(input).toBeEnabled()
  await input.fill('必须保留的会话草稿')
  await page.locator('.conversation-item-main').filter({ hasText: '当前会话' }).click()
  await expect(input).toHaveValue('必须保留的会话草稿')
  await page.locator('.conversation-item-main').filter({ hasText: '读取失败的会话' }).click()
  await expect(page.locator('.career-error-toast')).toContainText('模拟会话失败')
  await expect(input).toHaveValue('必须保留的会话草稿')
  await expect(page.locator('.career-workspace')).toHaveClass(/has-context-rail/)
})

test('最新技能选择胜出，迟到响应不能覆盖当前编辑', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await installPlatformFixtures(page)
  const skills = ['c','a','b'].map(id => ({ id, name: `技能 ${id.toUpperCase()}`, editable: true }))
  await page.route('**/api/skills', route => route.fulfill({ json: { items: skills } }))
  let releaseA
  const blockedA = new Promise(resolve => { releaseA = resolve })
  let seenA
  const requestedA = new Promise(resolve => { seenA = resolve })
  await page.route(/\/api\/skills\/[abc]$/, async route => {
    const id = new URL(route.request().url()).pathname.split('/').at(-1)
    if (id === 'a') { seenA(); await blockedA }
    const skill = skills.find(item => item.id === id)
    await route.fulfill({ json: { skill, markdown: `# ${skill.name}` } })
  })
  await page.goto('/skills')
  await expect(page.locator('.skill-editor-panel h2')).toHaveText('技能 C')
  await page.locator('.installed-skill-item').filter({ hasText: '技能 A' }).click()
  await requestedA
  await page.locator('.installed-skill-item').filter({ hasText: '技能 B' }).click()
  await expect(page.locator('.skill-editor-panel h2')).toHaveText('技能 B')
  const lateResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/skills/a')
  releaseA()
  await lateResponse
  await page.locator('.skill-markdown-editor').fill('# B 的编辑内容')
  await expect(page.locator('.skill-editor-panel h2')).toHaveText('技能 B')
  await expect(page.locator('.skill-markdown-editor')).toHaveValue('# B 的编辑内容')
})

test('技能保存成功后反馈可见且内容保持新版本', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await installPlatformFixtures(page)
  const skill = { id: 'skill-1', name: '示例技能', editable: true }
  let markdown = '# 原始内容'
  await page.route('**/api/skills/skill-1', route => route.fulfill({ json: { skill, markdown } }))
  await page.route('**/api/skills/save', route => {
    markdown = route.request().postDataJSON().markdown
    return route.fulfill({ json: { skill, markdown, created: false } })
  })
  await page.goto('/skills')
  const editor = page.getByRole('textbox', { name: '编辑 SKILL.md' })
  await expect(editor).toBeVisible()
  await editor.fill('# 保存的新版本')
  await page.locator('.skill-save-row button').click()
  await expect(page.locator('.skill-save-row button')).toBeEnabled()
  await expect(page.getByRole('status')).toContainText('已更新本地 Skill')
  await expect(editor).toHaveValue('# 保存的新版本')
})

test('评测总览创建实验进入正确子路由并展示表单', async ({ page }) => {
  await installPlatformFixtures(page)
  await page.goto('/evaluations')
  await page.locator('.evaluation-actions button').filter({ hasText: '创建实验' }).click()
  await expect(page).toHaveURL(/\/evaluations\/experiments$/)
  await expect(page.locator('form.evaluation-form')).toBeVisible()
})

test('评估排队后失败保留旧报告并标明版本，成功再替换', async ({ page }) => {
  await installPlatformFixtures(page)
  const assessment = JSON.parse(readFileSync(new URL('./fixtures/job-match-assessment.json', import.meta.url), 'utf8'))
  const base = { binding_id: 'binding-1', candidate_profile: { id: 'candidate-1', display_name: '测试简历', version: 1 }, target_role: { id: 'role-1', role_name: '测试岗位', version: 1 } }
  let phase = 'initial'
  await page.route('**/api/career/conversations/conversation-1', route => route.fulfill({ json: {
    conversation: { id: 'conversation-1', title: '报告验证' }, context: { ...base, assessment: phase === 'failed' ? { status: 'failed' } : assessment }, messages: [], latest_turn: null
  } }))
  await page.route('**/api/career/conversations/conversation-1/assessment/retry', route => {
    phase = phase === 'initial' ? 'failed' : 'success'
    return route.fulfill({ json: { ...base, assessment: { status: 'queued' } } })
  })
  await career(page)
  const context = page.getByRole('button', { name: '职位信息', exact: true })
  if (await context.isVisible()) await context.click()
  await page.locator('.job-match-grid button').filter({ hasText: '核心要求' }).click()
  const dialog = page.getByRole('dialog', { name: '岗位匹配分析', exact: true })
  await dialog.getByRole('button', { name: '重新分析', exact: true }).click()
  await expect(dialog.getByRole('status').filter({ hasText: '本次分析未完成' })).toBeVisible()
  await expect(dialog.locator('.match-summaries')).toContainText('75–100%')
  await expect(dialog.locator('.match-previous-report')).toContainText('简历 v1')
  await expect(dialog.locator('.match-table tbody tr').first()).toBeVisible()
  await dialog.getByRole('button', { name: '重新分析', exact: true }).click()
  await expect(dialog.locator('.match-previous-report')).toHaveCount(0)
  await expect(dialog.locator('.match-summaries')).toContainText('75–100%')
})

test('资料版本更新后，旧评估轮询不能覆盖新绑定', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await installPlatformFixtures(page)
  const assessment = JSON.parse(readFileSync(new URL('./fixtures/job-match-assessment.json', import.meta.url), 'utf8'))
  const base = { binding_id: 'binding-1', candidate_profile: { id: 'candidate-1', display_name: '测试简历', version: 1 }, target_role: { id: 'role-1', role_name: '旧岗位', version: 1, requirements: [] } }
  const updated = { ...base, target_role: { ...base.target_role, role_name: '新岗位', version: 2 }, assessment }
  let reads = 0
  let release
  const blocked = new Promise(resolve => { release = resolve })
  let seen
  const requested = new Promise(resolve => { seen = resolve })
  await page.route('**/api/career/conversations/conversation-1', async route => {
    const first = ++reads === 1
    if (!first) { seen(); await blocked }
    await route.fulfill({ json: { conversation: { id: 'conversation-1', title: '异步绑定验证' }, context: { ...base, assessment: first ? { status: 'queued' } : assessment }, messages: [] } })
  })
  await page.route('**/api/career/conversations/conversation-1/context', route => route.fulfill({ json: updated }))
  await career(page)
  await requested
  await page.locator('.active-context-chip').click()
  const sheet = page.getByRole('dialog', { name: '建立求职上下文', exact: true })
  await sheet.getByRole('button', { name: '保留当前简历', exact: true }).click()
  await sheet.getByRole('button', { name: '保留当前岗位', exact: true }).click()
  await sheet.getByRole('button', { name: '保存并返回对话', exact: true }).click()
  await expect(sheet).toHaveCount(0)
  await expect(page.locator('.context-rail')).toContainText('新岗位')
  const response = page.waitForResponse(res => new URL(res.url()).pathname === '/api/career/conversations/conversation-1')
  release()
  await (await response).finished()
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => resolve())))
  const contextButton = page.getByRole('button', { name: '职位信息', exact: true })
  if (await contextButton.isVisible()) await contextButton.click()
  await page.locator('.job-match-grid button').filter({ hasText: '核心要求' }).click()
  await expect(page.locator('.match-header')).toContainText('新岗位')
})

test.describe('手机压力问题回归', () => {
  test.use({ viewport: { width: 390, height: 844 }, userAgent: mobileUserAgent, isMobile: true, hasTouch: true })

  test('长 URL 正文换行，正文16px，代码和表格只局部滚动', async ({ page }) => {
    await installPlatformFixtures(page)
    const content = 'https://example.com/' + 'LongPathWithoutSpace'.repeat(30)
      + '\n\n```text\n' + 'unbroken-code-'.repeat(80) + '\n```'
      + '\n\n| 第一列 | 第二列 | 第三列 | 第四列 |\n| --- | --- | --- | --- |\n| 内容 | 内容 | 内容 | 内容 |'
    await page.route('**/api/career/conversations/conversation-1', route => route.fulfill({ json: {
      conversation: { id: 'conversation-1', title: '长文本验证' }, context: null, messages: [{ id: 'message-1', role: 'assistant', content }]
    } }))
    await career(page)
    for (const selector of ['.message-list','.from-agent .message-content']) {
      expect(await page.locator(selector).evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true)
    }
    await expect(page.locator('.message-content')).toHaveCSS('font-size', '16px')
    expect(await page.locator('.message-content pre').evaluate(el => el.scrollWidth > el.clientWidth)).toBe(true)
    expect(await page.locator('.message-table-wrap').evaluate(el => el.scrollWidth > el.clientWidth)).toBe(true)
  })

  test('技能重选与跨技能返回保留未保存草稿', async ({ page }) => {
    await installPlatformFixtures(page)
    const skills = ['a', 'b'].map(id => ({ id, name: `技能 ${id.toUpperCase()}`, editable: true }))
    await page.route('**/api/skills', route => route.fulfill({ json: { items: skills } }))
    await page.route(/\/api\/skills\/[ab]$/, route => {
      const skill = skills.find(item => route.request().url().endsWith(`/${item.id}`))
      return route.fulfill({ json: { skill, markdown: `# ${skill.name}` } })
    })
    await page.goto('/skills')
    await page.locator('.installed-skill-item').filter({ hasText: '技能 A' }).click()
    await page.getByRole('button', { name: '编辑内容', exact: true }).click()
    const editor = page.getByRole('textbox', { name: '编辑 SKILL.md' })
    await editor.fill('# 未保存的修改')
    await page.getByRole('button', { name: '已安装', exact: true }).click()
    await page.locator('.installed-skill-item').filter({ hasText: '技能 A' }).click()
    await expect(editor).toHaveValue('# 未保存的修改')
    await page.getByRole('button', { name: '已安装', exact: true }).click()
    await page.locator('.installed-skill-item').filter({ hasText: '技能 B' }).click()
    await expect(editor).toHaveValue('# 技能 B')
    await editor.fill('# B 的草稿')
    await page.getByRole('button', { name: '已安装', exact: true }).click()
    await page.locator('.installed-skill-item').filter({ hasText: '技能 A' }).click()
    await expect(editor).toHaveValue('# 未保存的修改')
    await page.getByRole('button', { name: '阅读当前内容', exact: true }).click()
    await expect(page.locator('.mobile-skill-reader .message-content')).toHaveCSS('font-size', '16px')
  })

  test('简历文件、岗位和要求跨模块返回仍保留', async ({ page }) => {
    await installPlatformFixtures(page)
    await page.route('**/api/career/resume-optimizations/generate', route => route.fulfill({ json: { optimized_markdown: '# 尚未保存的优化结果' } }))
    await page.goto('/resume-assistant')
    await page.getByRole('button', { name: /新建优化任务/ }).click()
    await page.locator('#resume-file-input').setInputFiles({ name: 'draft-resume.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 draft') })
    await page.getByLabel('岗位介绍').fill('未保存的岗位介绍')
    await page.getByLabel('额外要求').fill('保留真实经历')
    await page.getByRole('button', { name: '开始分析', exact: true }).click()
    await page.getByRole('textbox', { name: '编辑修改建议', exact: true }).fill('保留已审核的修改建议')
    await page.getByRole('button', { name: '生成优化简历', exact: true }).click()
    await expect(page.locator('.optimized-preview')).toContainText('尚未保存的优化结果')
    await navigate(page, '求职助手')
    await expect(page).toHaveURL(/\/career$/)
    await page.goBack()
    await expect(page).toHaveURL(/\/resume-assistant\/new$/)
    await expect(page.getByLabel('岗位介绍')).toHaveValue('未保存的岗位介绍')
    await expect(page.getByLabel('额外要求')).toHaveValue('保留真实经历')
    await expect(page.locator('.file-field')).toContainText('draft-resume.pdf')
    await expect(page.locator('.optimized-preview')).toContainText('尚未保存的优化结果')
    await page.getByRole('button', { name: '返回修改意见', exact: true }).click()
    await expect(page.getByRole('textbox', { name: '编辑修改建议', exact: true })).toHaveValue('保留已审核的修改建议')
    await navigate(page, '求职助手')
    await navigate(page, '简历助手')
    await page.getByRole('button', { name: '继续当前任务', exact: true }).click()
    await expect(page.getByLabel('岗位介绍')).toHaveValue('未保存的岗位介绍')
    await page.getByRole('button', { name: '返回历史' }).click()
    page.once('dialog', dialog => dialog.dismiss())
    await page.getByRole('button', { name: /新建优化任务/ }).click()
    await page.getByRole('button', { name: '继续当前任务', exact: true }).click()
    await expect(page.getByLabel('额外要求')).toHaveValue('保留真实经历')
  })

  test('浏览器返回先退详情，再退路由，显式返回不留下无效层', async ({ page }) => {
    await installPlatformFixtures(page)
    await page.goto('/career')
    await navigate(page, '面经库')
    await expect(page).toHaveURL(/\/interviews$/)
    await page.locator('.tree-leaf').click()
    await expect(page.locator('.interview-detail-pane')).toBeVisible()
    await page.goBack()
    await expect(page).toHaveURL(/\/interviews$/)
    await expect(page.locator('.interview-tree-pane')).toBeVisible()
    await page.locator('.tree-leaf').click()
    await page.getByRole('button', { name: '返回面经列表' }).click()
    await expect(page.locator('.interview-tree-pane')).toBeVisible()
    await page.goBack()
    await expect(page).toHaveURL(/\/career$/)
  })

  test('浏览器返回关闭资料 Sheet 并保留聊天草稿', async ({ page }) => {
    await installPlatformFixtures(page)
    await career(page)
    const input = page.getByRole('textbox', { name: '求职咨询内容' })
    await input.fill('弹层返回后的草稿')
    await page.getByRole('button', { name: '模型配置', exact: true }).click()
    await page.locator('.active-context-chip').click()
    await expect(page.getByRole('dialog', { name: '建立求职上下文', exact: true })).toBeVisible()
    await page.goBack()
    await expect(page).toHaveURL(/\/career$/)
    await expect(page.getByRole('dialog', { name: '建立求职上下文', exact: true })).toHaveCount(0)
    await expect(input).toHaveValue('弹层返回后的草稿')
  })

  test('嵌套报告按层返回，资料保存中返回保持等待', async ({ page }) => {
    await installPlatformFixtures(page)
    const assessment = JSON.parse(readFileSync(new URL('./fixtures/job-match-assessment.json', import.meta.url), 'utf8'))
    const context = { binding_id: 'binding-1', candidate_profile: { id: 'candidate-1', display_name: '测试简历', version: 1 }, target_role: { id: 'role-1', role_name: '测试岗位', version: 1, requirements: [] }, assessment }
    await page.route('**/api/career/conversations/conversation-1', route => route.fulfill({ json: { conversation: { id: 'conversation-1' }, context, messages: [] } }))
    let release
    const blocked = new Promise(resolve => { release = resolve })
    let seen
    const requested = new Promise(resolve => { seen = resolve })
    await page.route('**/api/career/conversations/conversation-1/context', async route => { seen(); await blocked; await route.fulfill({ json: context }) })
    await career(page)
    await page.getByRole('button', { name: '职位信息', exact: true }).click()
    await page.locator('.job-match-grid button').filter({ hasText: '核心要求' }).click()
    await page.goBack()
    await expect(page.getByRole('dialog', { name: '岗位匹配分析', exact: true })).toHaveCount(0)
    await expect(page.locator('.context-rail')).toBeVisible()
    await page.goBack()
    await expect(page.locator('.context-rail-slot')).toHaveAttribute('aria-hidden', 'true')
    await expect(page.locator('.context-rail-slot')).not.toBeInViewport()
    await page.getByRole('button', { name: '模型配置', exact: true }).click()
    await page.locator('.active-context-chip').click()
    const sheet = page.getByRole('dialog', { name: '建立求职上下文', exact: true })
    await sheet.getByRole('button', { name: '保留当前简历', exact: true }).click()
    await sheet.getByRole('button', { name: '保留当前岗位', exact: true }).click()
    await sheet.getByRole('button', { name: '保存并返回对话', exact: true }).click()
    await requested
    await page.goBack()
    await expect(sheet.getByRole('button', { name: '正在更新资料…', exact: true })).toBeDisabled()
    await expect(page).toHaveURL(/\/career$/)
    release()
    await expect(sheet).toHaveCount(0)
    await navigate(page, '面经库')
    await page.goBack()
    await expect(page).toHaveURL(/\/career$/)
  })

  test('手机实际创建表单为单列', async ({ page }) => {
    await installPlatformFixtures(page)
    await page.route('**/api/evaluations/datasets', route => route.fulfill({ json: { items: [{ id: 'dataset-1', name: '真实数据集', target_type: 'interview_rag', versions: [{ id: 'version-1', status: 'draft', version_label: '草稿', case_count: 0 }] }] } }))
    await page.goto('/evaluations/experiments')
    await page.locator('.evaluation-actions button').filter({ hasText: '创建实验' }).click()
    await expect(page.locator('form.evaluation-form')).toBeVisible()
    async function singleColumn(selector) {
      const form = page.locator(selector)
      await expect(form).toBeVisible()
      expect(await form.evaluate(el => getComputedStyle(el).gridTemplateColumns.split(' '))).toHaveLength(1)
      expect(await form.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true)
    }
    await singleColumn('form.evaluation-form')
    await page.locator('.evaluation-tabs button').filter({ hasText: '数据集' }).click()
    await page.getByRole('button', { name: '新建数据集', exact: true }).click()
    await singleColumn('form.evaluation-form')
    await page.getByRole('button', { name: '新增版本', exact: true }).click()
    await singleColumn('form.evaluation-inline-form')
    await page.getByRole('button', { name: '录入案例', exact: true }).click()
    await singleColumn('.evaluation-case-form .evaluation-form')
  })
})
