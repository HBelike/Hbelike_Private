import { test, expect } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

const ACCOUNT = { id: 'cloud-account', platform: 'boss', platform_account_id: '9007199254740993123', display_name: '验收账号' }
const sourceAccount = { platform: ACCOUNT.platform, platform_account_id: ACCOUNT.platform_account_id, display_name: ACCOUNT.display_name }
const NOW = new Date('2026-11-09T08:00:00Z')
const conversation = (n = 1) => ({ id: `cloud-conversation-${n}`, account_id: ACCOUNT.id, platform_conversation_id: `boss-conversation-${n}`, peer_id: `hr-${n}`, peer_name: n === 1 ? '林女士' : '陈先生', company: n === 1 ? '本地验收甲公司' : '本地验收乙公司', job_title: '后端开发工程师', unread_count: n === 1 ? 1 : null, last_message_at: NOW.toISOString(), last_message_preview: n === 1 ? '想了解你的项目经历' : '方便沟通吗', source_url: `https://www.zhipin.com/web/geek/chat?id=hr-${n}` })
const message = (id, text, n = 1, type = 'text', time = NOW.toISOString()) => ({ id: `cloud-message-${id}`, platform_message_id: id, platform_conversation_id: `boss-conversation-${n}`, sender_id: `hr-${n}`, direction: 'incoming', message_type: type, text, sent_at: time })

async function prepare(page, { mode = 'ready', delayedFirst = false, selectFirst = true, pagedHistory = false, sourcePages = false } = {}) {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.clock.install({ time: NOW })
  const saved = [message('old', '已保存的 HR 回复', 1, 'text', new Date(NOW.getTime() - 60_000).toISOString()), message('media', '', 1, 'image', new Date(NOW.getTime() - 30_000).toISOString()), message('second', '乙公司的已有回复', 2), message('expired', '过期正文不得展示', 1, 'text', '2026-09-01T00:00:00Z')]
  if (pagedHistory) saved.splice(0, saved.length, ...Array.from({ length: 130 }, (_, index) => message(`history-${String(index + 1).padStart(3, '0')}`, `历史消息 ${String(index + 1).padStart(3, '0')}`, 1, 'text', new Date(NOW.getTime() - (130 - index) * 60_000).toISOString())))
  const syncBodies = []
  const operations = []
  let execution = { executor_id: '', revision: 1, paused: false, expires_at: new Date(NOW.getTime() + 90_000).toISOString() }
  await page.route('**/api/career/external-chat/**', async (route) => {
    const url = new URL(route.request().url())
    const body = route.request().postDataJSON()
    if (url.pathname.endsWith('/execution')) {
      if (mode === 'history_only' && !body.takeover) return route.fulfill({ status: 409, json: { detail: { code: 'execution_busy', message: '该账号正在另一页面执行，请手动接管。' } } })
      execution = { ...execution, executor_id: body.executor_id, revision: body.takeover ? execution.revision + 1 : execution.revision }
      return route.fulfill({ json: { execution } })
    }
    if (url.pathname.endsWith('/execution/check')) return route.fulfill(execution.paused ? { status: 409, json: { detail: { code: 'account_paused', message: execution.pause_reason } } } : { json: { execution } })
    if (url.pathname.endsWith('/execution/release')) return route.fulfill({ json: {} })
    if (url.pathname.endsWith('/pause')) { execution.paused = true; execution.pause_reason = body.message; return route.fulfill({ json: { execution } }) }
    if (url.pathname.endsWith('/resume')) { execution.paused = false; return route.fulfill({ json: { execution } }) }
    if (url.pathname.endsWith('/operations')) {
      if (route.request().method() === 'POST') {
        const operation = { ...body, account_id: ACCOUNT.id, conversation_id: url.pathname.split('/').at(-2), status: 'pending', created_at: NOW.toISOString(), expires_at: new Date(NOW.getTime() + 30_000).toISOString() }
        operations.push(operation)
        return route.fulfill({ json: { operation } })
      }
      return route.fulfill({ json: { operations } })
    }
    if (/\/operations\/[^/]+(?:\/transition|\/cancel)?$/.test(url.pathname)) {
      const id = url.pathname.split('/').at(url.pathname.endsWith('/transition') || url.pathname.endsWith('/cancel') ? -2 : -1)
      const operation = operations.find((item) => item.client_operation_id === id)
      if (operation && url.pathname.endsWith('/transition')) operation.status = body.status
      if (operation && url.pathname.endsWith('/cancel')) operation.status = operation.status === 'pending' ? 'cancelled' : 'unknown'
      return route.fulfill({ json: { operation } })
    }
    if (url.pathname.endsWith('/sync')) {
      const body = route.request().postDataJSON()
      syncBodies.push(body)
      for (const value of body.messages) if (!saved.some((item) => item.platform_message_id === value.platform_message_id)) saved.push({ ...value, id: `cloud-message-${value.platform_message_id}` })
      return route.fulfill({ json: { account: ACCOUNT, imported_messages: body.messages.length, imported_conversations: body.conversations.length } })
    }
    if (url.pathname.endsWith('/accounts')) return route.fulfill({ json: { accounts: [ACCOUNT] } })
    if (url.pathname.endsWith('/conversations')) return route.fulfill({ json: { conversations: [conversation(), conversation(2)] } })
    const n = url.pathname.includes('cloud-conversation-2') ? 2 : 1
    const eligible = saved.filter((item) => item.platform_conversation_id === `boss-conversation-${n}`)
    if (pagedHistory) {
      const end = url.searchParams.has('before') ? eligible.findIndex((item) => item.id === url.searchParams.get('before')) : eligible.length
      const start = Math.max(0, end - 100)
      return route.fulfill({ json: { messages: eligible.slice(start, end), has_more: start > 0 } })
    }
    return route.fulfill({ json: { messages: eligible, has_more: false } })
  })
  await page.addInitScript(({ mode, delayedFirst, sourceAccount, now, conversations, sourcePages }) => {
    window.inboxCalls = []
    window.inboxMode = mode
    window.inboxNewReply = false
    window.inboxDelayFirst = delayedFirst
    window.addEventListener('message', (event) => {
      const request = event.data
      if (request?.channel !== 'find-job-job-library-web-v1') return
      window.inboxCalls.push(request.action)
      const reply = (data, error = null) => window.postMessage({ channel: 'find-job-job-library-extension-v1', requestId: request.requestId, ok: !error, ...(error ? { error } : { data }) }, window.location.origin)
      if (request.action === 'ping') return reply({ connected: window.inboxMode !== 'offline', version: '0.6.0', capabilities: window.inboxMode === 'old' ? [] : ['boss_chat_read', 'boss_chat_execute_v1', 'boss_chat_reply'] })
      if (request.action === 'boss_chat_resume' || request.action === 'boss_chat_cancel') return reply({})
      if (request.action === 'boss_chat_reply') {
        void fetch(`/api/career/external-chat/operations/${request.payload.clientOperationId}/transition`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'unknown' }) }).then(() => reply({ submissionState: 'unknown' }))
        return
      }
      if (window.inboxMode === 'verification') return reply(null, { code: 'verification_required', message: '请先在 BOSS 页面完成安全验证。' })
      if (window.inboxMode === 'schema') return reply(null, { code: 'chat_schema_changed', message: 'BOSS 会话结构发生变化，请手动核对后继续。', stopBatch: true })
      if (request.action === 'boss_chat_state') return reply({ account: { ...sourceAccount, platform_account_id: window.inboxMode === 'mismatch' ? 'other-account' : sourceAccount.platform_account_id }, observed_at: now })
      if (request.action === 'boss_chat_list') return reply({ account: sourceAccount, conversations: conversations.map((item) => ({ ...item, security_id: `security-${item.peer_id}` })), observed_at: now, has_more: false })
      if (request.action === 'boss_chat_messages') {
        const second = request.payload.peerId === 'hr-2'
        const data = { account: sourceAccount, conversation: { platform_conversation_id: `boss-conversation-${second ? 2 : 1}`, peer_id: request.payload.peerId }, messages: window.inboxNewReply && !second ? [{ platform_message_id: 'hr-new', platform_conversation_id: 'boss-conversation-1', direction: 'incoming', sender_id: 'hr-1', message_type: 'text', text: '这是 HR 刚刚发来的新回复', sent_at: new Date().toISOString() }] : [], has_more: false, observed_at: now }
        if (sourcePages && !second) {
          const start = ((request.payload.page || 1) - 1) * 50
          data.messages = Array.from({ length: Math.max(0, Math.min(50, 120 - start)) }, (_, index) => ({ platform_message_id: `source-${start + index + 1}`, platform_conversation_id: 'boss-conversation-1', direction: 'incoming', sender_id: 'hr-1', message_type: 'text', text: `源历史 ${start + index + 1}`, sent_at: new Date(Date.parse(now) - (start + index + 1) * 1000).toISOString() }))
          data.has_more = start + 50 < 120
        }
        if (window.inboxDelayFirst && !second) { window.inboxPendingFirst = (error) => error ? reply(null, error) : reply({ ...data, messages: [{ platform_message_id: 'late', platform_conversation_id: 'boss-conversation-1', direction: 'incoming', sender_id: 'hr-1', message_type: 'text', text: '第一会话迟到的消息', sent_at: now }] }); return }
        return reply(data)
      }
      return reply(null, { code: 'unexpected_action', message: '此验收不允许写入 BOSS。' })
    })
  }, { mode, delayedFirst, sourceAccount, sourcePages, now: NOW.toISOString(), conversations: [conversation(), conversation(2)] })
  await page.goto('/e2e/fixtures/boss-inbox.html')
  await expect(page.getByRole('button', { name: /林女士/ })).toBeVisible()
  if (selectFirst) await page.getByRole('button', { name: /林女士/ }).click()
  return { syncBodies, saved, operations }
}

test('PC招聘沟通显示历史、媒体占位和30秒新HR回复，重新进入仍保留且没有BOSS写操作', async ({ page }) => {
  const { syncBodies } = await prepare(page, { selectFirst: false })
  await expect(page.getByText('自动接收已开启')).toBeVisible()
  expect(await page.evaluate(() => window.inboxCalls.includes('boss_chat_messages'))).toBe(false)
  await page.getByRole('button', { name: /林女士/ }).click()
  await expect(page.getByText('已保存的 HR 回复', { exact: true })).toBeVisible()
  await expect(page.getByText('图片消息', { exact: true })).toBeVisible()
  await expect(page.getByText('过期正文不得展示')).toHaveCount(0)
  await expect(page.getByRole('link', { name: '到 BOSS 回复' })).toHaveAttribute('href', 'https://www.zhipin.com/web/geek/chat?id=hr-1')
  await page.evaluate(() => { window.inboxNewReply = true })
  await page.clock.runFor(30_100)
  await expect(page.getByText('这是 HR 刚刚发来的新回复')).toBeVisible()
  expect(syncBodies.some((body) => body.messages.some((item) => item.platform_message_id === 'hr-new'))).toBe(true)
  expect(syncBodies.every((body) => !JSON.stringify(body).includes('security-'))).toBe(true)
  expect(await page.evaluate(() => window.inboxCalls.some((action) => ['send_greeting', 'retry_greeting_message', 'preflight_greeting', 'reply_message'].includes(action)))).toBe(false)
  const screenshotDirectory = fileURLToPath(new URL('../../output/boss-inbox/', import.meta.url))
  await mkdir(screenshotDirectory, { recursive: true })
  await page.screenshot({ path: `${screenshotDirectory}/boss-inbox-pc-simulated.png` })
  await page.reload()
  await page.getByRole('button', { name: /林女士/ }).click()
  await expect(page.getByText('这是 HR 刚刚发来的新回复')).toBeVisible()
})

test('PC旧扩展与未连接状态明确提示，仍可查看平台历史', async ({ page }) => {
  for (const mode of ['old', 'offline']) {
    await prepare(page, { mode })
    await expect(page.getByText('已保存的 HR 回复', { exact: true })).toBeVisible()
    await expect(page.getByText(mode === 'old' ? '需要更新浏览器助手' : '浏览器助手未连接', { exact: true })).toBeVisible()
    expect(await page.evaluate(() => window.inboxCalls.filter((action) => action !== 'ping'))).toEqual([])
  }
})

test('PC账号不符及验证码会暂停接收，时间经过不再轮询，手动处理后继续', async ({ page }) => {
  for (const mode of ['mismatch', 'verification', 'schema']) {
    await prepare(page, { mode })
    await expect(page.getByRole('alert')).toContainText('接收已暂停')
    const count = await page.evaluate(() => window.inboxCalls.length)
    await page.clock.runFor(60_100)
    expect(await page.evaluate(() => window.inboxCalls.length)).toBe(count)
    await page.evaluate(() => { window.inboxMode = 'ready' })
    await page.getByRole('button', { name: '重新检测并继续' }).click()
    await expect(page.getByText('自动接收已开启')).toBeVisible()
    await expect(page.getByRole('alert')).toHaveCount(0)
  }
})

test('PC切换会话后第一会话迟到正文不会覆盖或写入第二会话', async ({ page }) => {
  const { syncBodies } = await prepare(page, { delayedFirst: true })
  await expect.poll(() => page.evaluate(() => typeof window.inboxPendingFirst)).toBe('function')
  await page.getByRole('button', { name: /陈先生/ }).click()
  await expect(page.getByText('乙公司的已有回复')).toBeVisible()
  await page.evaluate(() => window.inboxPendingFirst())
  await expect(page.getByText('第一会话迟到的消息')).toHaveCount(0)
  await expect(page.locator('.conversation-heading h3')).toHaveText('陈先生')
  expect(syncBodies.some((body) => body.messages.some((item) => item.platform_message_id === 'late'))).toBe(false)
})

test('PC切会话后前一会话迟到限流暂停同账号，当前历史保留且不再轮询', async ({ page }) => {
  await prepare(page, { delayedFirst: true })
  await expect.poll(() => page.evaluate(() => typeof window.inboxPendingFirst)).toBe('function')
  await page.getByRole('button', { name: /陈先生/ }).click()
  await expect(page.getByText('乙公司的已有回复')).toBeVisible()
  await page.evaluate(() => window.inboxPendingFirst({ code: 'rate_limited', message: 'BOSS 已限制当前账号读取频率。', stopBatch: true }))
  await expect(page.getByRole('alert')).toContainText('接收已暂停')
  await expect(page.getByText('乙公司的已有回复')).toBeVisible()
  const count = await page.evaluate(() => window.inboxCalls.length)
  await page.clock.runFor(60_100)
  expect(await page.evaluate(() => window.inboxCalls.length)).toBe(count)
})

test('PC加载更早130条历史后自动接收新回复，保留旧正文及最早分页边界', async ({ page }) => {
  await prepare(page, { pagedHistory: true })
  await expect(page.locator('.external-message')).toHaveCount(100)
  await page.getByRole('button', { name: '加载更早的已保存消息' }).click()
  await expect(page.locator('.external-message')).toHaveCount(130)
  await expect(page.getByRole('button', { name: '加载更早的已保存消息' })).toHaveCount(0)
  await page.evaluate(() => { window.inboxNewReply = true })
  await page.clock.runFor(30_100)
  await expect(page.locator('.external-message')).toHaveCount(131)
  await expect(page.getByText('历史消息 001', { exact: true })).toHaveCount(1)
  await expect(page.getByText('这是 HR 刚刚发来的新回复')).toBeVisible()
  await expect(page.getByRole('button', { name: '加载更早的已保存消息' })).toHaveCount(0)
})

test('PC源平台120条历史跨三页入库，且仅选中会话读取正文', async ({ page }) => {
  const { syncBodies } = await prepare(page, { sourcePages: true })
  await expect.poll(() => syncBodies.flatMap((item) => item.messages).filter((item) => item.platform_message_id.startsWith('source-')).length).toBe(120)
  await expect(page.getByText('源历史 120', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => window.inboxCalls.filter((item) => item === 'boss_chat_messages').length)).toBe(3)
})

test('PC其他执行端占用时仅查历史，手动接管后才读取源会话', async ({ page }) => {
  await prepare(page, { mode: 'history_only' })
  await expect(page.getByText('仅查看历史', { exact: true })).toBeVisible()
  await expect(page.getByText('已保存的 HR 回复', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => window.inboxCalls.includes('boss_chat_list'))).toBe(false)
  await page.getByRole('button', { name: '在此接管', exact: true }).click()
  await expect(page.getByText('自动接收已开启')).toBeVisible()
})

test('PC逐条确认生成独立操作，未知结果不提供旧操作重发', async ({ page }) => {
  const { operations } = await prepare(page)
  await page.getByRole('textbox', { name: '回复 林女士' }).fill('方便，稍后补充项目说明。')
  await page.getByRole('button', { name: '确认发送', exact: true }).click()
  await expect(page.getByText('结果待核对', { exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '到 BOSS 核对', exact: true })).toBeVisible()
  expect(operations).toHaveLength(1)
  await page.reload()
  await page.getByRole('button', { name: /林女士/ }).click()
  await expect(page.getByText('结果待核对', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => window.inboxCalls.includes('boss_chat_reply'))).toBe(false)
  await page.getByRole('textbox', { name: '回复 林女士' }).fill('方便，稍后补充项目说明。')
  await page.getByRole('button', { name: '确认发送', exact: true }).click()
  await expect.poll(() => operations.length).toBe(2)
  expect(operations[0].client_operation_id).not.toBe(operations[1].client_operation_id)
})
