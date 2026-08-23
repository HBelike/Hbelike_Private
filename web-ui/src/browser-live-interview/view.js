export const INTERVIEW_MASTER_PATH = '/career/interview-master'
export const QWEN_ASR_PRICE_PER_SECOND = 0.00033

export function isInterviewMasterPath(pathname) {
  return pathname === INTERVIEW_MASTER_PATH
}

export function formatDuration(totalSeconds) {
  const value = Math.max(0, Math.floor(Number(totalSeconds) || 0))
  const hours = Math.floor(value / 3600)
  const minutes = Math.floor((value % 3600) / 60)
  const seconds = value % 60
  return [hours ? String(hours).padStart(2, '0') : null, String(minutes).padStart(2, '0'), String(seconds).padStart(2, '0')]
    .filter(Boolean)
    .join(':')
}

export function estimateAsrCost(totalSeconds, trackCount = 1) {
  const seconds = Math.max(0, Number(totalSeconds) || 0)
  const tracks = Math.max(1, Math.min(2, Number(trackCount) || 1))
  return Number((seconds * tracks * QWEN_ASR_PRICE_PER_SECOND).toFixed(4))
}

export function pickInitialAnswerModel(models, preferredId = '') {
  const ready = (models || []).filter((item) => item.readiness === 'ready')
  return ready.some((item) => item.id === preferredId) ? preferredId : ready[0]?.id || ''
}

export function answerPreviewLines(answerText, limit = 5) {
  return String(answerText || '')
    .split(/\r?\n/)
    .map((line) => line.replace(/^#{1,6}\s*/, '').trim())
    .filter(Boolean)
    .slice(0, limit)
}
