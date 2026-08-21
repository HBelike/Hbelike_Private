import {
  buildBossSearchPageUrl,
  buildCityEndpoint,
  buildDetailEndpoint,
  buildSearchEndpoint,
  classifyBossFailure,
  isBossSessionRefreshCoolingDown,
  normalizeDetailResponse,
  normalizeCityResponse,
  normalizeSearchResponse,
  shouldSyncBossSearchPage,
  shouldRefreshBossSession
} from './boss-data.js'

const WEB_CHANNEL = 'find-job-job-library-web-v1'
const ALLOWED_APP_HOSTS = new Set(['127.0.0.1', 'localhost', 'xingxingtech.cn', 'www.xingxingtech.cn'])
const BOSS_HOME = 'https://www.zhipin.com/web/geek/jobs?city=101020100&ka=open_joblist'
const REQUEST_GAP_MS = 1000
const SESSION_REFRESH_COOLDOWN_MS = 2 * 60 * 1000
const SESSION_SETTLE_MS = 1500
const SESSION_REFRESH_STORAGE_KEY = 'bossLastSessionRefreshAt'
let requestQueue = Promise.resolve()
let lastRequestAt = 0
let lastSessionRefreshAt = 0

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

  // 城市切换已经完成一次正常页面加载，避免紧接着因 code=37 再次自动刷新。
  await rememberSessionRefreshAt(Date.now())
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

async function readLastSessionRefreshAt() {
  try {
    const stored = await chrome.storage.session.get(SESSION_REFRESH_STORAGE_KEY)
    const value = Number(stored?.[SESSION_REFRESH_STORAGE_KEY])
    if (Number.isFinite(value) && value > 0) lastSessionRefreshAt = value
  } catch {
    // 存储不可用时仍保留当前 Service Worker 生命周期内的冷却限制。
  }
  return lastSessionRefreshAt
}

async function rememberSessionRefreshAt(value) {
  lastSessionRefreshAt = value
  try {
    await chrome.storage.session.set({ [SESSION_REFRESH_STORAGE_KEY]: value })
  } catch {
    // 单次重试边界不依赖存储；存储仅用于跨 Service Worker 生命周期延续冷却时间。
  }
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

async function handleBossOperation(action, payload) {
  await respectRequestGap()
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
  let result = await runBossFetch(tab.id, endpoint)
  lastRequestAt = Date.now()
  let failure = classifyBossFailure(result)

  if (shouldRefreshBossSession(failure, action)) {
    const now = Date.now()
    const previousRefreshAt = await readLastSessionRefreshAt()
    if (isBossSessionRefreshCoolingDown(previousRefreshAt, now, SESSION_REFRESH_COOLDOWN_MS)) {
      return {
        ok: false,
        error: {
          code: 'session_refresh_cooldown',
          message: 'BOSS 页面刚刚已自动刷新过。为保护账号，本次不会再次刷新或重试，请稍后再试。'
        }
      }
    }

    await rememberSessionRefreshAt(now)
    try {
      await reloadBossTab(tab.id)
    } catch {
      return {
        ok: false,
        error: {
          code: 'session_refresh_failed',
          message: 'BOSS 页面自动刷新未完成。为保护账号，助手不会继续重试，请打开 BOSS 页面检查状态。'
        }
      }
    }

    await respectRequestGap()
    result = await runBossFetch(tab.id, endpoint)
    lastRequestAt = Date.now()
    failure = classifyBossFailure(result)
    if (shouldRefreshBossSession(failure)) {
      return {
        ok: false,
        error: {
          code: 'session_refresh_failed',
          message: 'BOSS 页面已自动刷新一次，但仍限制访问。为保护账号，助手不会继续重试，请稍后再试或打开 BOSS 页面处理。'
        }
      }
    }
  }

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
