import { onBeforeUnmount, watch } from 'vue'
import { isMobileBrowser } from './platform-capabilities.js'

const stateKey = '__platformLayer'
const layers = []
let sequence = 0
let listening = false
let transition = null

function handlePopState(event) {
  const url = window.location.href
  let consumed = transition?.url === url
  let allowed = true
  while (layers.length && layers.at(-1).id !== event.state?.[stateKey]) {
    const layer = layers.at(-1)
    if (layer.url === url && !layer.canClose()) {
      window.history.pushState({ ...window.history.state, [stateKey]: layer.id }, '', url)
      consumed = true
      allowed = false
      break
    }
    layers.pop()
    layer.close()
    if (layer.url === url) consumed = true
  }
  const pending = transition
  transition = null
  pending?.resolve(allowed)
  // 同 URL 的返回只关闭界面层，不触发应用和子页面的路由重载。
  if (consumed) event.stopImmediatePropagation()
}

function back(steps, url) {
  const pending = { url }
  pending.promise = new Promise(resolve => { pending.resolve = resolve })
  transition = pending
  window.history.go(-steps)
  return pending.promise
}

async function withdraw(layer) {
  if (!layer) return
  const index = layers.indexOf(layer)
  if (index < 0) return
  layers.splice(index, 1)
  if (transition) await transition.promise
  if (window.history.state?.[stateKey] === layer.id && window.location.href === layer.url) {
    await back(1, layer.url)
  }
}

export async function dismissHistoryLayers() {
  if (transition && !await transition.promise) return false
  if (!layers.length || window.history.state?.[stateKey] !== layers.at(-1).id) return true
  if (layers.some(layer => !layer.canClose())) return false
  return back(layers.length, window.location.href)
}

export function useHistoryLayer(isOpen, close, canClose = () => true) {
  if (!isMobileBrowser()) return
  if (!listening) {
    window.addEventListener('popstate', handlePopState, true)
    listening = true
  }
  let entry = null
  let revision = 0
  const stop = watch(isOpen, async open => {
    const current = ++revision
    if (!open) {
      const previous = entry
      entry = null
      await withdraw(previous)
      return
    }
    if (transition) await transition.promise
    if (current !== revision || !isOpen()) return
    entry = { id: `${Date.now()}-${++sequence}`, url: window.location.href, close, canClose }
    layers.push(entry)
    window.history.pushState({ ...window.history.state, [stateKey]: entry.id }, '', entry.url)
  }, { flush: 'sync', immediate: true })
  onBeforeUnmount(() => { revision += 1; stop(); void withdraw(entry); entry = null })
}
