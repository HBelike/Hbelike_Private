import { greetingMessageError } from './career-greeting-preview.js'

const WEB_CHANNEL = 'find-job-job-library-web-v1'
const EXTENSION_CHANNEL = 'find-job-job-library-extension-v1'
const DEFAULT_TIMEOUT_MS = 22000
let requestSequence = 0
let connectionEpoch = 0
let pendingPing = null
let subscriptionWindow = null
const connectionListeners = new Set()
const pendingPingCancellations = new Set()
const handledConnectionEvents = new WeakSet()

function isTrustedExtensionMessage(event) {
  return event.source === window && event.origin === window.location.origin
    && event.data?.channel === EXTENSION_CHANNEL
}

function handleConnectionMessage(event) {
  if (!isTrustedExtensionMessage(event) || !['ready', 'disconnected'].includes(event.data.type)
    || handledConnectionEvents.has(event)) return
  handledConnectionEvents.add(event)
  connectionEpoch += 1
  // 生命周期变化只说明桥发生变化，必须重新握手确认后台实际可用。
  pendingPing = null
  for (const cancel of [...pendingPingCancellations]) cancel()
  const update = {
    type: event.data.type,
    version: cloneString(event.data.version || event.data.data?.version),
    epoch: connectionEpoch
  }
  for (const listener of [...connectionListeners]) {
    try {
      listener(update)
    } catch {
      // 单个视图的订阅错误不能阻断其他视图重新握手。
    }
  }
}

export function subscribeJobLibraryConnection(listener) {
  if (typeof listener !== 'function') throw new TypeError('连接监听器必须是函数')
  const subscription = (update) => listener(update)
  connectionListeners.add(subscription)
  if (!subscriptionWindow) {
    subscriptionWindow = window
    subscriptionWindow.addEventListener('message', handleConnectionMessage)
  }
  let subscribed = true
  return () => {
    if (!subscribed) return
    subscribed = false
    connectionListeners.delete(subscription)
    if (!connectionListeners.size && subscriptionWindow) {
      subscriptionWindow.removeEventListener('message', handleConnectionMessage)
      subscriptionWindow = null
    }
  }
}

const JOB_DETAIL_STRING_FIELDS = [
  'platform',
  'id',
  'jobKey',
  'securityId',
  'jobId',
  'bossId',
  'lid',
  'title',
  'description',
  'salary',
  'experience',
  'degree',
  'city',
  'district',
  'address',
  'recruiter',
  'recruiterAvatar',
  'company',
  'companyShort',
  'companyLogo',
  'industry',
  'scale',
  'stage',
  'sourceUrl'
]

function cloneString(value) {
  if (typeof value === 'string') return value
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return ''
}

function cloneStringList(value) {
  if (!Array.isArray(value)) return []
  return Array.from(value, cloneString).filter(Boolean)
}

function chatExecutionPayload(value) {
  return { account_id: cloneString(value?.account_id), executor_id: cloneString(value?.executor_id), revision: Number(value?.revision) || 0 }
}

export function createJobDetailPayload(job = {}) {
  const source = job && typeof job === 'object' ? job : {}
  const fallback = Object.fromEntries(
    JOB_DETAIL_STRING_FIELDS.map((field) => [field, cloneString(source[field])])
  )
  fallback.skills = cloneStringList(source.skills)
  fallback.welfare = cloneStringList(source.welfare)
  fallback.recruiterOnline = Boolean(source.recruiterOnline)
  fallback.labels = cloneStringList(source.labels)
  fallback.adapterData = { jobKind: cloneString(source.adapterData?.jobKind) }

  return {
    platform: fallback.platform || 'boss',
    securityId: fallback.securityId,
    jobId: fallback.jobId,
    fallback
  }
}

export function createGreetingRequestPayload(job = {}, message = '') {
  const normalizedMessage = cloneString(message).trim()
  const validationError = greetingMessageError(normalizedMessage)
  if (validationError) throw new JobLibraryBridgeError('message_invalid', validationError)
  return {
    securityId: cloneString(job?.securityId),
    jobId: cloneString(job?.jobId),
    bossId: cloneString(job?.bossId),
    lid: cloneString(job?.lid),
    message: normalizedMessage
  }
}

export class JobLibraryBridgeError extends Error {
  constructor(code, message, details = {}) {
    super(message)
    this.name = 'JobLibraryBridgeError'
    this.code = code
    this.retryable = details.retryable === true
    this.stopBatch = details.stopBatch === true
    this.retryMode = typeof details.retryMode === 'string' ? details.retryMode : ''
    this.submissionState = typeof details.submissionState === 'string' ? details.submissionState : ''
    this.defaultGreetingSent = details.defaultGreetingSent === true
  }
}

function requestExtension(action, payload = {}, timeoutMs = DEFAULT_TIMEOUT_MS) {
  return new Promise((resolve, reject) => {
    const requestId = `job-library-${Date.now()}-${++requestSequence}`
    const requestEpoch = connectionEpoch
    let settled = false
    const timeoutId = window.setTimeout(() => {
      finish(reject, new JobLibraryBridgeError(
        action === 'ping' ? 'bridge_timeout' : 'request_timeout',
        action === 'ping'
          ? '职位库浏览器助手连接超时，请检查扩展连接后重试。'
          : action.includes('xiaohongshu')
            ? '浏览器助手响应超时，请确认小红书页面可以正常访问。'
            : action.includes('liepin') || (action === 'search_jobs' && payload.platform === 'liepin')
              ? '猎聘官方搜索响应超时，请稍后重试。'
              : '职位库浏览器助手响应超时，请确认招聘平台页面可以正常访问。'
      ))
    }, timeoutMs)

    function finish(callback, value) {
      if (settled) return
      settled = true
      window.clearTimeout(timeoutId)
      window.removeEventListener('message', handleMessage)
      pendingPingCancellations.delete(cancelPing)
      callback(value)
    }

    function cancelPing() {
      finish(reject, new JobLibraryBridgeError('bridge_disconnected', '浏览器助手连接已变化，正在重新检测。'))
    }

    function handleMessage(event) {
      if (!isTrustedExtensionMessage(event)) return
      if (action === 'ping' && ['ready', 'disconnected'].includes(event.data.type)) {
        handleConnectionMessage(event)
        return
      }
      const message = event.data
      if (!message || message.channel !== EXTENSION_CHANNEL || message.requestId !== requestId) return
      if (action === 'ping' && requestEpoch !== connectionEpoch) {
        cancelPing()
        return
      }
      if (message.ok) {
        finish(resolve, message.data)
        return
      }
      finish(reject, new JobLibraryBridgeError(
        message.error?.code ?? 'extension_error',
        message.error?.message ?? '职位库助手调用失败。',
        message.error ?? {}
      ))
    }

    window.addEventListener('message', handleMessage)
    if (action === 'ping') pendingPingCancellations.add(cancelPing)
    try {
      window.postMessage({ channel: WEB_CHANNEL, requestId, action, payload }, window.location.origin)
    } catch {
      finish(reject, new JobLibraryBridgeError('bridge_error', '浏览器助手连接请求失败，请重试。'))
    }
  })
}

export async function pingExtensionWithRetry(
  pingOnce,
  { attempts = 2, wait = () => new Promise((resolve) => window.setTimeout(resolve, 160)) } = {}
) {
  let lastError
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      return await pingOnce()
    } catch (error) {
      lastError = error
      if (!(error instanceof JobLibraryBridgeError)
        || !['extension_unavailable', 'bridge_timeout', 'bridge_disconnected'].includes(error.code)
        || attempt >= attempts - 1) {
        throw error
      }
      await wait()
    }
  }
  throw lastError
}

export const jobLibraryBridge = {
  ping() {
    if (pendingPing) return pendingPing.promise
    const probe = { epoch: connectionEpoch, promise: null }
    probe.promise = pingExtensionWithRetry(() => (
      probe.epoch === connectionEpoch
        ? requestExtension('ping', {}, 1800)
        : jobLibraryBridge.ping()
    )).finally(() => {
      // 旧探测完成时不能清除 ready 后创建的新探测。
      if (pendingPing === probe) pendingPing = null
    })
    pendingPing = probe
    return probe.promise
  },
  listCities() {
    return requestExtension('list_cities')
  },
  searchJobs(query, page = 1, city = {}, platform = 'boss') {
    const selectedPlatform = cloneString(platform) || 'boss'
    return requestExtension('search_jobs', {
      platform: selectedPlatform,
      query,
      page,
      cityCode: String(city?.code ?? ''),
      cityName: String(city?.name ?? '')
    }, selectedPlatform === 'liepin' ? 35000 : DEFAULT_TIMEOUT_MS)
  },
  getLiepinState() {
    return requestExtension('liepin_state')
  },
  openLiepinSettings() {
    return requestExtension('liepin_open_settings')
  },
  getJobDetail(job) {
    return requestExtension('get_job_detail', createJobDetailPayload(job))
  },
  getBossChatState() {
    return requestExtension('boss_chat_state', {}, 45000)
  },
  listBossConversations({ accountId, page = 1, execution } = {}) {
    return requestExtension('boss_chat_list', { accountId: cloneString(accountId).trim(), page: Math.max(1, Math.min(100, Math.floor(Number(page) || 1))), execution: chatExecutionPayload(execution) }, 45000)
  },
  getBossMessages({ accountId, peerId, securityId, page = 1, count = 50, execution } = {}) {
    const payload = {
      accountId: cloneString(accountId).trim(),
      peerId: cloneString(peerId).trim(),
      securityId: cloneString(securityId).trim(),
      page: Math.max(1, Math.min(100, Math.floor(Number(page) || 1))),
      count: Math.max(1, Math.min(100, Math.floor(Number(count) || 50))),
      execution: chatExecutionPayload(execution)
    }
    if (!payload.accountId || !payload.peerId || !payload.securityId) {
      throw new JobLibraryBridgeError('chat_identity_missing', '会话标识不完整，请重新同步 BOSS 会话列表。')
    }
    return requestExtension('boss_chat_messages', payload, 45000)
  },
  replyBossMessage({ accountId, peerId, securityId, text, clientOperationId, execution } = {}) {
    return requestExtension('boss_chat_reply', {
      accountId: cloneString(accountId).trim(), peerId: cloneString(peerId).trim(), securityId: cloneString(securityId).trim(),
      text: cloneString(text), clientOperationId: cloneString(clientOperationId), execution: chatExecutionPayload(execution)
    }, 45000)
  },
  cancelBossChatOperation({ clientOperationId, execution } = {}) {
    return requestExtension('boss_chat_cancel', { clientOperationId: cloneString(clientOperationId), execution: chatExecutionPayload(execution) }, 10000)
  },
  resumeBossChat({ accountId, execution } = {}) {
    return requestExtension('boss_chat_resume', { accountId: cloneString(accountId), execution: chatExecutionPayload(execution) }, 45000)
  },
  searchXiaohongshuNotes(keyword, limit = 20) {
    return requestExtension('search_xiaohongshu_notes', {
      keyword: cloneString(keyword),
      limit: Math.max(5, Math.min(50, Number(limit) || 20))
    }, 90000)
  },
  getXiaohongshuNote(card) {
    return requestExtension('get_xiaohongshu_note', {
      noteId: cloneString(card?.noteId),
      signedUrl: cloneString(card?.signedUrl),
      title: cloneString(card?.title),
      authorName: cloneString(card?.authorName)
    }, 60000)
  },
  preflightGreeting(job, message) {
    return requestExtension('preflight_greeting', createGreetingRequestPayload(job, message))
  },
  sendGreeting(job, message) {
    return requestExtension('send_greeting', createGreetingRequestPayload(job, message), 60000)
  },
  retryGreetingMessage(job, message, context = {}) {
    return requestExtension('retry_greeting_message', {
      ...createGreetingRequestPayload(job, message),
      defaultGreetingSent: context.defaultGreetingSent === true
    }, 60000)
  }
}
