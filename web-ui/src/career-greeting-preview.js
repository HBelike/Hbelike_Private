export const GREETING_MESSAGE_TARGET_LENGTH = 200

export function greetingMessageLength(value) {
  return Array.from(String(value ?? '')).length
}

export function greetingMessageError(value) {
  const message = String(value ?? '').trim()
  if (!message) return '招呼语不能为空。'
  return ''
}

export function updateGreetingMessage(items, id, message) {
  return (Array.isArray(items) ? items : []).map((item) => item.id === id
    ? { ...item, message: String(message ?? ''), messageError: greetingMessageError(message) }
    : item)
}

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

export function createGreetingItems(jobs) {
  return (Array.isArray(jobs) ? jobs : []).map((job, index) => ({
    id: greetingJobKey(job),
    order: index + 1,
    job,
    message: '',
    revision: 0,
    included: true,
    status: 'generating',
    attemptCount: 0,
    lastAttemptAt: '',
    sentAt: '',
    retryMode: '',
    nextAttemptMode: 'full',
    defaultGreetingSent: false,
    evidence: [],
    jdHighlights: [],
    generationError: '',
    generatedModel: '',
    warnings: []
  }))
}

export function markGreetingGenerating(items, id) {
  return (Array.isArray(items) ? items : []).map((item) => item.id === id
    ? { ...item, status: 'generating', generationError: '' }
    : item)
}

export function applyGreetingResult(items, id, payload) {
  return (Array.isArray(items) ? items : []).map((item) => item.id === id
    ? {
        ...item,
        message: String(payload?.message || ''),
        revision: Number(item.revision || 0) + 1,
        status: 'ready',
        messageError: greetingMessageError(payload?.message),
        generationError: '',
        evidence: Array.isArray(payload?.resume_evidence)
          ? payload.resume_evidence.map((entry) => String(entry?.summary || '')).filter(Boolean)
          : [],
        jdHighlights: Array.isArray(payload?.jd_highlights)
          ? payload.jd_highlights.map((entry) => String(entry?.summary || '')).filter(Boolean)
          : [],
        generatedModel: String(payload?.model?.model_id || ''),
        warnings: Array.isArray(payload?.warnings) ? payload.warnings.map(String) : []
      }
    : item)
}

export function applyGreetingFailure(items, id, error, { preserveMessage = true } = {}) {
  return (Array.isArray(items) ? items : []).map((item) => item.id === id
    ? {
        ...item,
        status: preserveMessage && item.message ? 'ready' : 'generation_failed',
        generationError: error instanceof Error ? error.message : String(error || '招呼语生成失败')
      }
    : item)
}

export function queueGreetingItems(items) {
  return (Array.isArray(items) ? items : []).map((item) => ({
    ...item,
    messageError: greetingMessageError(item.message),
    status: item.included && !greetingMessageError(item.message) && !['generating', 'generation_failed'].includes(item.status)
      ? 'queued'
      : 'excluded',
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
    ? greetingMessageError(item.message)
      ? { ...item, status: 'failed', error: greetingMessageError(item.message), errorCode: 'message_invalid', retryable: false }
      : { ...item, status: 'sent' }
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
      if (greetingMessageError(item.message)) return { ...item, error: greetingMessageError(item.message), messageError: greetingMessageError(item.message), errorCode: 'message_invalid', retryable: false }
      return {
        ...item,
        status: 'queued',
        error: '',
        errorCode: '',
        retryable: false,
        nextAttemptMode: item.retryMode === 'message' ? 'message' : 'full'
      }
    }
    if (index > retryIndex && item.included && item.status === 'stopped' && !greetingMessageError(currentItems[retryIndex].message)) {
      if (greetingMessageError(item.message)) return { ...item, status: 'excluded', messageError: greetingMessageError(item.message) }
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
