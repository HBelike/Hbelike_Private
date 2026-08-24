import {
  buildBossSearchPageUrl,
  buildCityEndpoint,
  buildDetailEndpoint,
  buildSearchEndpoint,
  classifyBossFailure,
  normalizeDetailResponse,
  normalizeCityResponse,
  normalizeSearchResponse,
  shouldSyncBossSearchPage,
  shouldRefreshBossSession
} from './boss-data.js'
import {
  buildBossChatUrl,
  CHAT_DELIVERY_EVIDENCE,
  classifyFriendAddResponse,
  normalizeGreetingPayload
} from './boss-greeting.js'

const WEB_CHANNEL = 'find-job-job-library-web-v1'
const ALLOWED_APP_HOSTS = new Set(['127.0.0.1', 'localhost', 'xingxingtech.cn', 'www.xingxingtech.cn'])
const BOSS_HOME = 'https://www.zhipin.com/web/geek/jobs?city=101020100&ka=open_joblist'
const REQUEST_GAP_MS = 1000
const SESSION_SETTLE_MS = 1500
let requestQueue = Promise.resolve()
let lastRequestAt = 0
let greetingTabId = null

function isAllowedSender(sender) {
  try {
    return ALLOWED_APP_HOSTS.has(new URL(sender.url ?? '').hostname)
  } catch {
    return false
  }
}

function wait(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}

async function waitForTabReady(tabId, timeoutMs = 15000) {
  const current = await chrome.tabs.get(tabId)
  if (current.status === 'complete') return current

  return new Promise((resolve, reject) => {
    const timeoutId = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener)
      reject(new Error('BOSS 页面加载超时，请检查网络后重试。'))
    }, timeoutMs)
    const listener = (updatedTabId, changeInfo, tab) => {
      if (updatedTabId !== tabId || changeInfo.status !== 'complete') return
      clearTimeout(timeoutId)
      chrome.tabs.onUpdated.removeListener(listener)
      resolve(tab)
    }
    chrome.tabs.onUpdated.addListener(listener)
  })
}

async function getBossTab(initialUrl = BOSS_HOME) {
  const tabs = await chrome.tabs.query({ url: 'https://www.zhipin.com/*' })
  const prioritizedTabs = tabs
    .filter((tab) => tab.id)
    .sort((left, right) => {
      const searchPageDifference = Number(isBossSearchPage(right.url)) - Number(isBossSearchPage(left.url))
      if (searchPageDifference) return searchPageDifference
      const activeDifference = Number(Boolean(right.active)) - Number(Boolean(left.active))
      if (activeDifference) return activeDifference
      const availableDifference = Number(!right.discarded) - Number(!left.discarded)
      if (availableDifference) return availableDifference
      return Number(right.lastAccessed ?? 0) - Number(left.lastAccessed ?? 0)
    })
  const readyTab = prioritizedTabs.find((tab) => tab.status === 'complete')
  if (readyTab) return readyTab
  const pendingTab = prioritizedTabs[0]
  if (pendingTab?.id) return waitForTabReady(pendingTab.id)

  const created = await chrome.tabs.create({ url: initialUrl, active: false })
  if (!created.id) throw new Error('无法打开 BOSS 直聘后台页面。')
  return waitForTabReady(created.id)
}

function isBossSearchPage(url) {
  try {
    return new URL(url ?? '').pathname === '/web/geek/jobs'
  } catch {
    return false
  }
}

function isBossBlockedPage(url) {
  try {
    const pathname = new URL(url ?? '').pathname
    return /verify|security-check/i.test(pathname) || /\/web\/user\/?$/.test(pathname)
  } catch {
    return false
  }
}

async function navigateBossTab(tabId, url, timeoutMs = 20000) {
  let listener
  let timeoutId
  const ready = new Promise((resolve, reject) => {
    listener = (updatedTabId, changeInfo, tab) => {
      if (updatedTabId !== tabId || changeInfo.status !== 'complete') return
      clearTimeout(timeoutId)
      chrome.tabs.onUpdated.removeListener(listener)
      resolve(tab)
    }
    timeoutId = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener)
      reject(new Error('BOSS 城市页面加载超时。'))
    }, timeoutMs)
    chrome.tabs.onUpdated.addListener(listener)
  })

  try {
    await chrome.tabs.update(tabId, { url, active: false })
  } catch (error) {
    clearTimeout(timeoutId)
    chrome.tabs.onUpdated.removeListener(listener)
    throw error
  }

  const tab = await ready
  await wait(SESSION_SETTLE_MS)
  return tab
}

async function syncBossSearchContext(tab, payload) {
  if (!tab?.id || isBossBlockedPage(tab.url)) return tab
  const cityCode = String(payload.cityCode ?? '').trim()
  if (!shouldSyncBossSearchPage(tab.url, cityCode)) return tab

  const targetUrl = buildBossSearchPageUrl(payload.query, cityCode)
  let syncedTab
  if (isBossSearchPage(tab.url)) {
    syncedTab = await navigateBossTab(tab.id, targetUrl)
  } else {
    const created = await chrome.tabs.create({ url: targetUrl, active: false })
    if (!created.id) throw new Error('无法打开所选城市的 BOSS 搜索页面。')
    syncedTab = await waitForTabReady(created.id, 20000)
    await wait(SESSION_SETTLE_MS)
  }

  return syncedTab
}

async function reloadBossTab(tabId, timeoutMs = 20000) {
  let listener
  let timeoutId
  const ready = new Promise((resolve, reject) => {
    listener = (updatedTabId, changeInfo, tab) => {
      if (updatedTabId !== tabId || changeInfo.status !== 'complete') return
      clearTimeout(timeoutId)
      chrome.tabs.onUpdated.removeListener(listener)
      resolve(tab)
    }
    timeoutId = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener)
      reject(new Error('BOSS 页面自动刷新超时。'))
    }, timeoutMs)
    chrome.tabs.onUpdated.addListener(listener)
  })

  try {
    await chrome.tabs.reload(tabId, { bypassCache: false })
  } catch (error) {
    clearTimeout(timeoutId)
    chrome.tabs.onUpdated.removeListener(listener)
    throw error
  }

  const tab = await ready
  await wait(SESSION_SETTLE_MS)
  return tab
}

async function respectRequestGap() {
  const elapsed = Date.now() - lastRequestAt
  if (elapsed < REQUEST_GAP_MS) await wait(REQUEST_GAP_MS - elapsed)
}

async function runBossFetch(tabId, endpoint) {
  const [{ result } = {}] = await chrome.scripting.executeScript({
    target: { tabId },
    world: 'MAIN',
    args: [endpoint],
    func: async (requestedEndpoint) => {
      const pageText = document.body?.innerText ?? ''
      if (/安全验证|环境存在异常|请稍候/.test(pageText) || /verify|security-check/i.test(location.pathname)) {
        return { kind: 'verification' }
      }
      if (/登录后继续|扫码登录|密码登录/.test(pageText) || /\/web\/user\/?$/.test(location.pathname)) {
        return { kind: 'login' }
      }

      const controller = new AbortController()
      const timeoutId = setTimeout(() => controller.abort(), 12000)
      try {
        const supportedEndpoint = /^\/wapi\/zpgeek\/(search\/joblist|job\/detail)\.json\?/.test(requestedEndpoint)
          || requestedEndpoint === '/wapi/zpgeek/common/data/city/site.json'
        if (!supportedEndpoint) {
          return { kind: 'network', message: '不支持的职位操作。' }
        }

        const response = await fetch(requestedEndpoint, {
          method: 'GET',
          credentials: 'include',
          cache: 'no-store',
          headers: { Accept: 'application/json, text/plain, */*' },
          signal: controller.signal
        })
        if (!response.ok) return { kind: 'http', status: response.status }
        return { kind: 'api', response: await response.json() }
      } catch (error) {
        return {
          kind: 'network',
          message: error instanceof Error && error.name === 'AbortError'
            ? 'BOSS 直聘请求超时，请稍后重试。'
            : (error instanceof Error ? error.message : 'BOSS 直聘请求失败。')
        }
      } finally {
        clearTimeout(timeoutId)
      }
    }
  })
  return result
}

async function runBossReadWithSingleRefresh(tabId, endpoint, action) {
  await respectRequestGap()
  let result = await runBossFetch(tabId, endpoint)
  lastRequestAt = Date.now()
  let failure = classifyBossFailure(result)
  if (!shouldRefreshBossSession(failure, action)) return { result, failure, refreshed: false }

  try {
    await reloadBossTab(tabId)
  } catch {
    return {
      result: null,
      failure: {
        code: 'session_refresh_failed',
        message: 'BOSS 页面自动刷新未完成，本次未执行发送或写入，可安全重试。',
        retryable: true
      },
      refreshed: true
    }
  }

  await respectRequestGap()
  result = await runBossFetch(tabId, endpoint)
  lastRequestAt = Date.now()
  failure = classifyBossFailure(result)
  if (shouldRefreshBossSession(failure, action)) {
    return {
      result,
      failure: {
        code: 'session_refresh_failed',
        message: 'BOSS 页面已自动刷新一次，但会话状态仍未更新；本次未执行发送或写入，可安全重试。',
        retryable: true
      },
      refreshed: true
    }
  }
  return { result, failure, refreshed: true }
}

async function runFriendAdd(tabId, payload) {
  const [{ result } = {}] = await chrome.scripting.executeScript({
    target: { tabId },
    world: 'MAIN',
    args: [payload],
    func: async (job) => {
      const pageText = document.body?.innerText ?? ''
      if (/安全验证|环境存在异常|请稍候/.test(pageText) || /verify|security-check/i.test(location.pathname)) {
        return { kind: 'verification' }
      }
      if (/登录后继续|扫码登录|密码登录/.test(pageText) || /\/web\/user\/?$/.test(location.pathname)) {
        return { kind: 'login' }
      }

      const controller = new AbortController()
      const timeoutId = setTimeout(() => controller.abort(), 12000)
      try {
        const tokenResponse = await fetch('/wapi/zppassport/get/zpToken', {
          credentials: 'include',
          cache: 'no-store',
          signal: controller.signal
        })
        if (!tokenResponse.ok) return { kind: 'http', status: tokenResponse.status }
        const tokenPayload = await tokenResponse.json()
        const token = String(tokenPayload?.zpData?.token ?? '').trim()
        if (!token) return { kind: 'login' }

        const endpoint = new URL('/wapi/zpgeek/friend/add.json', location.origin)
        endpoint.search = new URLSearchParams({
          securityId: job.securityId,
          lid: job.lid,
          jobId: job.jobId
        }).toString()
        const response = await fetch(endpoint, {
          method: 'POST',
          credentials: 'include',
          cache: 'no-store',
          headers: {
            Accept: 'application/json, text/plain, */*',
            zp_token: token
          },
          signal: controller.signal
        })
        if (!response.ok) return { kind: 'http', status: response.status }
        return { kind: 'api', response: await response.json() }
      } catch (error) {
        return {
          kind: 'network',
          message: error instanceof Error && error.name === 'AbortError'
            ? 'BOSS 建立沟通请求超时。'
            : (error instanceof Error ? error.message : 'BOSS 建立沟通请求失败。')
        }
      } finally {
        clearTimeout(timeoutId)
      }
    }
  })
  return result
}

async function getGreetingTab(url) {
  if (greetingTabId) {
    try {
      const current = await chrome.tabs.get(greetingTabId)
      if (current?.id) return navigateBossTab(current.id, url, 25000)
    } catch {
      greetingTabId = null
    }
  }
  const created = await chrome.tabs.create({ url, active: false })
  if (!created.id) throw new Error('无法打开 BOSS 聊天页面。')
  greetingTabId = created.id
  return waitForTabReady(created.id, 25000)
}

async function runChatSend(tabId, message) {
  const [{ result } = {}] = await chrome.scripting.executeScript({
    target: { tabId },
    world: 'MAIN',
    args: [message, CHAT_DELIVERY_EVIDENCE],
    func: async (finalMessage, evidenceRules) => {
      const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds))
      const normalized = String(finalMessage ?? '').trim()
      const classifyEvidence = (evidence = {}) => {
        const statusClasses = String(evidence.statusClasses ?? '').trim().slice(0, 800)
        const statusText = String(evidence.statusText ?? '').trim().slice(0, 300)
        if (new RegExp(evidenceRules.failureClassPattern, 'i').test(statusClasses)
          || new RegExp(evidenceRules.failureTextPattern).test(statusText)) {
          return 'failed'
        }
        if (new RegExp(evidenceRules.successClassPattern, 'i').test(statusClasses)
          || new RegExp(evidenceRules.successTextPattern).test(statusText)) {
          return 'sent'
        }
        if (evidence.isNew === true
          && evidence.inputCleared === true
          && Number(evidence.stableForMs) >= Number(evidenceRules.stableMs)) {
          return 'sent'
        }
        return 'pending'
      }
      const pageState = () => {
        const pageText = document.body?.innerText ?? ''
        if (/安全验证|环境存在异常|请稍候/.test(pageText) || /verify|security-check/i.test(location.pathname)) {
          return { code: 'verification_required', message: 'BOSS 要求安全验证。', stopBatch: true }
        }
        if (/登录后继续|扫码登录|密码登录/.test(pageText) || /\/web\/user\/?$/.test(location.pathname)) {
          return { code: 'login_required', message: 'BOSS 登录状态已失效。', stopBatch: true }
        }
        if (/操作过于频繁|沟通上限|沟通额度|已与\d+位BOSS沟通/.test(pageText)) {
          return { code: 'rate_limited', message: 'BOSS 限制了当前沟通频率。', stopBatch: true }
        }
        return null
      }
      const currentPageState = pageState()
      if (currentPageState) return currentPageState

      let chatInput = null
      let sendButton = null
      for (let attempt = 0; attempt < 200; attempt += 1) {
        const blocked = pageState()
        if (blocked) return blocked
        chatInput = document.querySelector('#chat-input')
        sendButton = document.querySelector('.chat-op .btn-send')
          || [...document.querySelectorAll('button,a')].find((element) => element.textContent?.trim() === '发送')
        if (chatInput && sendButton) break
        await sleep(100)
      }
      if (!chatInput || !sendButton) {
        return { code: 'chat_unavailable', message: 'BOSS 聊天输入框或发送按钮未加载。', stopBatch: true }
      }

      const outgoingMessages = () => [...document.querySelectorAll('.item-myself .message-text')]
        .filter((element) => element.textContent?.trim() === normalized)
      if (outgoingMessages().some((element) => {
        const row = element.closest('.item-myself')
        const status = row?.querySelector('.status,[class*="status-"]')
        return classifyEvidence({
          statusClasses: `${row?.className ?? ''} ${status?.className ?? ''}`,
          statusText: status?.textContent ?? ''
        }) === 'sent'
      })) {
        return { ok: true, status: 'already_sent' }
      }

      const existingOutgoingMessages = new Set(outgoingMessages())
      const firstSeenAt = new WeakMap()

      chatInput.focus()
      const selection = window.getSelection()
      const range = document.createRange()
      range.selectNodeContents(chatInput)
      selection?.removeAllRanges()
      selection?.addRange(range)
      let inserted = false
      try {
        inserted = document.execCommand('insertText', false, normalized)
      } catch {
        inserted = false
      }
      if (!inserted || chatInput.textContent?.trim() !== normalized) {
        chatInput.textContent = normalized
      }
      chatInput.dispatchEvent(new InputEvent('input', {
        bubbles: true,
        inputType: 'insertText',
        data: normalized
      }))
      chatInput.dispatchEvent(new Event('change', { bubbles: true }))
      await sleep(180)
      if (chatInput.textContent?.trim() !== normalized) {
        return { code: 'message_fill_failed', message: '招呼语未能写入 BOSS 聊天框。', stopBatch: true }
      }
      if (sendButton.disabled || sendButton.getAttribute('aria-disabled') === 'true') {
        return { code: 'send_disabled', message: 'BOSS 发送按钮当前不可用。', stopBatch: true }
      }

      sendButton.click()
      for (let attempt = 0; attempt < 150; attempt += 1) {
        const blocked = pageState()
        if (blocked) return blocked
        for (const element of outgoingMessages()) {
          const row = element.closest('.item-myself')
          const status = row?.querySelector('.status,[class*="status-"]')
          const isNew = !existingOutgoingMessages.has(element)
          if (isNew && !firstSeenAt.has(element)) firstSeenAt.set(element, Date.now())
          const evidence = classifyEvidence({
            isNew,
            inputCleared: !chatInput.textContent?.trim(),
            stableForMs: isNew ? Date.now() - firstSeenAt.get(element) : 0,
            statusClasses: `${row?.className ?? ''} ${status?.className ?? ''}`,
            statusText: status?.textContent ?? ''
          })
          if (evidence === 'sent') {
            return { ok: true, status: 'sent' }
          }
          if (evidence === 'failed') {
            return { code: 'send_failed', message: 'BOSS 明确返回发送失败。', stopBatch: true }
          }
        }
        await sleep(100)
      }
      return {
        code: 'send_unknown',
        message: '未能确认招呼语是否送达，已停止本批以避免重复发送。',
        stopBatch: true
      }
    }
  })
  return result
}

function greetingFailure(result) {
  if (!result) return { code: 'empty_response', message: 'BOSS 没有返回发送结果。', stopBatch: true }
  if (result.kind === 'verification') return { code: 'verification_required', message: 'BOSS 要求安全验证。', stopBatch: true }
  if (result.kind === 'login') return { code: 'login_required', message: '请先登录 BOSS。', stopBatch: true }
  if (result.kind === 'network') return { code: 'network_error', message: result.message || 'BOSS 网络请求失败。', stopBatch: true }
  if (result.kind === 'http') {
    return Number(result.status) === 429
      ? { code: 'rate_limited', message: 'BOSS 限制了当前沟通频率。', stopBatch: true }
      : { code: 'boss_http_error', message: `BOSS 返回 HTTP ${result.status || '异常状态'}。`, stopBatch: true }
  }
  return null
}

async function handleGreetingOperation(action, rawPayload) {
  let payload
  try {
    payload = normalizeGreetingPayload(rawPayload)
  } catch (error) {
    return { ok: false, error: { code: 'invalid_greeting', message: error.message, stopBatch: true } }
  }

  const bossTab = await getBossTab(BOSS_HOME)
  if (!bossTab?.id || isBossBlockedPage(bossTab.url)) {
    return { ok: false, error: { code: 'login_or_verification_required', message: '请检查 BOSS 登录或安全验证状态。', stopBatch: true } }
  }

  const detailRead = await runBossReadWithSingleRefresh(
    bossTab.id,
    buildDetailEndpoint(payload.securityId),
    'get_job_detail'
  )
  const detailResult = detailRead.result
  const detailFailure = detailRead.failure
  if (detailFailure) return { ok: false, error: { ...detailFailure, stopBatch: true } }
  const detail = normalizeDetailResponse(detailResult.response, payload)
  if (!detail.jobId || !detail.bossId || !detail.lid) {
    return { ok: false, error: { code: 'job_identifiers_missing', message: '岗位缺少聊天标识，请重新搜索并选择岗位。', stopBatch: true } }
  }
  if (action === 'preflight_greeting') {
    return { ok: true, data: { ready: true, job: { securityId: detail.securityId, jobId: detail.jobId, bossId: detail.bossId, lid: detail.lid } } }
  }

  await respectRequestGap()
  const friendResult = await runFriendAdd(bossTab.id, detail)
  lastRequestAt = Date.now()
  const transportFailure = greetingFailure(friendResult)
  if (transportFailure) return { ok: false, error: transportFailure }
  const friendFailure = classifyFriendAddResponse(friendResult.response)
  if (!friendFailure.ok) {
    if (friendFailure.stopBatch === false) {
      return { ok: true, data: { status: 'skipped', reason: friendFailure.message, code: friendFailure.code } }
    }
    return { ok: false, error: friendFailure }
  }

  const chatTab = await getGreetingTab(buildBossChatUrl({ ...detail, message: payload.message }))
  const sendResult = await runChatSend(chatTab.id, payload.message)
  if (!sendResult?.ok) return { ok: false, error: sendResult || { code: 'send_unknown', message: '发送结果未知。', stopBatch: true } }
  return {
    ok: true,
    data: {
      status: sendResult.status,
      defaultGreetingSent: friendFailure.defaultGreetingSent === true,
      sentAt: new Date().toISOString()
    }
  }
}

async function handleBossOperation(action, payload) {
  const endpoint = action === 'search_jobs'
    ? buildSearchEndpoint(payload.query, payload.page, payload.cityCode)
    : action === 'list_cities'
      ? buildCityEndpoint()
      : buildDetailEndpoint(payload.securityId)
  const initialUrl = action === 'search_jobs'
    ? buildBossSearchPageUrl(payload.query, payload.cityCode)
    : BOSS_HOME
  let tab = await getBossTab(initialUrl)
  if (action === 'search_jobs') {
    try {
      tab = await syncBossSearchContext(tab, payload)
    } catch {
      return {
        ok: false,
        error: {
          code: 'city_context_sync_failed',
          message: '未能把 BOSS 页面切换到所选城市。助手已停止本次搜索，请检查 BOSS 页面后重试。'
        }
      }
    }
  }
  const read = await runBossReadWithSingleRefresh(tab.id, endpoint, action)
  const { result, failure } = read

  if (failure) return { ok: false, error: failure }

  if (action === 'list_cities') {
    return { ok: true, data: normalizeCityResponse(result.response) }
  }
  if (action === 'search_jobs') {
    const data = normalizeSearchResponse(result.response, {
      query: payload.query,
      cityCode: String(payload.cityCode ?? '').trim(),
      cityName: String(payload.cityName ?? '').trim() || '上海',
      page: payload.page
    })
    if (data.rawJobCount > 0 && data.jobs.length === 0 && data.rejectedCityCount === data.rawJobCount) {
      return {
        ok: false,
        error: {
          code: 'city_mismatch',
          message: `BOSS 返回的岗位城市与“${data.city}”不一致，已停止展示。请稍后重新搜索或打开 BOSS 页面确认城市。`
        }
      }
    }
    return {
      ok: true,
      data
    }
  }
  return {
    ok: true,
    data: normalizeDetailResponse(result.response, payload.fallback ?? {})
  }
}

async function handleMessage(message, sender) {
  if (!isAllowedSender(sender) || message?.channel !== WEB_CHANNEL) {
    return { ok: false, error: { code: 'forbidden_sender', message: '当前页面不能调用职位库助手。' } }
  }
  if (message.action === 'ping') return { ok: true, data: { connected: true, version: chrome.runtime.getManifest().version } }
  if (['preflight_greeting', 'send_greeting'].includes(message.action)) {
    return handleGreetingOperation(message.action, message.payload ?? {})
  }
  if (!['list_cities', 'search_jobs', 'get_job_detail'].includes(message.action)) {
    return { ok: false, error: { code: 'unsupported_action', message: '职位库助手不支持该操作。' } }
  }
  return handleBossOperation(message.action, message.payload ?? {})
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  requestQueue = requestQueue
    .catch(() => undefined)
    .then(() => handleMessage(message, sender))
    .then(sendResponse)
    .catch((error) => sendResponse({
      ok: false,
      error: {
        code: 'extension_error',
        message: error instanceof Error ? error.message : '职位库助手运行失败。'
      }
    }))
  return true
})
