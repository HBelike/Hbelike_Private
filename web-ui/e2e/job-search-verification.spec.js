import { test, expect } from '@playwright/test'

async function installFixture(page, cachedFirst = false) {
  await page.addInitScript(({ cachedFirst }) => {
    window.detailCalls = []
    window.verificationBlocked = true
    const jobs = Array.from({ length: 15 }, (_, index) => ({
      id: `job-${index}`, securityId: `job-${index}`, title: `测试工程师${index}`, company: '测试公司',
      companyShort: '测', city: '杭州', salary: '20-30K', experience: '3-5年', degree: '本科', skills: [], welfare: [],
      description: cachedFirst && index === 0 ? '已缓存的完整JD' : '',
      sourceUrl: `https://www.zhipin.com/job_detail/job-${index}.html`
    }))
    window.addEventListener('message', ({ data: request }) => {
      if (request?.channel !== 'find-job-job-library-web-v1') return
      let data = {}
      let error
      if (request.action === 'ping') data = { connected: true, version: '0.6.1' }
      if (request.action === 'list_cities') data = { cities: [{ code: '101210100', name: '杭州' }] }
      if (request.action === 'search_jobs') {
        if (request.payload.page > 1 && window.verificationBlocked) error = { code: 'verification_required', message: '请在 BOSS 页面完成安全验证后重试。' }
        else data = { jobs: request.payload.page > 1 ? [] : jobs, hasMore: request.payload.page === 1 }
      }
      if (request.action === 'get_job_detail') {
        window.detailCalls.push(request.payload.id)
        if (window.verificationBlocked) error = { code: 'verification_required', message: '请在 BOSS 页面完成安全验证后重试。' }
        else data = { ...jobs.find((job) => job.id === request.payload.id), description: '验证解除后获取到的完整JD' }
      }
      window.postMessage({ channel: 'find-job-job-library-extension-v1', requestId: request.requestId, ok: !error, data, error }, window.location.origin)
    })
  }, { cachedFirst })
}

test('详情受阻保留15个结果，加载更多失败不报空结果，用户重试后恢复', async ({ page }) => {
  await installFixture(page)
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto('/e2e/fixtures/job-search-card.html')
  await expect(page.locator('.job-card')).toHaveCount(15)
  await expect(page.locator('.detail-error')).toBeVisible()
  await expect(page.locator('.freshness')).toContainText('详情获取受阻')
  await expect(page.locator('.freshness')).not.toContainText('详情已更新')
  await expect(page.getByRole('button', { name: '设为目标岗位' })).toBeDisabled()
  await page.getByRole('button', { name: '加载更多岗位' }).click()
  await expect(page.locator('.job-alert')).toContainText('后续操作未完成，已保留现有岗位')
  await expect(page.getByText('本次没有获取到岗位', { exact: true })).toHaveCount(0)
  await expect(page.locator('.job-card')).toHaveCount(15)
  await page.locator('.connection-badge').click()
  await expect(page.locator('.connection-badge')).toContainText('需要处理 BOSS 页面')
  expect(await page.evaluate(() => window.detailCalls.length)).toBe(1)

  await page.evaluate(() => { window.verificationBlocked = false })
  await page.getByRole('button', { name: '重新获取详情' }).click()
  await expect(page.locator('.job-description')).toHaveText('验证解除后获取到的完整JD')
  await expect(page.locator('.detail-error')).toHaveCount(0)
  await expect(page.locator('.freshness')).toContainText('完整详情已获取')
  await expect(page.locator('.connection-badge')).toContainText('助手已连接')
  await expect(page.getByRole('button', { name: '设为目标岗位' })).toBeEnabled()
  await page.getByRole('button', { name: '加载更多岗位' }).click()
  await expect(page.locator('.job-alert')).toHaveCount(0)
  await expect(page.locator('.job-card')).toHaveCount(15)
})

test('预取遇到验证后停止，查看缓存详情和重新握手不伪装为验证已解除', async ({ page }) => {
  await installFixture(page, true)
  await page.goto('/e2e/fixtures/job-search-card.html')
  await expect(page.locator('.job-card')).toHaveCount(15)
  await expect(page.locator('.connection-badge')).toContainText('需要处理 BOSS 页面')
  await page.locator('.job-card').first().click()
  await expect(page.locator('.job-description')).toHaveText('已缓存的完整JD')
  await page.locator('.connection-badge').click()
  await expect(page.locator('.connection-badge')).toContainText('需要处理 BOSS 页面')
  expect(await page.evaluate(() => window.detailCalls.length)).toBe(1)
})
