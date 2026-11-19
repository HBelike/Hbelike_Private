import { test, expect } from '@playwright/test'

test('PC可见性事件恢复时重新同步，旧超时不污染前台且隐藏期间可查历史', async ({ page }) => {
  const account = { id: 'visibility-account', platform: 'boss', platform_account_id: '100', display_name: '可见性验收账号' }
  const execution = { executor_id: 'fixture', revision: 1, paused: false }
  const conversation = { id: 'visibility-conversation', account_id: account.id, platform_conversation_id: '200', peer_id: '200', peer_name: '可见性验收 HR', security_id: 'browser-only', source_url: 'https://www.zhipin.com/web/geek/chat?id=200' }
  const message = { id: 'visibility-message', platform_message_id: 'saved', platform_conversation_id: '200', sender_id: '200', direction: 'incoming', message_type: 'text', text: '平台已保存的历史回复', sent_at: new Date().toISOString() }
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.route('**/api/career/external-chat/**', (route) => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/accounts')) return route.fulfill({ json: { accounts: [account] } })
    if (path.endsWith('/conversations')) return route.fulfill({ json: { conversations: [conversation] } })
    if (path.endsWith('/messages')) return route.fulfill({ json: { messages: [message], has_more: false } })
    if (path.endsWith('/sync')) return route.fulfill({ json: { account, imported_messages: 0, imported_conversations: 1 } })
    if (path.endsWith('/operations')) return route.fulfill({ json: { operations: [] } })
    return route.fulfill({ json: { execution } })
  })
  await page.addInitScript(({ account, conversation }) => {
    window.visibilityCalls = []
    window.visibilityHoldNextState = false
    window.visibilityOldReply = null
    window.addEventListener('message', (event) => {
      const request = event.data
      if (request?.channel !== 'find-job-job-library-web-v1') return
      window.visibilityCalls.push(request.action)
      const reply = (data, error) => window.postMessage({ channel: 'find-job-job-library-extension-v1', requestId: request.requestId, ok: !error, data, error }, window.location.origin)
      if (request.action === 'ping') return reply({ connected: true, capabilities: ['boss_chat_read', 'boss_chat_execute_v1'] })
      if (request.action === 'boss_chat_state') {
        if (window.visibilityHoldNextState) {
          window.visibilityHoldNextState = false
          window.visibilityOldReply = () => reply(null, { code: 'request_timeout', message: '隐藏前的旧读取超时' })
          return
        }
        return reply({ account })
      }
      if (request.action === 'boss_chat_list') return reply({ account, conversations: [conversation], has_more: false })
      if (request.action === 'boss_chat_messages') return reply({ account, conversation, messages: [], has_more: false })
      return reply(null, { code: 'unexpected_action', message: '可见性验收不允许写入 BOSS。' })
    })
  }, { account, conversation })
  await page.goto('/e2e/fixtures/boss-inbox.html')
  await expect(page.getByText('自动接收已开启', { exact: true })).toBeVisible()
  const initialStates = await page.evaluate(() => window.visibilityCalls.filter((action) => action === 'boss_chat_state').length)
  await page.evaluate(() => { window.visibilityHoldNextState = true })
  await page.getByRole('button', { name: '立即同步', exact: true }).click()
  await expect.poll(() => page.evaluate(() => typeof window.visibilityOldReply)).toBe('function')

  // 在浏览器页面内模拟 visibility 状态，触发组件实际注册的事件处理器。
  async function setHidden(hidden) {
    await page.evaluate((value) => {
      Object.defineProperty(document, 'hidden', { configurable: true, value })
      Object.defineProperty(document, 'visibilityState', { configurable: true, value: value ? 'hidden' : 'visible' })
      document.dispatchEvent(new Event('visibilitychange'))
    }, hidden)
  }
  await setHidden(true)
  await page.getByRole('button', { name: /可见性验收 HR/ }).click()
  await expect(page.getByText('平台已保存的历史回复', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => window.visibilityCalls.includes('boss_chat_messages'))).toBe(false)

  await setHidden(false)
  await expect.poll(() => page.evaluate(() => window.visibilityCalls.filter((action) => action === 'boss_chat_state').length)).toBe(initialStates + 2)
  await expect(page.getByRole('button', { name: '立即同步', exact: true })).toBeEnabled()
  await page.evaluate(() => window.visibilityOldReply())
  await expect(page.getByText('自动接收已开启', { exact: true })).toBeVisible()
  await expect(page.getByText('隐藏前的旧读取超时', { exact: true })).toHaveCount(0)
  await expect(page.getByText('平台已保存的历史回复', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => window.visibilityCalls.filter((action) => action === 'boss_chat_list').length)).toBe(2)
})
