import { normalizeBossExtensionConnection } from './boss-extension-onboarding.js'

const CONNECTION_HINT = '浏览器助手暂时无法连接，请重新检测；如果刚安装或更新过助手，请刷新当前页面。'

export function createJobLibraryConnection({ ping, onChange = () => {} }) {
  let revision = 0
  let pending = null
  let disposed = false
  let state = { status: 'checking', version: '', capabilities: [], error: '' }

  function publish(next) {
    state = { ...state, ...next }
    if (!disposed) onChange({ ...state })
  }

  function check() {
    if (disposed) return Promise.resolve(false)
    if (pending) return pending
    const token = ++revision
    publish({ status: 'checking' })
    const request = Promise.resolve().then(ping).then((payload) => {
      if (disposed || token !== revision) return false
      const connection = normalizeBossExtensionConnection(payload)
      publish({ status: connection.status, version: connection.version || state.version,
        capabilities: connection.capabilities,
        error: connection.status === 'ready' ? '' : CONNECTION_HINT })
      return connection.status === 'ready'
    }).catch((error) => {
      if (disposed || token !== revision) return false
      publish({ status: 'unavailable', capabilities: [], error: error?.message ? `${error.message} ${CONNECTION_HINT}` : CONNECTION_HINT })
      return false
    }).finally(() => {
      if (pending === request) pending = null
    })
    pending = request
    return request
  }

  function handleSignal(signal) {
    if (disposed || !['ready', 'disconnected'].includes(signal?.type)) return Promise.resolve(false)
    // 注入新桥接后不能继续等待旧桥接的检测，也不能接受旧检测的晚返回。
    revision += 1
    pending = null
    if (signal.type === 'disconnected') {
      publish({ status: 'unavailable', capabilities: [], error: '浏览器助手连接已断开，请刷新页面或重新检测。' })
      return Promise.resolve(false)
    }
    return check()
  }

  function dispose() {
    disposed = true
    revision += 1
    pending = null
  }

  return { check, handleSignal, dispose }
}
