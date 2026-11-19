import { test, expect } from '@playwright/test'

async function prepare(page, responses) {
  const generated = []
  await page.addInitScript(() => {
    window.greetingExtensionCalls = []
    window.addEventListener('message', (event) => {
      if (event.data?.channel !== 'find-job-job-library-web-v1') return
      const request = event.data
      window.greetingExtensionCalls.push(request.action)
      if (request.action === 'ping') window.postMessage({ channel: 'find-job-job-library-extension-v1', requestId: request.requestId, ok: true, data: { connected: false } }, window.location.origin)
    })
  })
  await page.route('**/api/career/greetings/generate', async (route) => {
    const input = route.request().postDataJSON()
    const message = responses[Math.min(generated.length, responses.length - 1)]
    generated.push(input)
    return route.fulfill({ json: { job_key: input.job.id, message, resume_evidence: [], jd_highlights: [], model: { model_id: 'mock-model' } } })
  })
  await page.goto('/e2e/fixtures/greeting-message-limit.html')
  await expect(page.locator('#greeting-message')).toBeEnabled()
  return generated
}

test('PC粘贴201字可模拟发送，200字建议不会形成门槛且没有扩展发送', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await prepare(page, ['有效的招呼语'])
  const input = page.locator('#greeting-message')
  const send = page.getByRole('button', { name: /^确认并模拟发送/ })
  await input.fill('中'.repeat(201))
  await expect(page.locator('.message-meta')).toContainText('建议 200 字左右')
  await expect(page.locator('.message-meta')).toContainText('当前 201 字')
  await expect(page.locator('#greeting-message-validation')).toHaveCount(0)
  await expect(send).toBeEnabled()
  expect(await page.evaluate(() => window.greetingExtensionCalls.filter((action) => ['send_greeting', 'retry_greeting_message', 'preflight_greeting'].includes(action)))).toEqual([])
  await input.fill('  ')
  await expect(page.locator('#greeting-message-validation')).toContainText('不能为空')
  await expect(send).toBeDisabled()
  await input.fill('中'.repeat(201))
  await expect(send).toBeEnabled()
  await send.click()
  await expect(page.locator('.sending-stage')).toBeVisible()
  await expect(page.locator('.sending-track .track-item.sent')).toHaveCount(1)
  expect(await page.evaluate(() => window.greetingExtensionCalls.filter((action) => ['send_greeting', 'retry_greeting_message', 'preflight_greeting'].includes(action)))).toEqual([])
})

test('emoji按codepoint计数，首尾空白不计入最终正文且手机无横溢', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await prepare(page, ['😀'.repeat(200)])
  const input = page.locator('#greeting-message')
  await expect(page.locator('.message-meta')).toContainText('当前 200 字')
  await expect(page.getByRole('button', { name: /^确认并模拟发送/ })).toBeEnabled()
  await input.fill('😀'.repeat(201))
  await expect(page.locator('.message-meta')).toContainText('当前 201 字')
  await expect(page.getByRole('button', { name: /^确认并模拟发送/ })).toBeEnabled()
  await input.fill('  ' + '😀'.repeat(200) + '  ')
  await expect(page.locator('.message-meta')).toContainText('当前 200 字')
  await expect(page.getByRole('button', { name: /^确认并模拟发送/ })).toBeEnabled()
  expect((await input.inputValue()).length).toBe(404)
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await expect(page.locator('.message-meta')).toContainText('当前 200 字')
  expect(await page.evaluate(() => window.greetingExtensionCalls.filter((action) => ['send_greeting', 'retry_greeting_message', 'preflight_greeting'].includes(action)))).toEqual([])
})

test('模型返回201字保留全文且可发送，仍可按原文重新生成', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  const generated = await prepare(page, ['长'.repeat(201), '重新生成的合规文案'])
  const input = page.locator('#greeting-message')
  const send = page.getByRole('button', { name: /^确认并模拟发送/ })
  await expect(input).toHaveValue('长'.repeat(201))
  await expect(page.locator('.message-meta')).toContainText('当前 201 字')
  await expect(page.locator('#greeting-message-validation')).toHaveCount(0)
  await expect(send).toBeEnabled()
  await page.getByRole('button', { name: '重新生成这一条', exact: true }).click()
  await expect(input).toHaveValue('重新生成的合规文案')
  await expect(send).toBeEnabled()
  expect(generated).toHaveLength(2)
  expect(generated[1].previous_message).toBe('长'.repeat(201))
  expect(await page.evaluate(() => window.greetingExtensionCalls.filter((action) => ['send_greeting', 'retry_greeting_message', 'preflight_greeting'].includes(action)))).toEqual([])
})
