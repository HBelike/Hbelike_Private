const WEB_CHANNEL = 'find-job-job-library-web-v1'
const EXTENSION_CHANNEL = 'find-job-job-library-extension-v1'
const ALLOWED_ACTIONS = new Set([
  'ping',
  'list_cities',
  'search_jobs',
  'get_job_detail',
  'preflight_greeting',
  'send_greeting',
  'retry_greeting_message'
])

function postResponse(requestId, payload) {
  window.postMessage({
    channel: EXTENSION_CHANNEL,
    requestId,
    ...payload
  }, window.location.origin)
}

window.addEventListener('message', (event) => {
  if (event.source !== window || event.origin !== window.location.origin) return
  const message = event.data
  if (!message || message.channel !== WEB_CHANNEL) return
  if (typeof message.requestId !== 'string' || !ALLOWED_ACTIONS.has(message.action)) return

  chrome.runtime.sendMessage({
    channel: WEB_CHANNEL,
    action: message.action,
    requestId: message.requestId,
    payload: message.payload ?? {}
  }).then((response) => {
    postResponse(message.requestId, response ?? {
      ok: false,
      error: { code: 'empty_response', message: '浏览器助手没有返回结果，请重新加载页面。' }
    })
  }).catch((error) => {
    postResponse(message.requestId, {
      ok: false,
      error: {
        code: 'extension_unavailable',
        message: error instanceof Error ? error.message : '浏览器助手连接失败，请重新加载页面。'
      }
    })
  })
})

window.postMessage({ channel: EXTENSION_CHANNEL, type: 'ready' }, window.location.origin)
