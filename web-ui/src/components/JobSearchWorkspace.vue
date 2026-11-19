<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { JobLibraryBridgeError, jobLibraryBridge, subscribeJobLibraryConnection } from '../job-library-bridge.js'
import { createJobLibraryConnection } from '../job-library-connection.js'
import {
  greetingJobKey,
  normalizeGreetingLimit,
  toggleGreetingJob
} from '../career-greeting-preview.js'
import {
  beginSelectionIntent,
  cancelSelectionIntent,
  createSelectionIntentState,
  isCurrentSelectionIntent,
  resetSelectionIntents
} from '../job-search-multi-selection.js'
import {
  createJobDetailLoader,
  prefetchJobDetails
} from '../job-detail-prefetch.js'
import {
  DEFAULT_JOB_CITY,
  mergeJobCityCatalog,
  normalizeHotJobCities
} from '../job-city-catalog.js'
import { findSearchCity, summarizeSearchJobs } from '../job-search-card.js'
import { resolveJobSearchCity } from '../job-search-location.js'
import JobCityPicker from './JobCityPicker.vue'
import BossExtensionInstallDialog from './BossExtensionInstallDialog.vue'
import DesktopCapabilityNotice from './DesktopCapabilityNotice.vue'
import { isMobileBrowser } from '../platform-capabilities.js'

const CITY_STORAGE_KEY = 'find-job-job-city-v1'

function readStoredCity() {
  try {
    const value = JSON.parse(window.localStorage.getItem(CITY_STORAGE_KEY))
    if (/^\d{9}$/.test(String(value?.code ?? '')) && String(value?.name ?? '').trim()) {
      return value
    }
  } catch {
    // 浏览器禁用本地存储时继续使用默认城市。
  }
  return { ...DEFAULT_JOB_CITY }
}

const props = defineProps({
  selectionMode: { type: Boolean, default: false },
  multiSelectionMode: { type: Boolean, default: false },
  selectionLimit: { type: Number, default: 10 },
  chatMode: { type: Boolean, default: false },
  initialQuery: { type: String, default: '' },
  initialCityName: { type: String, default: '' },
  initialCitySource: { type: String, default: '' },
  autoSearch: { type: Boolean, default: false },
  searchSession: { type: Object, default: null },
  sourceMessageId: { type: String, default: '' },
  analysisRequested: { type: Boolean, default: false }
})

const emit = defineEmits(['selection-change', 'selection-list-change', 'search-state', 'navigate', 'batch-change'])
const mobileBrowser = isMobileBrowser()

const query = ref(props.initialQuery)
const selectedPlatform = ref('boss')
const liepinConfigured = ref(false)
const liepinConnectionState = ref('unconfigured')
const liepinSetupError = ref('')
const searchInputId = useId()
const locating = ref(false)
const locationMessage = ref('')
const locationAttribution = ref('')
const locationAttributionUrl = ref('')
const citySource = ref(props.initialCitySource || (props.initialCityName ? 'user' : ''))
let autoSearchAttempted = false
let initialized = false
let locationSequence = 0
const submittedQuery = ref('')
const jobs = ref([])
const selectedJobId = ref('')
const selectedJob = ref(null)
const compactView = ref('list')
const jobResults = ref(null)
let resultsScrollTop = 0
const page = ref(1)
const hasMore = ref(false)
const hasSearched = ref(false)
const searching = ref(false)
const loadingMore = ref(false)
const detailLoading = ref(false)
const searchError = ref('')
const detailError = ref('')
const selectedJobs = ref([])
const multiSelectionError = ref('')
const selectionLoadingKeys = ref([])
const selectionErrors = ref({})
const bridgeStatus = ref('checking')
const bridgeVersion = ref('')
const bridgeCapabilities = ref([])
const bossAttention = ref(null)
const installDialogOpen = ref(false)
const extensionDialogMode = ref('install')
const searchInput = ref(null)
const selectedCity = ref(props.chatMode
  ? findSearchCity(mergeJobCityCatalog(), props.initialCityName) || { code: '', name: props.initialCityName || '请选择城市' }
  : readStoredCity())
const submittedCity = ref({ ...selectedCity.value })
const cityCatalog = ref(mergeJobCityCatalog())
const hotCities = ref(normalizeHotJobCities())
const cityLoading = ref(false)
let searchSequence = 0
let detailSequence = 0
let prefetchSequence = 0
let selectionClosed = false
let unsubscribeConnection = () => {}
let lastConnectionError = ''
let lastPublishedSummary = ''
let currentBatchId = ''
const completionTimers = new Set()
function persistError(error) { searchError.value = error instanceof Error ? error.message : '岗位保存失败，请重试保存。' }
function publishBatch() { if (currentBatchId) emit('batch-change', props.searchSession?.state(currentBatchId)?.batch) }
function completeBatch(batchId) { if (batchId) void props.searchSession.complete(batchId).then(publishBatch).catch(persistError) }
const connection = createJobLibraryConnection({
  ping: () => jobLibraryBridge.ping(),
  onChange: (state) => {
    bridgeStatus.value = state.status === 'ready' && selectedPlatform.value === 'boss' && bossAttention.value ? 'attention' : state.status
    bridgeVersion.value = state.version
    bridgeCapabilities.value = Array.isArray(state.capabilities) ? state.capabilities : []
    if (state.status === 'ready') {
      if (searchError.value === lastConnectionError) searchError.value = ''
      lastConnectionError = ''
      installDialogOpen.value = false
    } else if (state.status === 'unavailable') {
      lastConnectionError = state.error
      searchError.value = state.error
    }
  }
})
const selectionIntents = createSelectionIntentState()
const detailLoader = createJobDetailLoader({
  keyOf: greetingJobKey,
  fetchDetail: (job) => jobLibraryBridge.getJobDetail(job)
})

const bridgeStatusText = computed(() => ({
  checking: '正在连接助手',
  ready: bridgeVersion.value ? `浏览器助手已连接 · v${bridgeVersion.value}` : '浏览器助手已连接',
  unavailable: '助手暂未连接 · 点击重试',
  attention: '需要处理平台状态'
}[bridgeStatus.value]))

const selectedSkills = computed(() => selectedJob.value?.skills ?? [])
const selectedWelfare = computed(() => selectedJob.value?.welfare ?? [])
const resultSummary = computed(() => jobs.value.length
  ? `已加载 ${jobs.value.length} 个岗位`
  : '等待真实结果')
const selectedPlatformName = computed(() => selectedPlatform.value === 'liepin' ? '猎聘' : 'BOSS 直聘')
const searchErrorTitle = computed(() => bridgeStatus.value === 'unavailable'
  ? '浏览器助手暂时无法连接'
  : jobs.value.length ? '后续操作未完成，已保留现有岗位' : '本次没有获取到岗位')
const detailFreshness = computed(() => selectedJob.value?.description
  ? '完整详情已获取' : selectedJob.value?.platform === 'liepin' ? '官方搜索未返回完整 JD' : detailLoading.value ? '正在获取完整详情' : detailError.value ? '详情获取受阻' : '仅有岗位摘要')
const resultCityName = computed(() => (hasSearched.value ? submittedCity.value : selectedCity.value).name)
const detailFetchedAt = computed(() => {
  const value = selectedJob.value?.fetchedAt
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(date)
})
const normalizedSelectionLimit = computed(() => normalizeGreetingLimit(props.selectionLimit))
const selectedJobCount = computed(() => selectedJobs.value.length)
const canSelectJobs = computed(() => selectedPlatform.value === 'boss')
const liepinFeatureReady = computed(() => bridgeStatus.value === 'ready' && bridgeCapabilities.value.includes('liepin_job_search'))
const platformStatusText = computed(() => selectedPlatform.value === 'liepin'
  ? bridgeStatus.value === 'ready' && !liepinFeatureReady.value
    ? `助手 v${bridgeVersion.value || '旧版本'}不支持猎聘搜索`
    : liepinConnectionState.value === 'ready'
    ? '猎聘授权可用'
    : liepinConfigured.value
      ? '猎聘授权已保存，首次搜索时验证'
      : '需要猎聘官方授权'
  : bridgeStatusText.value)
const searchButtonText = computed(() => searching.value
  ? '正在检索…'
  : selectedPlatform.value === 'liepin' && !liepinConfigured.value
    ? '配置猎聘授权'
    : '搜索真实岗位')

function readableBridgeError(error, fallback) {
  if (error instanceof JobLibraryBridgeError) return error.message
  return error instanceof Error ? error.message : fallback
}

function markBridgeAttention(error) {
  if (!(error instanceof JobLibraryBridgeError)) return
  if (['extension_unavailable', 'bridge_timeout', 'bridge_disconnected'].includes(error.code)) {
    void checkBridge({ loadCatalog: false })
  }
  if ([
    'login_required',
    'verification_required',
    'rate_limited',
    'city_mismatch',
    'city_context_sync_failed',
    'session_refresh_failed'
  ].includes(error.code)) {
    bossAttention.value = { code: error.code, message: error.message }
    bridgeStatus.value = 'attention'
  }
}

function clearBossAttention() {
  bossAttention.value = null
  bridgeStatus.value = 'ready'
}

function publishSelection(job) {
  if (props.selectionMode) emit('selection-change', canSelectJobs.value ? job : null)
}

function publishSelectionList() {
  if (props.multiSelectionMode) emit('selection-list-change', [...selectedJobs.value])
}

function isJobInSelection(job) {
  const key = greetingJobKey(job)
  return selectedJobs.value.some((item) => greetingJobKey(item) === key)
}

function removeSelectedJob(job) {
  const result = toggleGreetingJob(
    selectedJobs.value,
    job,
    normalizedSelectionLimit.value
  )
  selectedJobs.value = result.jobs
  multiSelectionError.value = result.error
  publishSelectionList()
}

function isSelectionLoading(job) {
  return selectionLoadingKeys.value.includes(greetingJobKey(job))
}

function selectionError(job) {
  return selectionErrors.value[greetingJobKey(job)] || ''
}

function setSelectionLoading(key, loading) {
  selectionLoadingKeys.value = loading
    ? [...new Set([...selectionLoadingKeys.value, key])]
    : selectionLoadingKeys.value.filter((item) => item !== key)
}

function clearSelectionError(key) {
  const next = { ...selectionErrors.value }
  delete next[key]
  selectionErrors.value = next
}

function mergeJobDetail(detail) {
  if (props.searchSession && currentBatchId) {
    void props.searchSession.saveDetail(currentBatchId, detail).then(publishBatch).catch(persistError)
  }
  jobs.value = jobs.value.map((item) => (
    greetingJobKey(item) === greetingJobKey(detail) ? { ...item, ...detail } : item
  ))
  if (selectedJob.value && greetingJobKey(selectedJob.value) === greetingJobKey(detail)) {
    selectedJob.value = { ...selectedJob.value, ...detail }
  }
  let selectionChanged = false
  selectedJobs.value = selectedJobs.value.map((item) => {
    if (greetingJobKey(item) !== greetingJobKey(detail)) return item
    selectionChanged = true
    return { ...item, ...detail }
  })
  if (selectionChanged) publishSelectionList()
  if (props.chatMode && !props.searchSession && hasSearched.value) {
    const summary = summarizeSearchJobs(jobs.value)
    const serialized = JSON.stringify(summary)
    if (serialized !== lastPublishedSummary) {
      lastPublishedSummary = serialized
      emit('search-state', { result_summary: summary })
    }
  }
}

function shouldStopDetailPrefetch(error) {
  return error instanceof JobLibraryBridgeError && [
    'login_required',
    'verification_required',
    'rate_limited',
    'session_refresh_failed'
  ].includes(error.code)
}

function prefetchCurrentJobs(jobList, searchToken) {
  const sequence = ++prefetchSequence
  const batchId = currentBatchId
  const eligibleJobs = jobList.filter((job) => job.platform !== 'liepin' || Boolean(job.description))
  if (!eligibleJobs.length) {
    if (batchId && props.analysisRequested) completeBatch(batchId)
    return
  }
  const selectedKeys = new Set(selectedJobs.value.map(greetingJobKey))
  const prioritized = [...eligibleJobs].sort((left, right) => (
    Number(selectedKeys.has(greetingJobKey(right))) - Number(selectedKeys.has(greetingJobKey(left)))
  ))
  const completion = prefetchJobDetails(prioritized, detailLoader, {
    // 扩展本身串行读取，前端也逐条领取，遇到验证后不再排入下一条。
    concurrency: 1,
    shouldContinue: () => !selectionClosed && searchToken === searchSequence && sequence === prefetchSequence
      && !['login_required', 'verification_required', 'rate_limited', 'session_refresh_failed'].includes(bossAttention.value?.code),
    onResolved: (detail) => {
      if (selectionClosed || searchToken !== searchSequence) return
      mergeJobDetail(detail)
      clearSelectionError(greetingJobKey(detail))
    },
    onRejected: (error, job) => {
      if (selectionClosed || searchToken !== searchSequence) return
      markBridgeAttention(error)
      if (isJobInSelection(job)) {
        selectionErrors.value = {
          ...selectionErrors.value,
          [greetingJobKey(job)]: readableBridgeError(error, '岗位详情预取失败，点击卡片可重试。')
        }
      }
    },
    shouldStop: shouldStopDetailPrefetch
  })
  if (batchId && props.analysisRequested) {
    const timer = setTimeout(() => completeBatch(batchId), 30000)
    completionTimers.add(timer)
    void completion.finally(() => { clearTimeout(timer); completionTimers.delete(timer); if (!selectionClosed) completeBatch(batchId) })
  }
}

async function hydrateSelectedJob(job, token) {
  const key = greetingJobKey(job)
  setSelectionLoading(key, true)
  try {
    const detail = await detailLoader.load(job)
    if (selectionClosed || !isCurrentSelectionIntent(selectionIntents, key, token)) return
    mergeJobDetail(detail)
    clearSelectionError(key)
  } catch (error) {
    if (selectionClosed || !isCurrentSelectionIntent(selectionIntents, key, token)) return
    markBridgeAttention(error)
    selectionErrors.value = {
      ...selectionErrors.value,
      [key]: readableBridgeError(error, '岗位详情获取失败，点击卡片可重试。')
    }
  } finally {
    if (isCurrentSelectionIntent(selectionIntents, key, token)) {
      cancelSelectionIntent(selectionIntents, key)
      setSelectionLoading(key, false)
    }
  }
}

async function toggleJobFromCard(job) {
  if (!canSelectJobs.value) return
  if (!props.multiSelectionMode) return
  const key = greetingJobKey(job)
  multiSelectionError.value = ''
  clearSelectionError(key)

  if (isJobInSelection(job)) {
    cancelSelectionIntent(selectionIntents, key)
    setSelectionLoading(key, false)
    removeSelectedJob(job)
    return
  }
  if (isSelectionLoading(job)) {
    cancelSelectionIntent(selectionIntents, key)
    setSelectionLoading(key, false)
    return
  }
  if (selectedJobs.value.length >= normalizedSelectionLimit.value) {
    multiSelectionError.value = `本批最多选择 ${normalizedSelectionLimit.value} 个岗位。`
    return
  }

  const token = beginSelectionIntent(selectionIntents, key)
  const cached = detailLoader.peek(job)
  const result = toggleGreetingJob(
    selectedJobs.value,
    cached ?? job,
    normalizedSelectionLimit.value
  )
  selectedJobs.value = result.jobs
  multiSelectionError.value = result.error
  if (result.error) {
    cancelSelectionIntent(selectionIntents, key)
    return
  }
  publishSelectionList()
  if (cached) {
    mergeJobDetail(cached)
    cancelSelectionIntent(selectionIntents, key)
    return
  }
  void hydrateSelectedJob(job, token)
}

function resetJobResults() {
  searchSequence += 1
  detailSequence += 1
  prefetchSequence += 1
  detailLoader.clear()
  jobs.value = []
  selectedJobId.value = ''
  selectedJob.value = null
  compactView.value = 'list'
  page.value = 1
  hasMore.value = false
  hasSearched.value = false
  submittedQuery.value = ''
  searchError.value = ''
  detailError.value = ''
  searching.value = false
  loadingMore.value = false
  detailLoading.value = false
  resetSelectionIntents(selectionIntents)
  selectionLoadingKeys.value = []
  selectionErrors.value = {}
  publishSelection(null)
}

function searchConditions() {
  return { platform: selectedPlatform.value, keyword: query.value.trim(), city: selectedCity.value.code ? selectedCity.value.name : null, city_source: citySource.value || null }
}

function publishConditions() {
  if (props.chatMode && !props.searchSession && initialized) emit('search-state', { conditions: searchConditions() })
}

async function locateCity(requestPermission = false) {
  const sequence = ++locationSequence
  locating.value = true
  const result = await resolveJobSearchCity({ cities: cityCatalog.value, requestPermission })
  if (selectionClosed || sequence !== locationSequence) return
  locating.value = false
  // 定位期间用户选了城市时，以用户选择为准。
  if (selectedCity.value.code && citySource.value !== 'location') return
  locationMessage.value = result.message || ''
  locationAttribution.value = result.attribution || ''
  locationAttributionUrl.value = result.attributionUrl || ''
  if (result.city) {
    selectedCity.value = { ...result.city }
    citySource.value = 'location'
    publishConditions()
    await maybeAutoSearch()
  }
}

async function maybeAutoSearch() {
  if (mobileBrowser) return
  if (!initialized || !props.chatMode || !props.autoSearch || autoSearchAttempted
    || !query.value.trim() || !selectedCity.value.code) return
  autoSearchAttempted = true
  await submitSearch({ automatic: true })
}

function handleCityChange(city) {
  if (!city?.code || city.code === selectedCity.value.code) return
  selectedCity.value = { ...city }
  if (props.chatMode) {
    citySource.value = 'manual'
    locationSequence += 1
    locating.value = false
    locationMessage.value = ''
    publishConditions()
  }
  try {
    window.localStorage.setItem(CITY_STORAGE_KEY, JSON.stringify(selectedCity.value))
  } catch {
    // 本地存储不可用不影响本次搜索。
  }
  if (hasSearched.value || jobs.value.length) resetJobResults()
  if (props.chatMode) void maybeAutoSearch()
}

async function loadCities() {
  if (cityLoading.value) return
  cityLoading.value = true
  try {
    const result = await jobLibraryBridge.listCities()
    cityCatalog.value = mergeJobCityCatalog(result)
    hotCities.value = normalizeHotJobCities(result)
    const refreshedCity = cityCatalog.value.find((city) => city.code === selectedCity.value.code)
    if (refreshedCity) selectedCity.value = refreshedCity
  } catch {
    // 城市目录请求失败时使用内置热门城市，岗位搜索仍可继续。
  } finally {
    cityLoading.value = false
  }
}

async function refreshLiepinState() {
  if (!liepinFeatureReady.value) return false
  try {
    const state = await jobLibraryBridge.getLiepinState()
    liepinConfigured.value = state?.configured === true
    liepinConnectionState.value = liepinConfigured.value ? 'saved' : 'unconfigured'
    liepinSetupError.value = ''
    return true
  } catch (error) {
    liepinSetupError.value = readableBridgeError(error, '无法读取猎聘授权状态。')
    liepinConnectionState.value = 'error'
    return false
  }
}

async function selectPlatform(platform) {
  if (!['boss', 'liepin'].includes(platform) || platform === selectedPlatform.value) return
  if (currentBatchId && props.searchSession) {
    const batchId = currentBatchId
    currentBatchId = ''
    completeBatch(batchId)
  }
  selectedPlatform.value = platform
  selectedJobs.value = []
  multiSelectionError.value = ''
  publishSelectionList()
  resetJobResults()
  publishConditions()
  if (platform === 'liepin') {
    if (bridgeStatus.value !== 'ready') await checkBridge({ loadCatalog: false })
    if (liepinFeatureReady.value) await refreshLiepinState()
    else if (bridgeStatus.value === 'ready') {
      liepinSetupError.value = '请更新到 0.7.0 或更高版本的浏览器助手，再连接猎聘。'
    }
  }
  else if (bridgeStatus.value === 'ready') void loadCities()
}

async function openLiepinSettings() {
  if (!await checkBridge({ loadCatalog: false })) return
  if (!liepinFeatureReady.value) {
    extensionDialogMode.value = 'update'
    installDialogOpen.value = true
    return
  }
  try {
    await jobLibraryBridge.openLiepinSettings()
    liepinSetupError.value = ''
  } catch (error) {
    liepinSetupError.value = readableBridgeError(error, '无法打开扩展授权设置，请检查助手连接。')
  }
}

async function checkBridge({ loadCatalog = true } = {}) {
  if (mobileBrowser) return false
  const connected = await connection.check()
  if (!selectionClosed && connected && loadCatalog) {
    if (selectedPlatform.value === 'boss') void loadCities()
    else void refreshLiepinState()
  }
  return connected
}

function handleConnectionBadge() {
  void checkBridge()
}

function handleConnectionSignal(signal) {
  void connection.handleSignal(signal).then(async (connected) => {
    if (!connected || selectionClosed) return
    if (selectedPlatform.value === 'boss') await loadCities()
    else await refreshLiepinState()
    // 只继续尚未尝试的自动搜索，不重放已提交的业务请求。
    if (!selectionClosed) await maybeAutoSearch()
  })
}

function refreshForExtensionCheck() {
  window.location.reload()
}

async function submitSearch({ automatic = false } = {}) {
  const nextQuery = query.value.trim()
  if (!nextQuery || searching.value) return
  if (selectedPlatform.value === 'liepin' && !liepinConfigured.value) {
    if (await checkBridge({ loadCatalog: false })) {
      if (!liepinFeatureReady.value) {
        liepinSetupError.value = '请更新到 0.7.0 或更高版本的浏览器助手，再连接猎聘。'
        extensionDialogMode.value = 'update'
        installDialogOpen.value = true
        return
      }
      await refreshLiepinState()
    }
    if (!liepinConfigured.value) {
      liepinSetupError.value = '请先完成猎聘官方授权，并在扩展设置页保存令牌。'
      await openLiepinSettings()
      return
    }
  }
  if (props.chatMode && !selectedCity.value.code) {
    locationMessage.value = '请选择工作城市，或启用定位服务获取当前位置。'
    return
  }
  if (!await checkBridge({ loadCatalog: false })) return
  if (selectedPlatform.value === 'liepin') {
    if (!liepinFeatureReady.value) {
      liepinSetupError.value = '请更新到 0.7.0 或更高版本的浏览器助手，再连接猎聘。'
      extensionDialogMode.value = 'update'
      installDialogOpen.value = true
      return
    }
    await refreshLiepinState()
    if (!liepinConfigured.value) {
      liepinSetupError.value = '猎聘授权尚未配置，请先连接后再搜索。'
      return
    }
  }
  const sequence = ++searchSequence
  detailSequence += 1
  jobs.value = []
  prefetchSequence += 1
  detailLoader.clear()
  searching.value = true
  searchError.value = ''
  detailError.value = ''
  publishSelection(null)
  submittedQuery.value = nextQuery
  submittedCity.value = { ...selectedCity.value }
  hasSearched.value = true
  lastPublishedSummary = ''
  if (props.chatMode && !props.searchSession) emit('search-state', { status: 'editing', result_summary: null, conditions: searchConditions() })

  try {
    if (props.searchSession) {
      const batch = await props.searchSession.start(props.sourceMessageId, searchConditions(), props.analysisRequested)
      if (sequence !== searchSequence) return
      currentBatchId = batch.id
      publishBatch()
    }
    const batchId = currentBatchId
    const result = await jobLibraryBridge.searchJobs(nextQuery, 1, submittedCity.value, selectedPlatform.value)
    if (batchId) { await props.searchSession.savePage(batchId, result.jobs || [], 1, result.hasMore); publishBatch() }
    if (sequence !== searchSequence) return
    jobs.value = result.jobs ?? []
    if (selectedPlatform.value === 'boss') clearBossAttention()
    else liepinConnectionState.value = 'ready'
    page.value = 1
    hasMore.value = Boolean(result.hasMore)
    if (props.chatMode && !props.searchSession) emit('search-state', { status: 'searched', conditions: { platform: selectedPlatform.value, keyword: submittedQuery.value, city: submittedCity.value.name, city_source: citySource.value || null }, result_summary: summarizeSearchJobs(jobs.value) })
    selectedJobId.value = ''
    selectedJob.value = null
    if (jobs.value.length) {
      void selectJob(jobs.value[0], { showDetail: false })
      prefetchCurrentJobs(jobs.value, sequence)
    } else if (batchId && props.analysisRequested) completeBatch(batchId)
  } catch (error) {
    if (sequence !== searchSequence) return
    jobs.value = []
    hasMore.value = false
    selectedJobId.value = ''
    selectedJob.value = null
    markBridgeAttention(error)
    searchError.value = readableBridgeError(error, '真实岗位获取失败，请稍后重试。')
    if (error?.code === 'liepin_authorization_required') {
      liepinConnectionState.value = 'expired'
      liepinSetupError.value = '猎聘授权已失效，请重新授权并在扩展设置页更新令牌。'
    }
  } finally {
    if (sequence === searchSequence) searching.value = false
  }
}

async function selectJob(job, { showDetail = true } = {}) {
  const sequence = ++detailSequence
  if (showDetail && compactView.value === 'list') resultsScrollTop = jobResults.value?.scrollTop || 0
  if (showDetail) compactView.value = 'detail'
  publishSelection(null)
  selectedJobId.value = job.id
  const searchToken = searchSequence
  if (props.searchSession && currentBatchId) void props.searchSession.focus(currentBatchId, job).catch(persistError)
  const cached = detailLoader.peek(job)
  selectedJob.value = cached ?? job
  if (cached) {
    mergeJobDetail(cached)
    publishSelection(cached)
    detailLoading.value = false
    detailError.value = ''
    return
  }
  if (job.platform === 'liepin' && !job.description) {
    detailLoading.value = false
    detailError.value = '猎聘官方搜索结果未包含完整 JD；可打开原岗位查看，完整 JD 到位后才能加入招呼流程。'
    publishSelection(null)
    return
  }
  detailLoading.value = true
  detailError.value = ''
  try {
    const detail = await detailLoader.load(job)
    if (sequence !== detailSequence || searchToken !== searchSequence) return
    selectedJob.value = detail
    mergeJobDetail(detail)
    if (selectedPlatform.value === 'boss') clearBossAttention()
    publishSelection(detail)
  } catch (error) {
    if (sequence !== detailSequence || searchToken !== searchSequence) return
    markBridgeAttention(error)
    detailError.value = readableBridgeError(error, '岗位详情获取失败，请点击当前卡片重试。')
    publishSelection(null)
  } finally {
    if (sequence === detailSequence) detailLoading.value = false
  }
}

function returnToJobList() {
  compactView.value = 'list'
  nextTick(() => jobResults.value?.scrollTo({ top: resultsScrollTop, behavior: 'instant' }))
}

async function loadMore() {
  if (!hasMore.value || loadingMore.value || !submittedQuery.value) return
  const searchToken = searchSequence
  const batchId = currentBatchId
  const conditions = { query: submittedQuery.value, city: { ...submittedCity.value }, platform: selectedPlatform.value }
  loadingMore.value = true
  searchError.value = ''
  try {
    const nextPage = page.value + 1
    const result = await jobLibraryBridge.searchJobs(conditions.query, nextPage, conditions.city, conditions.platform)
    if (batchId) await props.searchSession.savePage(batchId, result.jobs || [], nextPage, result.hasMore)
    if (searchToken !== searchSequence || selectionClosed) return
    const knownIds = new Set(jobs.value.map((job) => job.id))
    const additions = (result.jobs ?? []).filter((job) => !knownIds.has(job.id))
    jobs.value = [...jobs.value, ...additions]
    page.value = nextPage
    hasMore.value = Boolean(result.hasMore) && additions.length > 0
    if (selectedPlatform.value === 'boss') clearBossAttention()
    else liepinConnectionState.value = 'ready'
    if (additions.length) prefetchCurrentJobs(additions, searchSequence)
    if (props.chatMode && !props.searchSession) emit('search-state', { status: 'searched', conditions: { platform: selectedPlatform.value, keyword: submittedQuery.value, city: submittedCity.value.name, city_source: citySource.value || null }, result_summary: summarizeSearchJobs(jobs.value) })
  } catch (error) {
    if (searchToken !== searchSequence || selectionClosed) return
    markBridgeAttention(error)
    searchError.value = readableBridgeError(error, '更多岗位获取失败，请稍后重试。')
  } finally {
    if (searchToken === searchSequence) loadingMore.value = false
  }
}

watch(() => [props.initialQuery, props.initialCityName, props.initialCitySource], async ([keyword, cityName, source]) => {
  if (!props.chatMode) return
  const changed = query.value !== keyword || (cityName ? selectedCity.value.name !== cityName : Boolean(selectedCity.value.code))
  if (!changed) {
    if (source) citySource.value = source
    return
  }
  query.value = keyword
  if (cityName) {
    selectedCity.value = findSearchCity(cityCatalog.value, cityName) || { code: '', name: cityName }
    citySource.value = props.initialCitySource || 'user'
  } else {
    selectedCity.value = { code: '', name: '请选择城市' }
    citySource.value = ''
    locationMessage.value = '请选择工作城市，或启用定位服务获取当前位置。'
  }
  if (hasSearched.value) resetJobResults()
  await maybeAutoSearch()
})

watch(() => props.autoSearch, () => { void maybeAutoSearch() })

onMounted(async () => {
  if (mobileBrowser) return
  unsubscribeConnection = subscribeJobLibraryConnection(handleConnectionSignal)
  if (!props.chatMode) {
    void checkBridge()
    nextTick(() => searchInput.value?.focus())
    return
  }
  const connected = await checkBridge({ loadCatalog: false })
  if (selectionClosed) return
  if (connected) await loadCities()
  if (selectionClosed) return
  if (props.initialCityName) {
    selectedCity.value = findSearchCity(cityCatalog.value, props.initialCityName) || { code: '', name: props.initialCityName }
    if (!selectedCity.value.code) locationMessage.value = '暂未匹配到该城市，请手动选择工作城市。'
  }
  initialized = true
  if (!props.initialCityName) await locateCity(false)
  if (!selectionClosed) await maybeAutoSearch()
})

onBeforeUnmount(() => {
  for (const timer of completionTimers) clearTimeout(timer)
  selectionClosed = true
  unsubscribeConnection()
  connection.dispose()
  resetSelectionIntents(selectionIntents)
})
</script>

<template>
  <DesktopCapabilityNotice v-if="mobileBrowser" embedded title="实时岗位检索需要电脑端"
    description="实时在招信息由电脑端已登录的招聘网站读取。请在电脑端连接浏览器助手后检索和选择岗位。"
    :return-path="selectionMode || multiSelectionMode || chatMode ? '' : '/career'"
    @navigate="path => emit('navigate', path)" />
  <section v-else class="job-workspace" :class="{ 'is-chat': chatMode }">
    <header class="job-workspace-header">
      <div class="workspace-intro">
        <h2>查找真实岗位</h2>
        <p>{{ selectedPlatform === 'liepin' ? '通过猎聘官方授权搜索当前在招信息，职位数据仅用于只读浏览。' : '选择工作城市并输入岗位名称，职位库会通过已登录浏览器读取当前在招信息。' }}</p>
      </div>
      <div class="workspace-header-actions">
        <span v-if="multiSelectionMode" class="selection-badge">
          已选 <strong>{{ selectedJobCount }}</strong>/{{ normalizedSelectionLimit }}
        </span>
        <button
          type="button"
          class="connection-badge"
          :class="`is-${bridgeStatus}`"
          :disabled="bridgeStatus === 'checking'"
          @click="handleConnectionBadge"
        >
          <i aria-hidden="true"></i>
          {{ bridgeStatusText }}
        </button>
      </div>
    </header>

    <section v-if="!chatMode && !selectionMode && !multiSelectionMode" class="platform-switcher" aria-label="选择招聘平台">
      <div class="platform-tabs" role="tablist" aria-label="招聘平台">
        <button type="button" role="tab" :aria-selected="selectedPlatform === 'boss'" :class="{ active: selectedPlatform === 'boss' }" @click="selectPlatform('boss')">BOSS 直聘</button>
        <button type="button" role="tab" :aria-selected="selectedPlatform === 'liepin'" :class="{ active: selectedPlatform === 'liepin' }" @click="selectPlatform('liepin')">猎聘</button>
        <button type="button" role="tab" aria-selected="false" aria-disabled="true" disabled>前程无忧 · 授权后开放</button>
      </div>
      <div v-if="selectedPlatform === 'liepin'" class="platform-connection-card">
        <div class="platform-connection-copy">
          <strong>{{ bridgeStatus === 'ready' && !liepinFeatureReady ? '需要更新浏览器助手' : liepinConnectionState === 'ready' ? '猎聘搜索已连接' : liepinConfigured ? '猎聘授权已保存' : '连接猎聘官方搜索' }}</strong>
          <span>{{ !liepinFeatureReady ? '请先更新浏览器助手，再配置猎聘授权。' : liepinConfigured ? '令牌保存在浏览器助手的本地扩展存储中。' : '先在猎聘官方授权页获取令牌，再到浏览器助手设置页保存。' }}</span>
        </div>
        <div class="platform-connection-actions">
          <a href="https://www.liepin.com/mcp/auth" target="_blank" rel="noopener noreferrer">猎聘官方授权页 ↗</a>
          <button type="button" :disabled="bridgeStatus !== 'ready'" @click="openLiepinSettings">{{ liepinFeatureReady ? '打开扩展授权设置' : '更新浏览器助手' }}</button>
          <button type="button" class="subtle-action" :disabled="!liepinFeatureReady" @click="refreshLiepinState">刷新授权状态</button>
        </div>
        <p v-if="liepinSetupError" class="platform-setup-error" role="alert">{{ liepinSetupError }}</p>
        <p class="platform-capability-note">{{ liepinFeatureReady ? '当前接入范围为官方只读职位搜索。完整 JD 可能需要打开原岗位；猎聘沟通能力尚未验证，因此不会加入 BOSS 招呼清单。' : '当前浏览器助手版本不支持猎聘搜索，请下载最新版并在扩展管理页重新加载。' }}</p>
      </div>
      <p v-else class="platform-status-line" :class="`is-${bridgeStatus}`">
        <span class="status-dot"></span>{{ platformStatusText }}<span>·</span>职位搜索和岗位详情沿用当前 BOSS 助手流程
      </p>
    </section>

    <form class="search-rail" :class="{ searching }" @submit.prevent="submitSearch">
      <label :for="searchInputId">岗位名称</label>
      <div class="search-input-shell">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="11" cy="11" r="6.5"></circle>
          <path d="m16 16 4 4"></path>
        </svg>
        <input
          :id="searchInputId"
          ref="searchInput"
          v-model="query"
          @change="publishConditions"
          type="search"
          maxlength="80"
          autocomplete="off"
          placeholder="例如：Agent 软件开发"
        />
      </div>
      <JobCityPicker
        :model-value="selectedCity"
        :cities="cityCatalog"
        :hot-cities="hotCities"
        :loading="cityLoading"
        @update:model-value="handleCityChange"
      />
      <button type="submit" :disabled="searching || !query.trim()">
        <span v-if="searching" class="button-loader" aria-hidden="true"></span>
        {{ searchButtonText }}
      </button>
      <div class="live-track" aria-hidden="true"><i></i></div>
    </form>

    <div v-if="chatMode && (!selectedCity.code || locationMessage || locating)" class="location-prompt" role="status">
      <p>{{ locating ? '正在获取当前位置…' : locationMessage || '可启用定位服务获取所在城市，也可以手动选择。' }}</p>
      <small>启用后由 BigDataCloud 解析当前位置</small>
      <button v-if="!locating" type="button" @click="locateCity(true)">{{ citySource === 'location' ? '重新定位' : '启用定位服务' }}</button>
    </div>

    <a v-if="chatMode && locationAttribution && locationAttributionUrl" class="location-attribution" :href="locationAttributionUrl" target="_blank" rel="noreferrer">位置数据：{{ locationAttribution }}</a>

    <section v-if="searchError" class="job-alert" :class="{ 'is-setup': bridgeStatus === 'unavailable' }">
      <div>
        <strong>{{ searchErrorTitle }}</strong>
        <p>{{ searchError }}</p>
        <p v-if="bridgeStatus === 'unavailable'" class="setup-path">
          已安装时请确认扩展已启用并允许访问当前网站；刚安装或更新后可刷新页面。首次使用可查看安装说明。
        </p>
      </div>
      <div v-if="bridgeStatus === 'unavailable'" class="connection-actions">
        <button type="button" @click="checkBridge()">重新检测</button>
        <button type="button" @click="extensionDialogMode = 'install'; installDialogOpen = true">查看安装说明</button>
      </div>
    </section>

    <p v-if="multiSelectionError" class="multi-selection-error" role="alert">
      {{ multiSelectionError }}
    </p>

    <div
      v-if="!chatMode || hasSearched"
      class="job-browser"
      :class="{ 'is-searching': searching, 'show-compact-detail': compactView === 'detail' }"
    >
      <aside ref="jobResults" class="job-results" aria-label="岗位搜索结果">
        <header class="result-heading">
          <div>
            <strong>{{ submittedQuery || '真实岗位结果' }}</strong>
            <span>{{ selectedPlatformName }} · {{ resultCityName }} · 当前会话返回</span>
          </div>
          <span class="result-count">{{ resultSummary }}</span>
        </header>

        <div v-if="!hasSearched" class="result-empty">
          <span aria-hidden="true">⌕</span>
          <strong>选择城市并输入岗位名称开始</strong>
          <p>不需要粘贴 URL，也不会跳转离开职位库。</p>
        </div>
        <div v-else-if="!searching && !jobs.length && !searchError" class="result-empty">
          <span aria-hidden="true">—</span>
          <strong>没有找到匹配岗位</strong>
          <p>尝试缩短岗位名称，例如只搜索“Agent”或“Java”。</p>
        </div>

        <div v-if="jobs.length" class="job-card-list">
          <article
            v-for="job in jobs"
            :key="job.id"
            class="job-card"
            :class="{ active: selectedJobId === job.id, selected: isJobInSelection(job), 'has-selection': multiSelectionMode && canSelectJobs }"
            role="button"
            tabindex="0"
            @click="selectJob(job)"
            @keydown.enter.prevent="selectJob(job)"
            @keydown.space.prevent="selectJob(job)"
          >
            <span class="card-track-node" aria-hidden="true"></span>
            <button
              v-if="multiSelectionMode && canSelectJobs"
              type="button"
              class="job-selection-checkbox"
              :class="{ checked: isJobInSelection(job), loading: isSelectionLoading(job) }"
              role="checkbox"
              :aria-checked="isJobInSelection(job)"
              :aria-label="`${isJobInSelection(job) ? '移除' : '选择'}岗位：${job.title}`"
              @click.stop="toggleJobFromCard(job)"
            >
              <span aria-hidden="true">{{ isJobInSelection(job) ? '✓' : '' }}</span>
              <i v-if="isSelectionLoading(job)" class="selection-detail-loader" aria-hidden="true"></i>
            </button>
            <span class="job-card-topline">
              <strong>{{ job.title }}</strong>
              <em>{{ job.salary }}</em>
            </span>
            <span class="job-tags">
              <i class="platform-tag">{{ job.platform === 'liepin' ? '猎聘' : 'BOSS 直聘' }}</i>
              <i>{{ job.experience }}</i>
              <i>{{ job.degree }}</i>
              <i v-if="job.skills[0]">{{ job.skills[0] }}</i>
            </span>
            <span class="company-row">
              <span class="company-mark" aria-hidden="true">{{ job.companyShort.slice(0, 1) }}</span>
              <span class="company-name">{{ job.companyShort }}</span>
              <span class="company-location">{{ job.district || job.city }}</span>
            </span>
            <span v-if="selectionError(job)" class="job-selection-error" role="alert">
              {{ selectionError(job) }}
            </span>
          </article>
        </div>

        <button v-if="jobs.length && hasMore" type="button" class="load-more" :disabled="loadingMore" @click="loadMore">
          <span v-if="loadingMore" class="button-loader" aria-hidden="true"></span>
          {{ loadingMore ? '正在加载…' : '加载更多岗位' }}
          <span v-if="!loadingMore" aria-hidden="true">↓</span>
        </button>
        <p v-else-if="jobs.length" class="result-end">已展示当前搜索返回的全部岗位</p>
      </aside>

      <article v-if="selectedJob" class="job-detail" :class="{ refreshing: detailLoading }" aria-live="polite">
        <button
          type="button"
          class="detail-back-button"
          aria-label="返回岗位列表"
          @click="returnToJobList"
        >返回岗位列表</button>
        <div v-if="detailLoading" class="detail-refreshing">
          <span class="button-loader" aria-hidden="true"></span>
          正在获取最新详情
        </div>

        <header class="detail-hero">
          <div class="detail-title-block">
            <span class="detail-track-node" aria-hidden="true"></span>
            <div class="title-line">
              <h3>{{ selectedJob.title }}</h3>
              <strong>{{ selectedJob.salary }}</strong>
            </div>
            <div class="detail-meta">
              <span>
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21s6-5.2 6-11a6 6 0 1 0-12 0c0 5.8 6 11 6 11Z"></path><circle cx="12" cy="10" r="2"></circle></svg>
                {{ selectedJob.city || submittedCity.name }}
              </span>
              <span>{{ selectedJob.experience }}</span>
              <span>{{ selectedJob.degree }}</span>
            </div>
          </div>
          <div class="detail-hero-actions">
            <div class="freshness">
              <span><i aria-hidden="true"></i>{{ detailFreshness }}</span>
              <small v-if="selectedJob.description">{{ detailFetchedAt || '本次会话' }}</small>
            </div>
          </div>
        </header>

        <section class="company-strip">
          <span class="company-logo" aria-hidden="true">{{ selectedJob.companyShort.slice(0, 1) }}</span>
          <div>
            <strong>{{ selectedJob.company }}</strong>
            <p>{{ [selectedJob.industry, selectedJob.stage, selectedJob.scale].filter(Boolean).join(' · ') || '公司信息以招聘平台页面为准' }}</p>
          </div>
          <span v-if="selectedJob.platform !== 'liepin'" class="recruiter-online" :class="{ offline: !selectedJob.recruiterOnline }">
            <i aria-hidden="true"></i>{{ selectedJob.recruiter || '招聘者信息待补充' }}
          </span>
        </section>

        <div class="detail-scroll">
          <section v-if="detailError" class="detail-error">
            <strong>{{ selectedJob.platform === 'liepin' ? '猎聘搜索结果未包含完整 JD' : '详情暂时没有刷新成功' }}</strong>
            <p>{{ detailError }}</p>
            <button v-if="selectedJob.platform !== 'liepin'" type="button" @click="selectJob(selectedJob)">重新获取详情</button>
          </section>

          <section v-if="selectedSkills.length" class="job-summary">
            <div class="skill-list">
              <span v-for="skill in selectedSkills" :key="skill">{{ skill }}</span>
            </div>
          </section>

          <section class="description-section">
            <h4>职位描述</h4>
            <div v-if="selectedJob.description" class="job-description">{{ selectedJob.description }}</div>
            <p v-else class="description-empty">{{ selectedJob.platform === 'liepin' ? '猎聘官方搜索未附职位正文，请通过原岗位链接查看。' : '职位详情暂未提供正文，可再次点击左侧卡片刷新。' }}</p>
          </section>

          <section v-if="selectedJob.address" class="description-section">
            <h4>工作地点</h4>
            <p>{{ selectedJob.address }}</p>
          </section>

          <section v-if="selectedWelfare.length" class="description-section welfare-section">
            <h4>职位福利</h4>
            <div class="welfare-list">
              <span v-for="item in selectedWelfare" :key="item">{{ item }}</span>
            </div>
          </section>

          <a v-if="selectedJob.sourceUrl" class="source-link" :href="selectedJob.sourceUrl" target="_blank" rel="noopener noreferrer">
            在{{ selectedJob.platform === 'liepin' ? '猎聘' : 'BOSS 直聘' }}查看原岗位
            <span aria-hidden="true">↗</span>
          </a>
        </div>
      </article>

      <section v-else class="detail-empty">
        <span aria-hidden="true">◎</span>
        <strong>{{ searching ? '正在查找真实岗位' : '选择一个岗位查看详情' }}</strong>
        <p>{{ searching ? '职位列表返回后会自动读取第一条岗位详情。' : '每次点击岗位卡片，都会重新获取最新职位描述。' }}</p>
      </section>
    </div>
  </section>

  <BossExtensionInstallDialog
    :open="installDialogOpen"
    :error="bridgeStatus === 'ready' ? '' : searchError"
    :mode="extensionDialogMode"
    :platform="selectedPlatform"
    @close="installDialogOpen = false"
    @refresh="refreshForExtensionCheck"
  />
</template>

<style scoped>
.job-workspace {
  --job-canvas: var(--ui-canvas, #f3f7fc);
  --job-paper: var(--ui-surface, #fff);
  --job-ink: var(--ui-text, #14213d);
  --job-copy: var(--ui-text-secondary, #526078);
  --job-muted: var(--ui-text-muted, #7b879c);
  --job-line: var(--ui-line, #d7e4f6);
  --job-line-strong: var(--ui-line-strong, #b9cfed);
  --job-blue: var(--ui-accent, #0869d8);
  --job-blue-ink: var(--ui-accent-ink, #004aa8);
  --job-blue-soft: var(--ui-surface-active, #eaf3ff);
  --job-teal: #04a6a6;
  --job-red: #f05b52;
  display: flex;
  min-height: 0;
  min-width: 0;
  container: job-search / inline-size;
  flex: 1;
  flex-direction: column;
  gap: 12px;
  color: var(--job-ink);
  font-family: var(--ui-font-body, Inter, "Segoe UI Variable Text", "PingFang SC", sans-serif);
}

.job-workspace-header { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 18px; }
.workspace-intro h2 { margin: 0; color: var(--job-ink); font-family: var(--ui-font-display, "Segoe UI Variable Display", "PingFang SC", sans-serif); font-size: 20px; font-weight: 760; letter-spacing: -.025em; }
.workspace-intro p { margin: 3px 0 0; color: var(--job-muted); font-size: 12px; }
.workspace-header-actions { display: flex; align-items: center; gap: 9px; }
.selection-badge { border: 1px solid var(--job-line); border-radius: 999px; background: var(--job-blue-soft); color: var(--job-blue-ink); padding: 6px 10px; font: 750 10px/1.2 var(--ui-font-utility, Consolas, monospace); }
.selection-badge strong { font-size: 12px; }
.connection-badge { display: inline-flex; align-items: center; gap: 7px; border: 1px solid color-mix(in srgb, var(--job-teal) 28%, transparent); border-radius: 999px; background: color-mix(in srgb, var(--job-teal) 8%, var(--job-paper)); color: #087d7d; padding: 6px 10px; cursor: pointer; font-family: var(--ui-font-utility, Consolas, monospace); font-size: 10px; font-weight: 700; }
.connection-badge i,.recruiter-online i,.freshness i { width: 7px; height: 7px; flex: none; border-radius: 50%; background: var(--job-teal); box-shadow: 0 0 0 3px color-mix(in srgb, var(--job-teal) 14%, transparent); }
.connection-badge.is-checking i { animation: status-pulse .8s ease-in-out infinite alternate; }
.connection-badge.is-missing,.connection-badge.is-attention { border-color: color-mix(in srgb, #d99a35 36%, transparent); background: #fff8e8; color: #8c621c; }
.connection-badge.is-missing i,.connection-badge.is-attention i { background: #d99a35; box-shadow: 0 0 0 3px rgba(217,154,53,.14); }

.platform-switcher { display: grid; gap: 10px; border: 1px solid var(--job-line); border-radius: 13px; background: var(--job-paper); padding: 10px 12px; }
.platform-tabs { display: flex; flex-wrap: wrap; gap: 7px; }
.platform-tabs button { min-height: 34px; border: 1px solid transparent; border-radius: 8px; background: transparent; color: var(--job-copy); padding: 7px 12px; cursor: pointer; font: 700 11px/1.2 var(--ui-font-body, sans-serif); }
.platform-tabs button:hover:not(:disabled) { background: var(--job-blue-soft); color: var(--job-blue-ink); }
.platform-tabs button.active { border-color: color-mix(in srgb, var(--job-blue) 24%, var(--job-line)); background: var(--job-blue-soft); color: var(--job-blue-ink); }
.platform-tabs button:disabled { color: var(--job-muted); cursor: not-allowed; opacity: .72; }
.platform-status-line { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; margin: 0; color: var(--job-muted); font-size: 10px; }
.status-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--job-teal); }
.platform-status-line:not(.is-ready) .status-dot { background: #d99a35; }
.platform-connection-card { display: grid; grid-template-columns: minmax(0,1fr) auto; align-items: center; gap: 8px 18px; border-top: 1px solid var(--job-line); padding: 10px 2px 2px; }
.platform-connection-copy { display: grid; gap: 4px; }
.platform-connection-copy strong { color: var(--job-ink); font-size: 12px; }
.platform-connection-copy span,.platform-capability-note { color: var(--job-muted); font-size: 10px; line-height: 1.55; }
.platform-connection-actions { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 7px; }
.platform-connection-actions a,.platform-connection-actions button { min-height: 30px; display: inline-flex; align-items: center; border: 1px solid var(--job-line); border-radius: 7px; background: var(--job-paper); color: var(--job-blue-ink); padding: 6px 9px; font: 700 10px/1.2 var(--ui-font-body, sans-serif); text-decoration: none; }
.platform-connection-actions button { cursor: pointer; }
.platform-connection-actions button:hover:not(:disabled),.platform-connection-actions a:hover { border-color: var(--job-blue); background: var(--job-blue-soft); }
.platform-connection-actions button:disabled { cursor: not-allowed; opacity: .6; }
.platform-connection-actions .subtle-action { color: var(--job-copy); }
.platform-setup-error { grid-column: 1/-1; margin: 0; color: #a33b46; font-size: 10px; }
.platform-capability-note { grid-column: 1/-1; margin: 0; }

.search-rail { position: relative; z-index: 10; display: grid; grid-template-columns: auto minmax(320px, 720px) auto auto 1fr; align-items: center; gap: 12px; min-height: 66px; overflow: visible; border: 1px solid var(--job-line); border-radius: 14px; background: var(--job-paper); padding: 12px 16px 14px; }
.search-rail > label { color: var(--job-copy); font-size: 12px; font-weight: 800; }
.search-input-shell { display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: center; gap: 10px; min-height: 42px; border: 1px solid var(--job-line-strong); border-radius: 10px; background: color-mix(in srgb, var(--job-paper) 84%, var(--job-blue-soft)); padding: 0 11px; transition: border-color .16s ease, box-shadow .16s ease; }
.search-input-shell:focus-within { border-color: var(--job-blue); box-shadow: 0 0 0 3px var(--ui-focus, rgba(8, 105, 216, .13)); }
.search-input-shell svg { width: 18px; fill: none; stroke: var(--job-blue); stroke-linecap: round; stroke-width: 1.8; }
.search-input-shell input { min-width: 0; border: 0; outline: 0; background: transparent; color: var(--job-ink); font-size: 15px; font-weight: 650; }
.search-input-shell input::placeholder { color: var(--job-muted); font-weight: 500; }
.search-rail > button { display: inline-flex; min-width: 136px; min-height: 42px; align-items: center; justify-content: center; gap: 8px; border: 0; border-radius: 10px; background: var(--job-blue); color: #fff; padding: 0 18px; cursor: pointer; font-size: 13px; font-weight: 820; box-shadow: 0 7px 18px color-mix(in srgb, var(--job-blue) 18%, transparent); }
.search-rail > button:hover:not(:disabled) { background: var(--ui-accent-hover, #0058c7); }
.search-rail > button:disabled { cursor: wait; opacity: .68; }
.button-loader { width: 13px; height: 13px; flex: none; border: 2px solid currentColor; border-right-color: transparent; border-radius: 50%; animation: job-spin .7s linear infinite; }
.live-track { position: absolute; right: 0; bottom: 0; left: 0; height: 3px; background: var(--job-blue-soft); }
.live-track i { display: block; width: 32%; height: 100%; background: linear-gradient(90deg, var(--job-blue), var(--job-teal)); transition: width .35s ease; }
.search-rail.searching .live-track i { width: 100%; animation: search-pulse 1.1s ease-in-out infinite alternate; }

.job-alert { display: flex; align-items: center; justify-content: space-between; gap: 20px; border: 1px solid #efd8b1; border-radius: 11px; background: #fff9ed; color: #795721; padding: 11px 14px; }
.job-alert > div { min-width: 0; }
.job-alert .connection-actions { display:flex; flex-wrap:wrap; gap:8px; }
.job-alert strong { font-size: 12px; }
.job-alert p { margin: 3px 0 0; font-size: 11px; line-height: 1.55; }
.job-alert .setup-path { color: #8b6b35; }
.job-alert code { font-family: var(--ui-font-utility, Consolas, monospace); font-size: 10px; }
.multi-selection-error { margin: -4px 0 0; border-left: 3px solid #c94955; border-radius: 0 8px 8px 0; background: #fff3f4; color: #a33b46; padding: 7px 10px; font-size: 11px; font-weight: 750; }
.job-alert button,.detail-error button { flex: none; border: 1px solid #ddb96f; border-radius: 7px; background: #fff; color: #805d22; padding: 7px 10px; cursor: pointer; font-size: 11px; font-weight: 750; }

.job-browser { position: relative; z-index: 1; display: grid; min-height: 0; flex: 1; grid-template-columns: 370px minmax(0, 1fr); overflow: hidden; border: 1px solid var(--job-line); border-radius: 14px; background: var(--job-paper); }
.job-results { min-width: 0; overflow-y: auto; border-right: 1px solid var(--job-line); background: color-mix(in srgb, var(--job-canvas) 65%, var(--job-paper)); }
.result-heading { position: sticky; z-index: 3; top: 0; display: flex; align-items: center; justify-content: space-between; gap: 14px; border-bottom: 1px solid var(--job-line); background: color-mix(in srgb, var(--job-paper) 94%, transparent); backdrop-filter: blur(12px); padding: 14px 15px 12px 20px; }
.result-heading > div { display: grid; min-width: 0; gap: 3px; }
.result-heading strong { overflow: hidden; color: var(--job-ink); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.result-heading span { color: var(--job-muted); font-size: 10px; }
.result-count { flex: none; border-radius: 999px; background: var(--job-blue-soft); color: var(--job-blue-ink) !important; padding: 5px 8px; font-family: var(--ui-font-utility, Consolas, monospace); }
.result-empty,.detail-empty { display: grid; place-items: center; align-content: center; color: var(--job-muted); padding: 28px; text-align: center; }
.result-empty { min-height: 260px; }
.result-empty > span,.detail-empty > span { display: grid; width: 42px; height: 42px; place-items: center; border-radius: 13px; background: var(--job-blue-soft); color: var(--job-blue); font-size: 22px; }
.result-empty strong,.detail-empty strong { margin-top: 13px; color: var(--job-ink); font-size: 14px; }
.result-empty p,.detail-empty p { max-width: 330px; margin: 7px 0 0; font-size: 11px; line-height: 1.65; }
.detail-empty { min-width: 0; background: var(--job-paper); }
.job-card-list { display: grid; gap: 9px; padding: 11px; }
.job-card { position: relative; display: grid; gap: 12px; width: 100%; overflow: hidden; border: 1px solid transparent; border-radius: 12px; background: var(--job-paper); color: var(--job-ink); padding: 15px 14px 13px 17px; cursor: pointer; text-align: left; transition: border-color .16s ease, box-shadow .16s ease, transform .16s ease; }
.job-card:hover { border-color: var(--job-line-strong); transform: translateY(-1px); }
.job-card.active { border-color: var(--job-blue); box-shadow: 0 8px 22px color-mix(in srgb, var(--job-blue) 9%, transparent); }
.job-card.selected { border-color: color-mix(in srgb, var(--job-teal) 55%, var(--job-line)); background: color-mix(in srgb, var(--job-teal) 5%, var(--job-paper)); }
.job-card.has-selection { padding-left: 45px; }
.job-card:focus-visible { outline: 0; border-color: var(--job-blue); box-shadow: 0 0 0 3px var(--ui-focus, rgba(8,105,216,.13)); }
.card-track-node { position: absolute; top: 17px; bottom: 17px; left: 0; width: 3px; border-radius: 0 3px 3px 0; background: transparent; }
.job-card.active .card-track-node { background: linear-gradient(var(--job-blue), var(--job-teal)); }
.job-card.selected .card-track-node { background: var(--job-teal); }
.job-selection-checkbox { position: absolute; z-index: 2; top: 14px; left: 13px; display: grid; width: 22px; height: 22px; place-items: center; border: 1px solid var(--job-line-strong); border-radius: 7px; background: var(--job-paper); color: #fff; padding: 0; cursor: pointer; }
.job-selection-checkbox:hover,.job-selection-checkbox:focus-visible { border-color: var(--job-blue); outline: 0; box-shadow: 0 0 0 3px var(--ui-focus, rgba(8,105,216,.13)); }
.job-selection-checkbox.checked { border-color: var(--job-teal); background: var(--job-teal); font-size: 13px; font-weight: 900; }
.job-selection-checkbox.loading { cursor: progress; }
.selection-detail-loader { position: absolute; right: -4px; bottom: -4px; width: 8px; height: 8px; border: 2px solid var(--job-paper); border-top-color: var(--job-blue); border-radius: 50%; background: var(--job-paper); animation: jobSpin .8s linear infinite; }
.job-selection-error { border-radius: 7px; background: #fff3f4; color: #a33b46; padding: 6px 8px; font-size: 10px; line-height: 1.45; }
.job-card-topline { display: flex; min-width: 0; align-items: start; justify-content: space-between; gap: 12px; }
.job-card.selected .job-card-topline { padding-right: 38px; }
.job-card-topline strong { min-width: 0; overflow: hidden; font-size: 15px; font-weight: 750; letter-spacing: -.015em; text-overflow: ellipsis; white-space: nowrap; }
.job-card-topline em { flex: none; color: var(--job-red); font-size: 15px; font-style: normal; font-weight: 800; }
.job-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.job-tags i { border-radius: 5px; background: color-mix(in srgb, var(--job-canvas) 76%, var(--job-paper)); color: var(--job-copy); padding: 4px 7px; font-size: 10px; font-style: normal; }
.job-tags i.platform-tag { background: color-mix(in srgb, var(--job-blue) 10%, var(--job-paper)); color: var(--job-blue-ink); font-weight: 750; }
.company-row { display: grid; grid-template-columns: 23px minmax(0, 1fr) auto; align-items: center; gap: 8px; padding-top: 1px; }
.company-mark,.company-logo { display: grid; place-items: center; border-radius: 7px; background: linear-gradient(145deg, var(--job-blue-soft), color-mix(in srgb, var(--job-teal) 12%, #fff)); color: var(--job-blue-ink); font-weight: 850; }
.company-mark { width: 23px; height: 23px; font-size: 10px; }
.company-name { min-width: 0; overflow: hidden; color: var(--job-copy); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.company-location { color: var(--job-muted); font-size: 10px; }
.load-more { display: flex; width: calc(100% - 22px); min-height: 38px; align-items: center; justify-content: center; gap: 7px; margin: 1px 11px 14px; border: 1px solid var(--job-line); border-radius: 9px; background: var(--job-paper); color: var(--job-blue-ink); cursor: pointer; font-size: 11px; font-weight: 750; }
.load-more:hover:not(:disabled) { border-color: var(--job-blue); background: var(--job-blue-soft); }
.load-more:disabled { opacity: .7; }
.result-end { margin: 4px 0 18px; color: var(--job-muted); font-size: 10px; text-align: center; }

.job-detail { position: relative; min-width: 0; overflow: hidden; background: var(--job-paper); transition: opacity .18s ease; }
.detail-back-button { display:none; min-height:40px; border:1px solid var(--job-line); border-radius:9px; background:var(--job-paper); color:var(--job-blue-ink); padding:7px 11px; font-size:12px; font-weight:800; }
.detail-hero-actions { display: flex; flex: none; align-items: center; gap: 12px; }
.detail-refreshing { position: absolute; z-index: 5; top: 14px; right: 20px; display: inline-flex; align-items: center; gap: 7px; border: 1px solid var(--job-line); border-radius: 999px; background: var(--job-paper); color: var(--job-blue-ink); padding: 6px 10px; font-size: 10px; font-weight: 750; box-shadow: 0 8px 22px rgba(20, 33, 61, .08); }
.detail-hero { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 24px; border-bottom: 1px solid var(--job-line); padding: 22px 28px 18px; }
.detail-title-block { position: relative; min-width: 0; }
.detail-track-node { position: absolute; top: 2px; bottom: 2px; left: -28px; width: 3px; border-radius: 0 3px 3px 0; background: linear-gradient(var(--job-blue), var(--job-teal)); }
.title-line { display: flex; min-width: 0; align-items: baseline; gap: 20px; }
.title-line h3 { min-width: 0; margin: 0; color: var(--job-ink); font-family: var(--ui-font-display, "Segoe UI Variable Display", sans-serif); font-size: clamp(22px, 2vw, 29px); font-weight: 770; letter-spacing: -.035em; }
.title-line > strong { flex: none; color: var(--job-red); font-size: 24px; letter-spacing: -.02em; }
.detail-meta { display: flex; flex-wrap: wrap; gap: 8px 18px; margin-top: 11px; color: var(--job-copy); font-size: 12px; }
.detail-meta span { display: inline-flex; align-items: center; gap: 5px; }
.detail-meta svg { width: 15px; fill: none; stroke: var(--job-muted); stroke-width: 1.7; }
.freshness { display: flex; flex-direction: column; align-items: end; justify-content: center; gap: 5px; color: var(--job-copy); }
.freshness > span { display: inline-flex; align-items: center; gap: 7px; font-size: 11px; font-weight: 750; }
.freshness small { color: var(--job-muted); font-family: var(--ui-font-utility, Consolas, monospace); font-size: 9px; }
.company-strip { display: grid; grid-template-columns: 42px minmax(0, 1fr) auto; align-items: center; gap: 12px; border-bottom: 1px solid var(--job-line); background: color-mix(in srgb, var(--job-canvas) 45%, var(--job-paper)); padding: 13px 28px; }
.company-logo { width: 42px; height: 42px; font-size: 16px; }
.company-strip > div { min-width: 0; }
.company-strip strong { display: block; overflow: hidden; color: var(--job-ink); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.company-strip p { margin: 4px 0 0; color: var(--job-muted); font-size: 10px; }
.recruiter-online { display: inline-flex; align-items: center; gap: 8px; color: var(--job-copy); font-size: 10px; }
.recruiter-online.offline i { background: #a8b1be; box-shadow: 0 0 0 3px rgba(168,177,190,.14); }
.detail-scroll { height: calc(100% - 150px); overflow-y: auto; padding: 20px 28px 40px; }
.detail-error { display: flex; align-items: center; justify-content: space-between; gap: 14px; border: 1px solid #efd8b1; border-radius: 10px; background: #fff9ed; padding: 12px 14px; }
.detail-error strong { color: #795721; font-size: 12px; }
.detail-error p { flex: 1; margin: 0; color: #8b6b35; font-size: 11px; }
.job-summary { border-left: 3px solid var(--job-blue); border-radius: 0 10px 10px 0; background: color-mix(in srgb, var(--job-blue-soft) 62%, var(--job-paper)); padding: 13px 16px; }
.skill-list,.welfare-list { display: flex; flex-wrap: wrap; gap: 7px; }
.skill-list span { border: 1px solid color-mix(in srgb, var(--job-blue) 18%, var(--job-line)); border-radius: 6px; background: var(--job-paper); color: var(--job-blue-ink); padding: 5px 8px; font-size: 10px; font-weight: 750; }
.description-section { max-width: 960px; margin-top: 23px; color: var(--job-copy); }
.description-section h4 { display: flex; align-items: center; gap: 9px; margin: 0 0 10px; color: var(--job-ink); font-size: 16px; font-weight: 790; }
.description-section h4::before { width: 4px; height: 14px; border-radius: 3px; background: var(--job-blue); content: ""; }
.description-section p { margin: 0 0 8px; font-size: 13px; line-height: 1.85; }
.job-description { color: var(--job-copy); font-family: inherit; font-size: 13px; line-height: 1.9; white-space: pre-wrap; word-break: break-word; }
.description-empty { color: var(--job-muted); }
.welfare-list span { border-radius: 6px; background: color-mix(in srgb, var(--job-teal) 8%, var(--job-paper)); color: #167777; padding: 6px 9px; font-size: 10px; font-weight: 700; }
.source-link { display: inline-flex; align-items: center; gap: 6px; margin-top: 25px; color: var(--job-blue-ink); font-size: 11px; font-weight: 750; text-decoration: none; }
.source-link:hover { text-decoration: underline; }
.job-browser.is-searching .job-card-list,.job-browser.is-searching .job-detail { opacity: .48; pointer-events: none; }

@keyframes job-spin { to { transform: rotate(360deg); } }
@keyframes search-pulse { from { filter: saturate(.8); opacity: .58; } to { filter: saturate(1.2); opacity: 1; } }
@keyframes status-pulse { from { opacity: .35; } to { opacity: 1; } }

@container job-search (max-width:1000px) {
  .search-rail {
    grid-template-columns:auto minmax(0,1fr) auto;
    gap:10px;
  }

  .search-input-shell {
    grid-column:2 / 4;
  }

  .search-rail > :deep(.job-city-picker) {
    grid-row:2;
    grid-column:2;
    justify-self:start;
  }

  .search-rail > button {
    grid-row:2;
    grid-column:3;
  }

  .job-browser {
    grid-template-columns:320px minmax(0,1fr);
  }

  .detail-hero,
  .company-strip,
  .detail-scroll {
    padding-right:20px;
    padding-left:20px;
  }
}

@container job-search (max-width:760px) {
  .job-workspace-header {
    grid-template-columns:minmax(0,1fr);
    gap:10px;
  }

  .workspace-header-actions {
    flex-wrap:wrap;
  }

  .search-rail {
    grid-template-columns:minmax(0,1fr);
    padding:12px;
  }

  .search-rail > label,
  .search-input-shell,
  .search-rail > :deep(.job-city-picker),
  .search-rail > button {
    grid-row:auto;
    grid-column:1;
    width:100%;
  }

  .job-browser {
    min-height:520px;
    grid-template-columns:minmax(0,1fr);
    grid-template-rows:minmax(0,1fr);
  }

  .job-results,
  .job-detail,
  .detail-empty {
    min-width:0;
    grid-row:1;
    grid-column:1;
  }

  .job-detail {
    display:none;
  }

  .job-browser.show-compact-detail .job-results {
    display:none;
  }

  .job-browser.show-compact-detail .job-detail {
    display:block;
  }

  .detail-back-button {
    display:inline-flex;
    align-items:center;
    margin:12px 14px 0;
  }

  .detail-refreshing {
    top:14px;
    right:14px;
  }

  .detail-hero {
    grid-template-columns:minmax(0,1fr);
    gap:12px;
    padding:16px 16px 14px;
  }

  .title-line {
    align-items:flex-start;
    flex-direction:column;
    gap:7px;
  }

  .title-line h3 {
    overflow-wrap:anywhere;
  }

  .freshness {
    align-items:start;
  }

  .company-strip {
    grid-template-columns:36px minmax(0,1fr);
    padding:12px 16px;
  }

  .company-logo {
    width:36px;
    height:36px;
  }

  .recruiter-online {
    grid-column:2;
  }

  .detail-scroll {
    height:calc(100% - 238px);
    padding:16px 16px 32px;
  }
}

@media (prefers-reduced-motion: reduce) { .button-loader,.search-rail.searching .live-track i,.connection-badge.is-checking i { animation: none; } .job-card,.search-input-shell,.live-track i { transition: none; } }
.location-attribution{align-self:flex-start;color:var(--job-muted);font-size:10px}.location-prompt{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 10px;border:1px solid var(--job-line);border-radius:9px;background:var(--job-paper);font-size:12px}.location-prompt small{font-size:10px;color:var(--job-muted)}.location-prompt p{margin:0;line-height:1.6}.location-prompt button{flex:none;border:1px solid var(--job-line-strong);border-radius:7px;background:var(--job-blue-soft);color:var(--job-blue-ink);padding:7px 10px;cursor:pointer}
.job-workspace.is-chat{gap:10px}.is-chat :deep(*){box-sizing:border-box}.is-chat .workspace-intro h2{font-size:15px}.is-chat .workspace-intro p{font-size:11px}.is-chat .job-browser{height:480px;min-height:0;flex:none}.is-chat .search-rail{grid-template-columns:minmax(0,1fr) auto auto;gap:8px;padding:10px}.is-chat .search-rail>label{grid-column:1/-1}.is-chat .search-input-shell{min-width:0;grid-column:auto;grid-row:auto}.is-chat .search-rail>button{grid-column:auto;grid-row:auto;width:auto}.is-chat .search-rail :deep(.city-picker){grid-column:auto;grid-row:auto;min-width:0}.is-chat .workspace-header-actions{min-width:0}
@container job-search (max-width:760px){.is-chat .search-rail{grid-template-columns:minmax(0,1fr) auto}.is-chat .search-input-shell{grid-column:1/-1}.is-chat .job-browser{height:460px}.is-chat .workspace-intro p{display:none}}
</style>
