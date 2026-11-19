import { nextTick, onBeforeUnmount, watch } from 'vue'
import { useHistoryLayer } from './use-history-layer.js'

// 只复用弹层焦点行为，保存、取消和等待状态仍由各页面负责。
export function useDialogFocus(isOpen, selector, close, canClose = () => true) {
  useHistoryLayer(isOpen, close, canClose)
  let revision = 0
  let release = () => {}
  const stop = watch(isOpen, async open => {
    const current = ++revision
    release()
    release = () => {}
    if (!open) return
    const previous = document.activeElement
    await nextTick()
    if (current !== revision) return
    const root = document.querySelector(selector)
    if (!root) return
    const viewport = window.visualViewport
    const syncViewport = () => root.style.setProperty('--dialog-visible-height', `${Math.round(viewport?.height || window.innerHeight)}px`)
    syncViewport()
    viewport?.addEventListener('resize', syncViewport)
    const shell = document.querySelector('.shell')
    const previousInert = shell?.inert
    if (shell) shell.inert = true
    const controls = panel => [...panel.querySelectorAll('button,a[href],input,select,textarea,[tabindex="0"]')]
      .filter(el => !el.disabled && !el.closest('[inert]') && el.getClientRects().length)
    root.tabIndex = -1
    ;(controls(root)[0] || root).focus()
    function keydown(event) {
      if (document.querySelector('dialog[open]')) return
      const panel = root.querySelector('[role="alertdialog"]') || root
      if (event.key === 'Escape') {
        if (panel !== root) return
        event.preventDefault()
        event.stopPropagation()
        close()
      } else if (event.key === 'Tab') {
        const items = controls(panel)
        const target = !panel.contains(document.activeElement) ? items[0]
          : event.shiftKey && document.activeElement === items[0] ? items.at(-1)
            : !event.shiftKey && document.activeElement === items.at(-1) ? items[0] : null
        if (target) { event.preventDefault(); target.focus() }
      }
    }
    document.addEventListener('keydown', keydown, true)
    release = () => {
      viewport?.removeEventListener('resize', syncViewport)
      root.style.removeProperty('--dialog-visible-height')
      document.removeEventListener('keydown', keydown, true)
      if (shell) shell.inert = previousInert
      if (previous?.isConnected) previous.focus()
    }
  }, { flush: 'post' })
  onBeforeUnmount(() => { revision += 1; stop(); release() })
}
