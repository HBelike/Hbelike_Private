import { test, expect } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { installPlatformFixtures, mobileUserAgent, openFixtureConversation } from './support/platform-fixtures.js'

test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, userAgent: mobileUserAgent })

async function expectFits(page, selector = '.main') {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true)
  for (const element of await page.locator(selector).all()) {
    await expect.poll(() => element.evaluate(el => el.scrollWidth <= el.clientWidth + 1), {
      message: `${page.url()} @ ${JSON.stringify(page.viewportSize())} 的 ${selector} 不应横向溢出`
    }).toBe(true)
  }
}

test('手机对话选中关闭抽屉、草稿跨尺寸保留、组词和换行不发送', async ({ page }) => {
  await installPlatformFixtures(page)
  let requests = 0
  await page.route('**/api/career/conversations/conversation-1/turns', route => {
    requests += 1
    return route.fulfill({ status: 503, json: { detail: '模拟网络中断' } })
  })
  await page.goto('/career')
  await expect(page.locator('.history-trigger')).toBeVisible()
  await openFixtureConversation(page)
  await expect(page.locator('.career-workspace')).not.toHaveClass(/compact-history-open/)
  await expect(page.locator('#career-history-panel')).toHaveAttribute('inert', '')
  const input = page.getByRole('textbox', { name: '求职咨询内容' })
  await expect(input).toBeEnabled()
  await input.fill('跨尺寸草稿')
  await page.setViewportSize({ width: 1440, height: 900 })
  await expect(input).toHaveValue('跨尺寸草稿')
  for (const [width,height] of [[360,800],[375,812],[390,844],[430,932],[844,390],[390,430]]) {
    await page.setViewportSize({ width, height })
    await expect(input).toHaveValue('跨尺寸草稿')
    await expect.poll(async () => {
      const box = await page.locator('.send-button').boundingBox()
      return box && box.y >= 0 && box.y + box.height <= height + 1
    }).toBe(true)
    await expectFits(page, '.career-chat-panel')
  }
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: 'test-results/mobile-career-ready.png', fullPage: true, animations: 'disabled' })
  await input.dispatchEvent('keydown', { key: 'Enter', isComposing: true })
  await input.press('Enter')
  await expect(input).toHaveValue('跨尺寸草稿\n')
  expect(requests).toBe(0)
  await page.getByRole('button', { name: '发送', exact: true }).click()
  await expect.poll(() => requests).toBe(1)
  await expect(page.locator('.career-error-toast')).toContainText('模拟网络中断')
  await page.screenshot({ path: 'test-results/mobile-career.png', fullPage: true, animations: 'disabled' })
})

test('手机抽屉加载失败保留列表，Tab 不进入背景', async ({ page }) => {
  await installPlatformFixtures(page)
  await page.route('**/api/career/conversations/conversation-1', route => route.fulfill({ status: 503, json: { detail: '读取失败' } }))
  await page.goto('/career')
  await page.locator('.history-trigger').click()
  await page.locator('.conversation-item-main').first().click()
  await expect(page.locator('.career-error-toast')).toContainText('读取失败')
  await expect(page.locator('.career-workspace')).toHaveClass(/compact-history-open/)
  const close = page.getByRole('button', { name: '关闭会话历史' })
  await close.focus()
  await page.keyboard.press('Shift+Tab')
  expect(await page.locator('#career-history-panel').evaluate(el => el.contains(document.activeElement))).toBe(true)
  await page.keyboard.press('Escape')
  await expect(page.locator('.history-trigger')).toBeFocused()
})

test('手机资料表单和主导航约束焦点，关闭返回原入口', async ({ page }) => {
  await installPlatformFixtures(page)
  await page.goto('/career')
  await expect(page.locator('.history-trigger')).toBeVisible()
  await openFixtureConversation(page)
  await page.getByRole('button', { name: '模型配置', exact: true }).click()
  const trigger = page.locator('.active-context-chip')
  await trigger.click()
  const dialog = page.getByRole('dialog', { name: '建立求职上下文', exact: true })
  await expect(dialog).toBeVisible()
  expect(await page.locator('.shell').evaluate(el => el.inert)).toBe(true)
  await page.setViewportSize({ width: 390, height: 430 })
  await expect.poll(async () => {
    const box = await dialog.locator('.context-dialog-footer').boundingBox()
    return box && box.y + box.height <= 431
  }).toBe(true)
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  await expect(trigger).toBeFocused()
  expect(await page.locator('.shell').evaluate(el => el.inert)).toBe(false)
  await page.setViewportSize({ width: 390, height: 844 })
  const menu = page.getByRole('button', { name: '打开导航', exact: true })
  await menu.click()
  const buttons = page.locator('#app-navigation .nav-item')
  await expect(buttons.first()).toBeFocused()
  await page.keyboard.press('Shift+Tab')
  await expect(buttons.last()).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(menu).toBeFocused()
  await page.getByRole('button', { name: '模型配置', exact: true }).click()
  await page.getByRole('button', { name: '模型连接', exact: true }).click()
  const models = page.getByRole('dialog', { name: '模型与连接管理', exact: true })
  await expect(models).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(models).toHaveCount(0)
})

test('手机匹配报告摘要、卡片证据和原文在单列中可核对', async ({ page }) => {
  await installPlatformFixtures(page)
  const assessment = JSON.parse(readFileSync(new URL('./fixtures/job-match-assessment.json', import.meta.url), 'utf8'))
  await page.route('**/api/career/conversations/conversation-1', route => route.fulfill({ json: {
    conversation: { id: 'conversation-1', title: '运营岗位匹配验收' }, messages: [], latest_turn: null,
    context: { candidate_profile: { id: 'candidate-1', display_name: '示例简历', version: 1 },
      target_role: { id: 'role-1', role_name: '运营专员', company_name: '示例公司' }, assessment }
  } }))
  await page.goto('/career')
  await expect(page.locator('.history-trigger')).toBeVisible()
  await openFixtureConversation(page)
  await page.getByRole('button', { name: '职位信息', exact: true }).click()
  await page.locator('.job-match-grid button').filter({ hasText: '核心要求' }).click()
  const dialog = page.getByRole('dialog', { name: '岗位匹配分析', exact: true })
  await expect(dialog).toBeVisible()
  await expectFits(page, 'dialog,.match-scroll,.match-content')
  await expect(dialog.locator('.match-mobile-evidence')).toContainText('独立策划活动并复盘')
  await page.screenshot({ path: 'test-results/mobile-match-report-compare.png', fullPage: true, animations: 'disabled' })
  await dialog.getByRole('button', { name: '定位简历原文 →' }).click()
  await expect(dialog.locator('.match-original.is-highlighted')).toHaveCount(1)
  await dialog.getByRole('button', { name: '计算规则', exact: true }).click()
  await expect(dialog.locator('.match-formula').first()).toContainText('75%')
  await page.screenshot({ path: 'test-results/mobile-match-report.png', fullPage: true, animations: 'disabled' })
  await dialog.getByRole('button', { name: '关闭岗位匹配分析' }).click()
  await expect(dialog).toHaveCount(0)
})

test('手机桌面专属功能显示原因，不启动采集和扩展连接', async ({ page }) => {
  await installPlatformFixtures(page)
  const calls = []
  page.on('request', request => { if (/online-assessment|interview-master/.test(new URL(request.url()).pathname) && request.url().includes('/api/')) calls.push(request.url()) })
  for (const path of ['/career/online-assessment','/career/interview-master','/interviews/jobs']) {
    await page.goto(path)
    await expect(page.locator('.desktop-capability-notice')).toBeVisible()
    await expect(page.locator('.desktop-capability-notice')).toContainText('电脑端')
    await expectFits(page, '.desktop-capability-notice')
    expect(await page.evaluate(() => innerWidth)).toBe(390)
  }
  expect(calls).toEqual([])
})

test('手机面经在列表与阅读间返回时保留搜索和当前节点', async ({ page }) => {
  await installPlatformFixtures(page)
  await page.goto('/interviews')
  await expect(page.locator('.tree-leaf')).toBeVisible()
  const search = page.getByPlaceholder('搜索公司或岗位')
  await search.fill('示例')
  await page.locator('.tree-leaf').click()
  await expect(page.locator('.interview-detail-pane')).toBeVisible()
  await expect(page.locator('.interview-tree-pane')).toBeHidden()
  await expect(page.locator('.markdown-reading')).toContainText('项目职责与技术取舍')
  await page.getByRole('button', { name: '返回面经列表' }).click()
  await expect(search).toHaveValue('示例')
  await expect(page.locator('.tree-leaf.active')).toHaveCount(1)
  await page.getByRole('button', { name: '导入面经', exact: true }).click()
  await page.getByRole('button', { name: '上传面经', exact: true }).click()
  const importDialog = page.getByRole('dialog', { name: '导入面经材料', exact: true })
  await expect(importDialog).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(importDialog).toHaveCount(0)
  await page.screenshot({ path: 'test-results/mobile-interview-library.png', fullPage: true, animations: 'disabled' })
})

test('手机简历日期筛选收纳后保留值，工作区标签正常切换', async ({ page }) => {
  await installPlatformFixtures(page)
  await page.goto('/resume-assistant')
  await expect(page.locator('#resume-start-date-filter')).toBeHidden()
  await page.getByRole('button', { name: '日期筛选', exact: true }).click()
  await page.getByLabel('开始日期').fill('2026-11-01')
  await page.getByRole('button', { name: '收起日期筛选' }).click()
  await page.getByRole('button', { name: /日期筛选/ }).click()
  await expect(page.getByLabel('开始日期')).toHaveValue('2026-11-01')
  await page.getByRole('button', { name: /新建优化任务/ }).click()
  await page.locator('#resume-file-input').setInputFiles({ name: 'resume.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 mobile test') })
  await page.getByLabel('岗位介绍').fill('负责 Agent 开发与验证')
  await page.getByRole('button', { name: '开始分析', exact: true }).click()
  await page.getByRole('tab', { name: '原始简历', exact: true }).click()
  await page.getByRole('tab', { name: '优化助手', exact: true }).click()
  await expect(page.getByText('强化 Agent 项目结果')).toBeVisible()
  await expectFits(page)
})

test('手机技能阅读与编辑切换保留内容，保存失败保留编辑', async ({ page }) => {
  await installPlatformFixtures(page)
  await page.route('**/api/skills/save', route => route.fulfill({ status: 503, json: { detail: '模拟保存失败' } }))
  await page.goto('/skills')
  await expect(page.locator('.installed-skill-item')).toBeVisible()
  await page.locator('.installed-skill-item').click()
  await expect(page.locator('.mobile-skill-reader')).toContainText('技能说明')
  await page.getByRole('button', { name: '编辑内容', exact: true }).click()
  const editor = page.getByRole('textbox', { name: '编辑 SKILL.md' })
  await editor.fill('# 修改后的内容\n\n保留这份草稿。')
  await page.getByRole('button', { name: '阅读当前内容', exact: true }).click()
  await expect(page.locator('.mobile-skill-reader')).toContainText('修改后的内容')
  await page.getByRole('button', { name: '编辑内容', exact: true }).click()
  await expect(editor).toHaveValue('# 修改后的内容\n\n保留这份草稿。')
  await page.locator('.skill-save-row button').click()
  await expect(page.getByRole('alert')).toContainText('模拟保存失败')
  await expect(page.getByRole('alert')).toBeInViewport()
  await expect(editor).toHaveValue('# 修改后的内容\n\n保留这份草稿。')
  await expectFits(page)
})

test('评测子路由直接访问和返回保持主任务，普通用户无管理权限', async ({ page }) => {
  await installPlatformFixtures(page)
  await page.goto('/evaluations')
  await page.getByRole('button', { name: '真实数据集', exact: true }).click()
  await expect(page).toHaveURL(/\/evaluations\/datasets$/)
  await expect(page.locator('.evaluation-heading h2')).toHaveText('真实数据集')
  await page.goBack()
  await expect(page.locator('.evaluation-heading h2')).toHaveText('评测总览')
  await page.goto('/evaluations/metrics')
  await expect(page.locator('.evaluation-heading h2')).toHaveText('指标字典')
  await expectFits(page)
  await installPlatformFixtures(page, { role: 'user' })
  await page.goto('/evaluations/datasets')
  await expect(page).toHaveURL(/\/career$/)
  await expect(page.locator('.evaluation-page')).toHaveCount(0)
})

test('全平台手机路由保持可读布局和无运行异常', async ({ page }) => {
  await installPlatformFixtures(page)
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  for (const path of ['/career','/resume-assistant','/interviews','/skills','/review','/review/article','/review/pipeline',
    '/review/assets','/review/history','/review/prompts','/evaluations','/evaluations/datasets','/evaluations/experiments',
    '/evaluations/metrics','/observability','/admin/modules','/admin/github','/admin/prompts','/admin/model-context']) {
    await page.goto(path)
    await expect(page.locator('.main .topbar')).toBeVisible()
    await expectFits(page)
    if (path === '/evaluations') {
      await expect(page.locator('.evaluation-chain')).toBeVisible()
      await page.screenshot({ path: 'test-results/mobile-evaluations.png', fullPage: true, animations: 'disabled' })
    }
  }
  expect(errors).toEqual([])
})

test('手机文章长正文与宽表格只在各自容器滚动', async ({ page }) => {
  await installPlatformFixtures(page)
  await page.route('**/api/preview/latest*', route => route.fulfill({ json: {
    content: { id: 1, title: '手机文章阅读验证', digest: '检查正文与宽表格。' },
    article_layout: { article_html: `<p>${'这是一段较长的正文。'.repeat(30)}</p><table style="width:1200px"><tbody><tr>${Array.from({ length: 8 }, (_, i) => `<td style="min-width:140px">指标 ${i + 1}</td>`).join('')}</tr></tbody></table>` }
  } }))
  await page.goto('/review/article')
  await expect(page.locator('.article-body.rendered table')).toBeVisible()
  await expectFits(page, '.main,.article-body')
  expect(await page.locator('.article-body table').evaluate(el => el.scrollWidth > el.clientWidth)).toBe(true)
})

test('手机认证表单在竖屏和横屏可操作', async ({ page }) => {
  await installPlatformFixtures(page)
  for (const [width,height] of [[360,800],[390,844],[844,390]]) {
    await page.setViewportSize({ width, height })
    await page.goto('/responsive-layout-preview.html?scenario=login')
    await expect(page.locator('.login-submit')).toBeVisible()
    await expectFits(page, '.login-page')
  }
})

test.describe('PC 全平台布局回归', () => {
  test.use({ isMobile: false, hasTouch: false, userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36' })
  test('PC 多尺寸核心路由保持可访问，窄窗口保留电脑能力', async ({ page }) => {
    await installPlatformFixtures(page)
    for (const [width,height] of [[900,420],[1024,600],[1093,614],[1280,720],[1440,900],[1920,1080]]) {
      await page.setViewportSize({ width, height })
      for (const path of ['/career','/resume-assistant','/interviews','/skills','/review','/evaluations','/admin/modules']) {
        await page.goto(path)
        await expect(page.locator('.main .topbar')).toBeVisible()
        await expectFits(page)
      }
    }
    await page.setViewportSize({ width: 900, height: 600 })
    await page.goto('/career/online-assessment')
    await expect(page.locator('.assessment-page')).toBeVisible()
    await expect(page.locator('.desktop-capability-notice')).toHaveCount(0)
    await expectFits(page, '.assessment-page')
  })

  test('紧凑 PC 笔试在题面、代码与测试间切换保留内容', async ({ page }) => {
    await installPlatformFixtures(page)
    await page.setViewportSize({ width: 900, height: 600 })
    await page.addInitScript(() => sessionStorage.setItem('career-online-assessment-session-v1', JSON.stringify({
      phase: 'ready', problem: { title: '两数之和', statement: '返回两个数的和。', problem_type: 'algorithm',
        language: 'python', interface_kind: 'function', function_signature: 'add(a, b)', confidence: 1, incomplete_reasons: [] },
      solution: { code: 'def add(a, b):\n    return a + b', answer_markdown: '直接相加。' }, tests: [], report: null
    })))
    await page.goto('/career/online-assessment')
    await page.getByRole('tab', { name: '代码', exact: true }).click()
    const code = page.locator('.code-editor textarea')
    await expect(code).toHaveValue('def add(a, b):\n    return a + b')
    await code.fill('def add(a, b):\n    return b + a')
    await page.getByRole('tab', { name: '测试', exact: true }).click()
    await expect(page.locator('.tests-panel')).toBeVisible()
    await page.getByRole('tab', { name: '代码', exact: true }).click()
    await expect(code).toHaveValue('def add(a, b):\n    return b + a')
    await expectFits(page, '.assessment-page,.assessment-workbench')
  })
})
