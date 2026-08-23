const WEB_CHANNEL = 'find-job-job-library-web-v1'
const EXTENSION_CHANNEL = 'find-job-job-library-extension-v1'
const DEFAULT_TIMEOUT_MS = 22000
let requestSequence = 0

const JOB_DETAIL_STRING_FIELDS = [
  'id',
  'securityId',
  'jobId',
  'bossId',
  'lid',
  'title',
  'salary',
  'experience',
  'degree',
  'city',
  'district',
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

export function createJobDetailPayload(job = {}) {
  const source = job && typeof job === 'object' ? job : {}
  const fallback = Object.fromEntries(
    JOB_DETAIL_STRING_FIELDS.map((field) => [field, cloneString(source[field])])
  )
  fallback.skills = cloneStringList(source.skills)
  fallback.welfare = cloneStringList(source.welfare)
  fallback.recruiterOnline = Boolean(source.recruiterOnline)

  return {
    securityId: fallback.securityId,
    fallback
  }
}

export function createGreetingRequestPayload(job = {}, message = '', defaultGreetingDisabled = false) {
  return {
    securityId: cloneString(job?.securityId),
    jobId: cloneString(job?.jobId),
    bossId: cloneString(job?.bossId),
    lid: cloneString(job?.lid),
    message: cloneString(message),
    defaultGreetingDisabled: defaultGreetingDisabled === true
  }
}

export class JobLibraryBridgeError extends Error {
  constructor(code, message) {
    super(message)
    this.name = 'JobLibraryBridgeError'
    this.code = code
  }
}

function requestExtension(action, payload = {}, timeoutMs = DEFAULT_TIMEOUT_MS) {
  return new Promise((resolve, reject) => {
    const requestId = `job-library-${Date.now()}-${++requestSequence}`
    const timeoutId = window.setTimeout(() => {
      window.removeEventListener('message', handleMessage)
      reject(new JobLibraryBridgeError(
        'extension_unavailable',
        action === 'ping'
          ? '未检测到职位库浏览器助手，请先安装并启用扩展。'
          : '职位库浏览器助手响应超时，请确认 BOSS 页面可以正常访问。'
      ))
    }, timeoutMs)

    function handleMessage(event) {
      if (event.source !== window || event.origin !== window.location.origin) return
      const message = event.data
      if (!message || message.channel !== EXTENSION_CHANNEL || message.requestId !== requestId) return
      window.clearTimeout(timeoutId)
      window.removeEventListener('message', handleMessage)
      if (message.ok) {
        resolve(message.data)
        return
      }
      reject(new JobLibraryBridgeError(
        message.error?.code ?? 'extension_error',
        message.error?.message ?? '职位库助手调用失败。'
      ))
    }

    window.addEventListener('message', handleMessage)
    window.postMessage({ channel: WEB_CHANNEL, requestId, action, payload }, window.location.origin)
  })
}

export const jobLibraryBridge = {
  ping() {
    return requestExtension('ping', {}, 1800)
  },
  listCities() {
    return requestExtension('list_cities')
  },
  searchJobs(query, page = 1, city = {}) {
    return requestExtension('search_jobs', {
      query,
      page,
      cityCode: String(city?.code ?? ''),
      cityName: String(city?.name ?? '')
    })
  },
  getJobDetail(job) {
    return requestExtension('get_job_detail', createJobDetailPayload(job))
  },
  preflightGreeting(job, message, options = {}) {
    return requestExtension('preflight_greeting', createGreetingRequestPayload(
      job,
      message,
      options.defaultGreetingDisabled
    ))
  },
  sendGreeting(job, message, options = {}) {
    return requestExtension('send_greeting', createGreetingRequestPayload(
      job,
      message,
      options.defaultGreetingDisabled
    ), 60000)
  }
}
