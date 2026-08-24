export const BOSS_EXTENSION_VERSION = '0.2.2'
export const BOSS_EXTENSION_DOWNLOAD_URL = `/downloads/find-job-boss-helper-v${BOSS_EXTENSION_VERSION}.zip`
export const BOSS_EXTENSION_GUIDE_URL = '/boss-extension-guide.html'

export function normalizeBossExtensionConnection(payload) {
  if (payload?.connected !== true) return { status: 'missing', version: '' }
  return {
    status: 'ready',
    version: typeof payload.version === 'string' ? payload.version.trim() : ''
  }
}

export function bossExtensionGuideUrl(browser = 'chrome') {
  return `${BOSS_EXTENSION_GUIDE_URL}#${browser === 'edge' ? 'edge' : 'chrome'}`
}
