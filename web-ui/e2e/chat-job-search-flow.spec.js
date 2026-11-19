import { test, expect } from '@playwright/test'
import { installApiFixtures } from './support/api-fixtures.js'

const conversation = { id: 'conversation-1', title: 'Agent 工程师求职准备', updated_at: '2026-11-04T10:00:00+08:00' }
const stamp = '2026-11-04T10:00:00+08:00'
const message = (id, role, content, extra = {}) => ({ id, role, content, created_at: stamp, ...extra })
const card = (id, keyword = 'Java 开发', city = '杭州') => message(id, 'assistant', '已准备好搜索条件。', {
  ui_payload: { type: 'job_search', version: 1, status: 'editing', conditions: { keyword, city, city_source: city ? 'user' : null }, auto_search: true }
})

function validJobSummary(job) {
  const limits = { id: 8192, title: 240, company: 240, salary: 120, city: 120,
    experience: 120, education: 120, skills: 600, description_excerpt: 2000 }
  return job && typeof job === 'object' && Object.entries(job).every(([key, value]) => (
    key in limits && typeof value === 'string' && Array.from(value).length <= limits[key]
  ))
}

async function installChatApi(page, initialMessages = [], extensionJobs = null) {
  await installApiFixtures(page)
  const state = { messages: structuredClone(initialMessages), context: null, rounds: [], patches: [], selectedRoles: [], submissions: 0, rejectedPatches: [], turnSnapshots: [], beforePatch: null }
  await page.addInitScript(() => {
    window.actualJobSearches = []
    window.addEventListener('message', (event) => {
      if (event.data?.channel === 'find-job-job-library-web-v1' && event.data.action === 'search_jobs') window.actualJobSearches.push(event.data.payload)
    })
  })
  if (extensionJobs) await page.addInitScript((jobs) => {
    // 覆盖预览页固定岗位响应；只使用合成数据，不访问用户招聘数据。
    window.addEventListener('message', (event) => {
      const request = event.data
      if (event.source !== window || request?.channel !== 'find-job-job-library-web-v1'
        || !['search_jobs', 'get_job_detail'].includes(request.action)) return
      event.stopImmediatePropagation()
      const data = request.action === 'search_jobs' ? { jobs, hasMore: false }
        : jobs.find((job) => job.securityId === request.payload.securityId) || jobs[0]
      window.postMessage({ channel: 'find-job-job-library-extension-v1', requestId: request.requestId, ok: true, data }, window.location.origin)
    })
  }, extensionJobs)
  await page.route('**/api/career/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()
    if (path === '/api/career/conversations/conversation-1') {
      return route.fulfill({ json: { conversation, context: state.context, messages: state.messages, latest_turn: null, last_model_selection: null } })
    }
    if (path.endsWith('/turns') && method === 'POST') {
      state.turnSnapshots.push(structuredClone(state.messages))
      const input = route.request().postDataJSON()
      const round = state.rounds[state.submissions++]
      expect(round, '每次提交都有预设服务端回复').toBeTruthy()
      round.user = message(`user-${state.submissions}`, 'user', input.text)
      round.turn = { id: `turn-${state.submissions}`, conversation_id: conversation.id, status: 'queued', conversation_position: 0, queue_sequence: state.submissions }
      return route.fulfill({ json: { turn: round.turn } })
    }
    const events = path.match(/\/turns\/(turn-\d+)\/events$/)
    if (events) {
      const round = state.rounds.find((item) => item.turn?.id === events[1])
      const payload = { turn: { ...round.turn, status: 'succeeded' }, message: round.user, assistant_message: round.assistant, updated_messages: round.updated || [] }
      state.messages.push(round.user, round.assistant)
      for (const updated of payload.updated_messages) state.messages = state.messages.map((item) => item.id === updated.id ? updated : item)
      return route.fulfill({ contentType: 'text/event-stream', body: `event: done\ndata: ${JSON.stringify(payload)}\n\n` })
    }
    const patchCard = path.match(/\/messages\/([^/]+)\/job-search$/)
    if (patchCard && method === 'PATCH') {
      const patch = route.request().postDataJSON()
      // 镜像服务端摘要字段约束；无效请求真实返回422，不能无条件伪造保存成功。
      const summary = patch.result_summary
      const invalid = (summary && (!Array.isArray(summary.jobs) || summary.jobs.length > 10
        || !Number.isInteger(summary.count) || summary.count < summary.jobs.length
        || !summary.jobs.every(validJobSummary)))
        || (patch.selected_job && !validJobSummary(patch.selected_job))
      if (invalid) {
        state.rejectedPatches.push(patch)
        return route.fulfill({ status: 422, json: { detail: '卡片字段格式或长度无效' } })
      }
      if (state.beforePatch) await state.beforePatch(patch)
      const current = state.messages.find((item) => item.id === patchCard[1])
      expect(current).toBeTruthy()
      current.ui_payload = { ...current.ui_payload, ...patch, conditions: { ...current.ui_payload.conditions, ...(patch.conditions || {}) } }
      state.patches.push({ id: current.id, patch })
      return route.fulfill({ json: { message: current } })
    }
    if (path === '/api/career/target-role-profiles' && method === 'POST') {
      state.selectedRoles.push(route.request().postDataJSON())
      return route.fulfill({ json: { id: 'new-role', role_name: 'Agent 软件开发工程师', company_name: '示例科技有限公司' } })
    }
    if (path.endsWith('/context') && method === 'POST') {
      state.context = { candidate_profile: null, target_role: { id: 'new-role', role_name: 'Agent 软件开发工程师', company_name: '示例科技有限公司' }, job_assessment: null }
      return route.fulfill({ json: state.context })
    }
    return route.fallback()
  })
  return state
}

async function openChat(page) {
  await page.goto('/responsive-layout-preview.html?scenario=career')
  if (page.viewportSize()?.width <= 640) await page.getByRole('button', { name: '历史', exact: true }).click()
  await page.locator('.conversation-item-main').filter({ hasText: conversation.title }).click()
  if (page.viewportSize()?.width <= 640) await expect(page.locator('.career-workspace')).not.toHaveClass(/compact-history-open/)
}

async function send(page, text) {
  await page.locator('.composer textarea').fill(text)
  await page.locator('.send-button').click()
  await expect(page.locator('.send-button')).toBeEnabled()
}

for (const analysisFails of [false, true]) test(`v2保存全部岗位并发送当前预览引用，完成后自动分析${analysisFails ? '并重试失败推荐' : ''}`, async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  const jobs = Array.from({ length: 15 }, (_, index) => ({ id: `job-${index}`, securityId: `job-${index}`, title: `Agent开发${index + 1}`, company: '合成公司', companyShort: '合成公司', city: '杭州', salary: '20K', degree: '本科', experience: '1-3年', skills: [], welfare: [], description: index === 11 ? '岗位要求12\n熟悉 Agent 工作流和 RAG 检索，具备后端开发经验。\n' + 'LangGraphAgentWorkflow'.repeat(12) + '\n能够说明项目中的职责、证据与可迁移能力。'.repeat(12) : `岗位要求${index + 1}` }))
  const state = await installChatApi(page, [], jobs)
  const reply = card('v2-card')
  reply.ui_payload.version = 2
  reply.ui_payload.auto_analyze = true
  state.rounds.push({ assistant: reply }, { assistant: message('focused-reply', 'assistant', '已分析当前岗位。') })
  let batch = null
  let savedJobs = []
  let submittedView = null
  let failFocusSave = false
  let analysisRetries = 0
  await page.route('**/api/career/conversations/conversation-1/job-search/batches**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()
    const body = method === 'GET' ? {} : route.request().postDataJSON()
    if ((body.jobs && (!Array.isArray(body.jobs) || body.jobs.length > 50 || body.jobs.some((job) => String(job.description || '').length > 30000))) || (method !== 'GET' && method !== 'PATCH' && !body.request_id)) return route.fulfill({ status: 422, json: { detail: '批次操作格式无效' } })
    if (path.endsWith('/batches') && method === 'GET') return route.fulfill({ json: { batches: batch ? [batch] : [] } })
    if (path.endsWith('/batches')) batch = { id: 'batch-1', batch_no: 1, source_message_id: 'v2-card', revision: 0, conditions: body.conditions }
    else if (path.endsWith('/pages')) {
      savedJobs = body.jobs.map((job, index) => ({ ...job, job_ref: `S1-J${index + 1}` }))
      batch = { ...batch, revision: batch.revision + 1, loaded_count: savedJobs.length }
    } else if (path.endsWith('/details')) batch = { ...batch, revision: batch.revision + 1 }
    else if (path.endsWith('/focus')) {
      if (failFocusSave) { failFocusSave = false; return route.fulfill({ status: 503, json: { detail: '模拟保存失败' } }) }
      batch = { ...batch, focused_job_ref: body.job_ref }
    }
    else if (path.endsWith('/jobs')) return route.fulfill({ json: { batch, jobs: savedJobs, next_cursor: null } })
    else if (path.endsWith('/complete')) {
      batch = { ...batch, analysis_turn_id: 'auto-analysis' }
      return route.fulfill({ json: { batch, analysis_turn: { id: 'auto-analysis' } } })
    } else if (path.endsWith('/analysis/retry')) {
      analysisRetries++
      batch = { ...batch, analysis_turn_id: 'auto-analysis-retry' }
      return route.fulfill({ json: { batch, analysis_turn: { id: 'auto-analysis-retry' } } })
    }
    return route.fulfill({ json: { batch, revision: batch.revision,
      job_refs: Object.fromEntries(savedJobs.map((job) => [job.jobId || job.id || job.securityId, job.job_ref])) } })
  })
  await page.route('**/api/career/turns/auto-analysis*/events', (route) => {
    const failed = analysisFails && !route.request().url().includes('auto-analysis-retry')
    if (failed) return route.fulfill({ contentType: 'text/event-stream', body: `event: error\ndata: ${JSON.stringify({ state: 'failed', detail: '模型暂时不可用' })}\n\n` })
    const turnId = route.request().url().includes('auto-analysis-retry') ? 'auto-analysis-retry' : 'auto-analysis'
    const answer = message('automatic-answer', 'assistant', '本批岗位自动推荐已完成。', { turn_id: turnId })
    if (!state.messages.some((item) => item.id === answer.id)) state.messages.push(answer)
    return route.fulfill({ contentType: 'text/event-stream',
      body: `event: delta\ndata: ${JSON.stringify({ content: '自动推荐生成中' })}\n\nevent: done\ndata: ${JSON.stringify({ source: 'job_search_auto_analysis', state: 'succeeded', message: null, assistant_message: answer })}\n\n` })
  })
  await page.route('**/api/career/conversations/conversation-1/turns', async (route) => {
    submittedView = route.request().postDataJSON().job_search_view
    return route.fallback()
  })
  await openChat(page)
  await send(page, '帮我找杭州Java岗位并推荐适合我的')
  await expect(page.locator('.career-job-search-card .job-card')).toHaveCount(15)
  await expect.poll(() => savedJobs.length).toBe(15)
  if (analysisFails) {
    await expect(page.getByRole('button', { name: '重试推荐', exact: true })).toBeVisible()
    expect(state.submissions).toBe(1)
    await page.getByRole('button', { name: '重试推荐', exact: true }).click()
    await expect.poll(() => analysisRetries).toBe(1)
  }
  await expect(page.getByText('本批岗位自动推荐已完成。', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => window.actualJobSearches.length)).toBe(1)
  expect(state.submissions).toBe(1)
  await expect.poll(() => batch.focused_job_ref).toBe('S1-J1')
  failFocusSave = true
  await page.locator('.career-job-search-card .job-card').nth(11).click()
  await expect(page.getByText(/岗位结果尚未保存成功|模拟保存失败/).first()).toBeVisible()
  await page.locator('.composer textarea').fill('这个岗位适合我吗')
  await page.locator('.send-button').click()
  await expect(page.locator('.composer textarea')).toHaveValue('这个岗位适合我吗')
  expect(state.submissions).toBe(1)
  await page.getByRole('button', { name: '重试保存', exact: true }).click()
  await expect.poll(() => batch.focused_job_ref).toBe('S1-J12')
  await send(page, '这个岗位适合我吗')
  expect(submittedView.focused_job_ref).toBe('S1-J12')
  expect(submittedView.active_batch_id).toBe('batch-1')
  expect(state.selectedRoles).toHaveLength(0)
  const invalid = await page.evaluate(async () => (await fetch('/api/career/conversations/conversation-1/job-search/batches/batch-1/pages', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: 'invalid', jobs: Array.from({ length: 51 }, () => ({ id: 'x' })) }) })).status)
  expect(invalid).toBe(422)
  await page.reload()
  await page.locator('.conversation-item-main').filter({ hasText: conversation.title }).click()
  await expect(page.locator('.career-job-search-card .job-workspace')).toHaveCount(0)
  expect(await page.evaluate(() => window.actualJobSearches.length)).toBe(0)
  await expect(page.getByText('本批岗位自动推荐已完成。', { exact: true })).toHaveCount(1)
  await expect(page.getByText('自动推荐生成中', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: /第 1 批/ }).click()
  await expect(page.locator('[aria-label="已保存岗位"] li')).toHaveCount(15)
  await page.getByRole('button', { name: /S1-J12 · Agent开发12/ }).click()
  await expect(page.getByRole('heading', { name: 'Agent开发12', exact: true })).toBeVisible()
  const archive = page.locator('.job-archive-grid')
  expect(await archive.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true)
  await archive.scrollIntoViewIfNeeded()
  await page.screenshot({ path: '../tmp/chat-job-search-v2-pc.png', fullPage: true })
  await page.locator('.career-job-search-card').screenshot({ path: '../tmp/chat-job-search-v2-card-pc.png' })
})

test('真实聊天页发送后搜索、保存状态、确认岗位和刷新恢复', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  const state = await installChatApi(page)
  state.rounds.push({ assistant: card('card-1') })
  await openChat(page)
  await send(page, '帮我找杭州 Java 开发岗位')
  const liveCard = page.locator('.career-job-search-card')
  await expect(liveCard.locator('.job-card')).toHaveCount(1)
  await expect.poll(() => state.patches.some((item) => item.patch.status === 'searched')).toBe(true)
  await expect(liveCard.locator('.job-workspace')).toBeVisible()
  expect(await page.evaluate(() => window.actualJobSearches.length)).toBe(1)
  expect(state.selectedRoles).toHaveLength(0)
  await liveCard.getByRole('button', { name: '设为目标岗位' }).click()
  await expect(liveCard.getByText('目标岗位已确认', { exact: false })).toBeVisible()
  expect(state.selectedRoles).toHaveLength(1)
  await expect(page.getByText('目标岗位已设置，后续回答将使用该岗位；添加简历后可分析匹配情况。')).toBeVisible()
  await page.reload()
  await page.locator('.conversation-item-main').filter({ hasText: conversation.title }).click()
  await expect(page.locator('.career-job-search-card .job-workspace')).toHaveCount(0)
  await expect(page.locator('.career-job-search-card')).toContainText('目标岗位已确认')
  expect(await page.evaluate(() => window.actualJobSearches.length)).toBe(0)
})

test('已搜岗位的JD保存完成后才提交追问，刷新后仍有匹配依据', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  const state = await installChatApi(page)
  state.rounds.push({ assistant: card('card-followup') },
    { assistant: message('reply-followup', 'assistant', '根据已获取岗位与简历给出初筛。') })
  let releaseSave
  const saved = new Promise((resolve) => { releaseSave = resolve })
  let waitingForSave = false
  state.beforePatch = async (patch) => {
    if (patch.result_summary?.jobs.some((job) => job.description_excerpt)) {
      waitingForSave = true
      await saved
    }
  }
  await openChat(page)
  await send(page, '帮我找杭州 Java 开发岗位')
  await expect.poll(() => waitingForSave).toBe(true)
  await page.locator('.composer textarea').fill('刚才这些岗位我适合哪些？')
  await page.locator('.send-button').click()
  await expect(page.locator('.send-button')).toBeDisabled()
  expect(state.submissions).toBe(1)
  releaseSave()
  await expect.poll(() => state.submissions).toBe(2)
  const persisted = state.turnSnapshots[1].find((item) => item.id === 'card-followup')
  expect(persisted.ui_payload.result_summary.jobs[0].description_excerpt).toBeTruthy()
  expect(state.selectedRoles).toHaveLength(0)
  await expect(page.locator('.send-button')).toBeEnabled()
  await page.reload()
  await page.locator('.conversation-item-main').filter({ hasText: conversation.title }).click()
  expect(state.messages.find((item) => item.id === 'card-followup').ui_payload.result_summary.jobs[0].description_excerpt).toBeTruthy()
  expect(await page.evaluate(() => window.actualJobSearches.length)).toBe(0)
})

test('新搜索卡激活，旧卡摘要只读且搜后PATCH不卸载实时结果', async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1300 })
  const old = card('old-card')
  old.ui_payload.status = 'searched'
  old.ui_payload.result_summary = { count: 1, jobs: [{ id: 'old-job', title: '旧岗位', company: '旧公司', salary: '20K' }] }
  const state = await installChatApi(page, [old])
  state.rounds.push({ assistant: card('new-card', 'Python 开发', '上海') })
  await openChat(page)
  await expect(page.locator('.career-job-search-card')).toContainText('旧岗位')
  expect(await page.evaluate(() => window.actualJobSearches.length)).toBe(0)
  await send(page, '再找上海 Python 开发岗位')
  await expect(page.locator('.career-job-search-card')).toHaveCount(2)
  await expect(page.locator('.career-job-search-card').first().locator('button')).toHaveCount(0)
  await expect(page.locator('.career-job-search-card').last().locator('.job-card')).toHaveCount(1)
  await expect.poll(() => state.patches.some((item) => item.id === 'new-card' && item.patch.status === 'searched')).toBe(true)
  await expect(page.locator('.career-job-search-card').last().locator('.job-workspace')).toBeVisible()
  await page.locator('.career-job-search-card').last().scrollIntoViewIfNeeded()
  await page.screenshot({ path: '../tmp/chat-job-search-main-pc.png', fullPage: true })
  await page.locator('.career-job-search-card').last().screenshot({ path: '../tmp/chat-job-search-main-card-pc.png' })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test('updated_messages 更新原有编辑卡，清空城市不残留旧条件', async ({ page }) => {
  const editing = card('editing-card', '', '杭州')
  editing.ui_payload.auto_search = false
  const state = await installChatApi(page, [editing])
  const updated = structuredClone(editing)
  updated.ui_payload.conditions = { keyword: 'Java 开发', city: null, city_source: null }
  state.rounds.push({ assistant: message('update-reply', 'assistant', '已更新搜索条件。'), updated: [updated] })
  await openChat(page)
  await expect(page.locator('.career-job-search-card .city-picker-trigger')).toContainText('杭州')
  await send(page, '岗位改成 Java 开发，先清空城市')
  await expect(page.locator('.career-job-search-card')).toHaveCount(1)
  await expect(page.locator('.career-job-search-card input[type="search"]')).toHaveValue('Java 开发')
  await expect(page.locator('.career-job-search-card .city-picker-trigger')).toContainText('请选择城市')
  expect(await page.evaluate(() => window.actualJobSearches.length)).toBe(0)
})

async function mockLocation(page, permission, fail = false) {
  await page.addInitScript(({ permission, fail }) => {
    window.locationReads = 0
    Object.defineProperty(navigator, 'geolocation', { configurable: true, value: {
      getCurrentPosition(success, error) {
        window.locationReads += 1
        if (fail) error({ code: 2 })
        else success({ coords: { latitude: 30.2741, longitude: 120.1551 } })
      }
    } })
    const queryPermission = navigator.permissions.query.bind(navigator.permissions)
    navigator.permissions.query = (descriptor) => descriptor.name === 'geolocation'
      ? Promise.resolve({ state: permission }) : queryPermission(descriptor)
  }, { permission, fail })
  const requests = []
  await page.route('https://api.bigdatacloud.net/data/reverse-geocode-client**', async (route) => {
    requests.push(route.request().url())
    return route.fulfill({ json: { city: '杭州市' } })
  })
  return requests
}

test('已有定位授权时自动预填城市，恢复历史卡不自动搜索', async ({ page }) => {
  const geoRequests = await mockLocation(page, 'granted')
  const locatingCard = card('locating-card', 'Java 开发', null)
  locatingCard.ui_payload.auto_search = false
  const state = await installChatApi(page, [locatingCard])
  await openChat(page)
  await expect(page.locator('.career-job-search-card .city-picker-trigger')).toContainText('杭州')
  await expect(page.getByRole('button', { name: '重新定位' })).toBeVisible()
  expect(await page.evaluate(() => window.locationReads)).toBe(1)
  expect(geoRequests).toHaveLength(1)
  await expect.poll(() => state.patches.some((item) => item.patch.conditions?.city_source === 'location')).toBe(true)
  expect(await page.evaluate(() => window.actualJobSearches.length)).toBe(0)
})

test('定位未授权时不读取坐标、不默认上海，用户启用后才定位', async ({ page }) => {
  const geoRequests = await mockLocation(page, 'prompt')
  const locatingCard = card('prompt-card', 'Java 开发', null)
  locatingCard.ui_payload.auto_search = false
  await installChatApi(page, [locatingCard])
  await openChat(page)
  await expect(page.getByRole('button', { name: '启用定位服务' })).toBeVisible()
  await expect(page.locator('.career-job-search-card .city-picker-trigger')).toContainText('请选择城市')
  expect(await page.evaluate(() => window.locationReads)).toBe(0)
  expect(geoRequests).toHaveLength(0)
  await page.getByRole('button', { name: '启用定位服务' }).click()
  await expect(page.locator('.career-job-search-card .city-picker-trigger')).toContainText('杭州')
  expect(await page.evaluate(() => window.locationReads)).toBe(1)
  expect(geoRequests).toHaveLength(1)
})

test('定位失败时保留空城市，用户可以手动选择城市', async ({ page }) => {
  const geoRequests = await mockLocation(page, 'granted', true)
  const locatingCard = card('failed-location', 'Java 开发', null)
  locatingCard.ui_payload.auto_search = false
  await installChatApi(page, [locatingCard])
  await openChat(page)
  await expect(page.getByText('暂时无法获取当前位置，可以重试定位，或手动选择城市。')).toBeVisible()
  await page.locator('.career-job-search-card .city-picker-trigger').click()
  await page.locator('.career-job-search-card .hot-city-list').getByRole('button', { name: '杭州', exact: true }).click()
  await expect(page.locator('.career-job-search-card .city-picker-trigger')).toContainText('杭州')
  expect(geoRequests).toHaveLength(0)
  expect(await page.evaluate(() => window.actualJobSearches.length)).toBe(0)
})

for (const viewport of [{ name: 'PC', width: 1440, height: 1000 }, { name: '手机', width: 390, height: 844 }]) {
  test(`15个长securityId岗位可保存搜索摘要和确认岗位（${viewport.name}）`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    const jobs = Array.from({ length: 15 }, (_, index) => {
      const securityId = `security-${index}-` + 'x'.repeat(480)
      return {
        id: securityId, securityId, jobId: index % 2 ? `job-stable-${index}` : undefined,
        title: `Java 开发 ${index + 1}`, company: '合成测试公司', companyShort: '合成测试公司',
        salary: '20-30K', city: '杭州', experience: '3-5年', degree: '本科', skills: ['Java'],
        welfare: [], description: `完整合成岗位描述-${index}`, sourceUrl: 'https://www.zhipin.com/'
      }
    })
    const state = await installChatApi(page, [], jobs)
    state.rounds.push({ assistant: card('long-id-card') })
    await openChat(page)
    await send(page, '帮我找杭州 Java 开发岗位')
    const liveCard = page.locator('.career-job-search-card')
    await expect(liveCard.locator('.job-card')).toHaveCount(15)
    await expect.poll(() => state.patches.some((item) => item.patch.status === 'searched')).toBe(true)
    const saved = state.patches.find((item) => item.patch.status === 'searched').patch.result_summary
    expect(saved.count).toBe(15)
    expect(saved.jobs).toHaveLength(10)
    expect(saved.jobs[0].id).toBe(jobs[0].securityId)
    expect(saved.jobs[1].id).toBe('job-stable-1')
    expect(state.rejectedPatches).toHaveLength(0)
    await expect(liveCard.locator('.job-chat-error')).toHaveCount(0)

    // 验证这套浏览器API模拟确实拒绝超限数据，而不是永远返回成功。
    const rejected = await page.evaluate(async () => {
      const response = await fetch('/api/career/conversations/conversation-1/messages/long-id-card/job-search', {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ selected_job: { id: 'x'.repeat(8193) } })
      })
      return response.status
    })
    expect(rejected).toBe(422)
    expect(state.rejectedPatches).toHaveLength(1)

    await expect(liveCard.getByRole('button', { name: '设为目标岗位' })).toBeEnabled()
    await liveCard.getByRole('button', { name: '设为目标岗位' }).click()
    await expect(liveCard).toContainText('目标岗位已确认')
    const selected = state.patches.find((item) => item.patch.status === 'selected').patch.selected_job
    expect(selected.id).toBe(jobs[0].securityId)
    expect(state.rejectedPatches).toHaveLength(1)
    expect(state.selectedRoles).toHaveLength(1)
    expect(state.selectedRoles[0].job_text).toContain(jobs[0].description)
    await expect(liveCard.locator('.job-chat-error')).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  })
}
