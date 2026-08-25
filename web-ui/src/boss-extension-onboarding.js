export const BOSS_EXTENSION_VERSION = '0.2.6'
export const BOSS_EXTENSION_DOWNLOAD_URL = `/downloads/find-job-boss-helper-v${BOSS_EXTENSION_VERSION}.zip`
export const BOSS_EXTENSION_GUIDE_URL = '/boss-extension-guide.html'
export const BOSS_EXTENSION_REQUIRED_CAPABILITIES = Object.freeze([
  'retry_greeting_message',
  'greeting_submission_state'
])

export function normalizeBossExtensionConnection(payload) {
  if (payload?.connected !== true) {
    return {
      status: 'missing',
      version: '',
      capabilities: [],
      greetingRetryReady: false,
      missingCapabilities: [...BOSS_EXTENSION_REQUIRED_CAPABILITIES]
    }
  }
  const capabilities = Array.isArray(payload.capabilities)
    ? [...new Set(payload.capabilities.filter((item) => typeof item === 'string'))]
    : []
  const capabilitySet = new Set(capabilities)
  const missingCapabilities = BOSS_EXTENSION_REQUIRED_CAPABILITIES.filter((item) => !capabilitySet.has(item))
  return {
    status: 'ready',
    version: typeof payload.version === 'string' ? payload.version.trim() : '',
    capabilities,
    greetingRetryReady: missingCapabilities.length === 0,
    missingCapabilities
  }
}

export function shouldOpenBossExtensionGate(status, { interactive = false } = {}) {
  return interactive && status === 'missing'
}

export function bossExtensionGuideUrl(browser = 'chrome') {
  return `${BOSS_EXTENSION_GUIDE_URL}#${browser === 'edge' ? 'edge' : 'chrome'}`
}
