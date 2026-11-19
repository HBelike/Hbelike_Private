import { test, expect } from '@playwright/test'

test.use({ baseURL: process.env.CAREER_CARD_TEST_URL || 'http://127.0.0.1:5173' })

async function mockExtension(page, connected = true) {
  await page.addInitScript(({ connected }) => {
    window.searchCalls = []
    window.addEventListener('message', (event) => {
      const request = event.data
      if (request?.channel !== 'find-job-job-library-web-v1') return
      const job = { id: 'job-1', title: 'Java 开发', salary: '20-30K', experience: '3-5年', degree: '本科', city: '杭州', company: '测试公司', companyShort: '测试公司', skills: ['Java'], welfare: [], description: '负责服务端业务开发。' }
      let data = {}
      if (request.action === 'ping') data = { connected, version: '0.4.0' }
      if (request.action === 'list_cities') data = { cities: [{ code: '101210100', name: '杭州' }] }
      if (request.action === 'search_jobs') { window.searchCalls.push(request.payload); data = { jobs: [job], hasMore: false } }
      if (request.action === 'get_job_detail') data = job
      window.postMessage({ channel: 'find-job-job-library-extension-v1', requestId: request.requestId, ok: true, data }, window.location.origin)
    })
  }, { connected })
}

test('对话内自动搜索、显示详情且只有明确确认才提交岗位', async ({ page }) => {
  await mockExtension(page)
  await page.goto('/e2e/fixtures/job-search-card.html')
  await expect(page.locator('.job-card')).toHaveCount(1)
  await expect.poll(() => page.evaluate(() => window.searchCalls.length)).toBe(1)
  await expect(page.getByRole('button', { name: '设为目标岗位' })).toBeEnabled()
  expect(await page.evaluate(() => window.cardConfirmed)).toBeNull()
  await page.getByRole('button', { name: '设为目标岗位' }).click()
  expect((await page.evaluate(() => window.cardConfirmed)).id).toBe('job-1')
  for (const width of [1280, 900, 390]) {
    await page.setViewportSize({ width, height: 900 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  }
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.screenshot({ path: '../tmp/chat-job-search-card-pc.png', fullPage: true })
})

test('缺少扩展自动搜索只显示卡内提示，不打开安装弹窗', async ({ page }) => {
  await mockExtension(page, false)
  await page.goto('/e2e/fixtures/job-search-card.html')
  await expect(page.getByRole('button', { name: '重新检测', exact: true })).toBeVisible()
  await expect(page.locator('[aria-modal="true"]')).toHaveCount(0)
  expect(await page.evaluate(() => window.searchCalls.length)).toBe(0)
})

test('恢复历史卡片不自动搜索且旧卡只显示摘要', async ({ page }) => {
  await mockExtension(page)
  await page.goto('/e2e/fixtures/job-search-card.html')
  await expect(page.locator('.job-card')).toHaveCount(1)
  await page.evaluate(() => { window.cardTestState.fresh = false; window.cardTestState.active = false })
  await expect(page.locator('.job-workspace')).toHaveCount(0)
  await expect(page.getByText('已找到 1 个岗位')).toBeVisible()
  expect(await page.evaluate(() => window.searchCalls.length)).toBe(1)
})
