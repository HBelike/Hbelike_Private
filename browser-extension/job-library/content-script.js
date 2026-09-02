(() => {
  const BRIDGE_STATE_KEY = '__findJobBrowserBridge'
  const WEB_CHANNEL = 'find-job-job-library-web-v1'
  const EXTENSION_CHANNEL = 'find-job-job-library-extension-v1'
  const ALLOWED_ACTIONS = new Set([
    'ping',
    'list_cities',
    'search_jobs',
    'get_job_detail',
    'search_xiaohongshu_notes',
    'get_xiaohongshu_note',
    'preflight_greeting',
    'send_greeting',
    'retry_greeting_message',
    'claim_assessment_launch',
    'refresh_assessment_problem',
    'append_assessment_problem'
  ])

  const previous = globalThis[BRIDGE_STATE_KEY]
  if (previous?.listener) window.removeEventListener('message', previous.listener)

  function postResponse(requestId, payload) {
    window.postMessage({
      channel: EXTENSION_CHANNEL,
      requestId,
      ...payload
    }, window.location.origin)
  }

  function errorMessage(error) {
    if (typeof error?.message === 'string' && error.message.trim()) return error.message
    if (typeof error === 'string' && error.trim()) return error
    return '浏览器助手连接失败，请重新加载页面。'
  }

  function isExtensionContextInvalid(error) {
    try {
      if (!chrome.runtime?.id) return true
    } catch {
      return true
    }
    return /Extension context invalidated/i.test(errorMessage(error))
  }

  function disconnectInvalidBridge(error) {
    if (!isExtensionContextInvalid(error)) return false
    window.removeEventListener('message', handleMessage)
    if (globalThis[BRIDGE_STATE_KEY]?.listener === handleMessage) {
      delete globalThis[BRIDGE_STATE_KEY]
    }
    return true
  }

  function handleBridgeError(requestId, error) {
    // 扩展更新后，页面里旧的 content script 仍会短暂存活；让它静默退出，
    // 避免抢先向页面返回失败，同时允许新注入的桥接监听器继续处理请求。
    if (disconnectInvalidBridge(error)) return
    postResponse(requestId, {
      ok: false,
      error: {
        code: 'extension_unavailable',
        message: errorMessage(error)
      }
    })
  }

  function handleMessage(event) {
    if (event.source !== window || event.origin !== window.location.origin) return
    const message = event.data
    if (!message || message.channel !== WEB_CHANNEL) return
    if (typeof message.requestId !== 'string' || !ALLOWED_ACTIONS.has(message.action)) return

    let request
    try {
      if (!chrome.runtime?.id) {
        disconnectInvalidBridge(new Error('Extension context invalidated.'))
        return
      }
      request = chrome.runtime.sendMessage({
        channel: WEB_CHANNEL,
        action: message.action,
        requestId: message.requestId,
        payload: message.payload ?? {}
      })
    } catch (error) {
      handleBridgeError(message.requestId, error)
      return
    }

    Promise.resolve(request).then((response) => {
      postResponse(message.requestId, response ?? {
        ok: false,
        error: { code: 'empty_response', message: '浏览器助手没有返回结果，请重新加载页面。' }
      })
    }).catch((error) => {
      handleBridgeError(message.requestId, error)
    })
  }

  window.addEventListener('message', handleMessage)
  globalThis[BRIDGE_STATE_KEY] = {
    listener: handleMessage,
    version: chrome.runtime.getManifest().version
  }
  window.postMessage({ channel: EXTENSION_CHANNEL, type: 'ready' }, window.location.origin)
})()
