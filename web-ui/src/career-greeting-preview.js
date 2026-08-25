const PREVIEW_EVIDENCE = [
  '211 本科学历',
  '3 年平安银行全栈开发经验',
  '微服务与分布式项目落地经验',
  '从 0 到 1 完整投产上线经历',
  'xingxingtech.cn 已部署上线'
]

const GREETING_ENDINGS = [
  '如方便，期待进一步沟通。',
  '想进一步了解岗位，期待沟通。',
  '希望有机会和您进一步交流。'
]

export function normalizeGreetingLimit(value) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return 5
  return Math.max(1, Math.min(10, Math.round(parsed)))
}

export function greetingJobKey(job) {
  return String(job?.id ?? job?.jobId ?? job?.sourceUrl ?? `${job?.company ?? ''}:${job?.title ?? ''}`)
}

export function toggleGreetingJob(jobs, job, limit) {
  const currentJobs = Array.isArray(jobs) ? jobs : []
  const key = greetingJobKey(job)
  const exists = currentJobs.some((item) => greetingJobKey(item) === key)
  if (exists) {
    return {
      jobs: currentJobs.filter((item) => greetingJobKey(item) !== key),
      error: ''
    }
  }

  const max = normalizeGreetingLimit(limit)
  if (currentJobs.length >= max) {
    return { jobs: currentJobs, error: `本批最多选择 ${max} 个岗位。` }
  }
  return { jobs: [...currentJobs, job], error: '' }
}

export function needsGreetingRiskWarning(count) {
  return Number(count) > 5
}

function previewGreeting(job, revision) {
  const role = String(job?.title || '当前').trim()
  const ending = GREETING_ENDINGS[(Math.max(1, revision) - 1) % GREETING_ENDINGS.length]
  const salutation = job?.recruiter ? `${String(job.recruiter).split('·')[0].trim()}您好` : '您好'
  const focus = Array.isArray(job?.skills) && job.skills.length
    ? `，岗位关注${job.skills.slice(0, 2).join('、')}`
    : ''
  return `${salutation}，我是211本科，3年平安银行全栈开发经验，做过微服务、分布式项目及0到1投产。xingxingtech.cn 已上线，含技术热点抓取 workflow 和求职简历分析 Agent${focus}。我对${role}很感兴趣。${ending}`
}

export function createGreetingItems(jobs) {
  return (Array.isArray(jobs) ? jobs : []).map((job, index) => ({
    id: greetingJobKey(job),
    order: index + 1,
    job,
    message: previewGreeting(job, 1),
    revision: 1,
    included: true,
    status: 'ready',
    attemptCount: 0,
    lastAttemptAt: '',
    sentAt: '',
    retryMode: '',
    nextAttemptMode: 'full',
    defaultGreetingSent: false,
    evidence: [...PREVIEW_EVIDENCE],
    jdHighlights: Array.isArray(job?.skills) && job.skills.length
      ? job.skills.slice(0, 4)
      : ['岗位职责', '项目交付']
  }))
}

export function regenerateGreetingItem(item) {
  const revision = Number(item?.revision || 1) + 1
  return {
    ...item,
    revision,
    message: previewGreeting(item?.job, revision),
    status: 'ready'
  }
}

export function queueGreetingItems(items) {
  return (Array.isArray(items) ? items : []).map((item) => ({
    ...item,
    status: item.included ? 'queued' : 'excluded',
    nextAttemptMode: 'full'
  }))
}

export function recordGreetingAttempt(items, id, attemptedAt = new Date().toISOString()) {
  return (Array.isArray(items) ? items : []).map((item) => item.id === id
    ? {
        ...item,
        attemptCount: Number(item.attemptCount || 0) + 1,
        lastAttemptAt: attemptedAt
      }
    : item)
}

export function advanceGreetingSend(items) {
  const currentItems = Array.isArray(items) ? items : []
  const nextIndex = currentItems.findIndex((item) => item.status === 'queued')
  if (nextIndex < 0) return currentItems.map((item) => ({ ...item }))
  return currentItems.map((item, index) => index === nextIndex
    ? { ...item, status: 'sent' }
    : { ...item })
}

export function stopGreetingItems(items) {
  return (Array.isArray(items) ? items : []).map((item) => ({
    ...item,
    status: ['queued', 'preflighting'].includes(item.status) ? 'stopped' : item.status
  }))
}

export function updateGreetingItemStatus(items, id, status, error = '', details = {}) {
  return (Array.isArray(items) ? items : []).map((item) => item.id === id
    ? { ...item, status, error, ...details }
    : item)
}

export function retryGreetingItems(items, id) {
  const currentItems = Array.isArray(items) ? items : []
  const retryIndex = currentItems.findIndex((item) => item.id === id && item.status === 'failed' && item.retryable)
  if (retryIndex < 0) return currentItems.map((item) => ({ ...item }))
  return currentItems.map((item, index) => {
    if (index === retryIndex) {
      return {
        ...item,
        status: 'queued',
        error: '',
        errorCode: '',
        retryable: false,
        nextAttemptMode: item.retryMode === 'message' ? 'message' : 'full'
      }
    }
    if (index > retryIndex && item.included && item.status === 'stopped') {
      return {
        ...item,
        status: 'queued',
        error: '',
        errorCode: '',
        retryable: false,
        retryMode: '',
        nextAttemptMode: 'full'
      }
    }
    return { ...item }
  })
}

export function findGreetingFailureAction(items) {
  const currentItems = Array.isArray(items) ? items : []
  const retryableItem = currentItems.find((item) => item.status === 'failed' && item.retryable === true)
  if (retryableItem) return { itemId: retryableItem.id, type: 'retry' }

  const legacyVerificationItem = currentItems.find((item) => item.status === 'failed'
    && ['verification_required', 'login_or_verification_required'].includes(item.errorCode)
    && !item.submissionState)
  return legacyVerificationItem
    ? { itemId: legacyVerificationItem.id, type: 'update_extension' }
    : null
}
