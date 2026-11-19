export function shouldSubmitComposer(event, mobileBrowser = false) {
  return event.key === 'Enter' && !mobileBrowser && !event.isComposing && event.keyCode !== 229
    && !event.shiftKey && !event.ctrlKey && !event.altKey && !event.metaKey && !event.defaultPrevented
}

export function shouldNavigateComposerHistory(event, mobileBrowser = false, browsing = false) {
  if (mobileBrowser || event.isComposing || event.keyCode === 229 || event.defaultPrevented
    || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey
    || !['ArrowUp', 'ArrowDown'].includes(event.key)) return false

  const { value, selectionStart, selectionEnd } = event.currentTarget
  if (selectionStart !== selectionEnd) return false
  if (browsing) return true
  // 未浏览历史时，首行向上才回填历史，保留多行文本中的光标移动。
  return event.key === 'ArrowUp' && !value.slice(0, selectionStart).includes('\n')
}

export function createComposerHistory() {
  let entries = []
  let index = null
  let draft = ''

  function reset() {
    entries = []
    index = null
    draft = ''
  }

  return {
    get browsing() { return index !== null },
    get text() { return index === null ? null : (index === entries.length ? draft : entries[index]) },
    reset,
    move(key, text, history) {
      if (index === null) {
        if (key !== 'ArrowUp') return null
        entries = history.filter(item => typeof item === 'string' && item.trim())
        if (!entries.length) return null
        draft = text
        index = entries.length
      }
      index = Math.max(0, Math.min(entries.length, index + (key === 'ArrowUp' ? -1 : 1)))
      const atDraft = index === entries.length
      return { text: atDraft ? draft : entries[index], atDraft }
    }
  }
}
