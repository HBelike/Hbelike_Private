let workerRegistration

export function registerShareWorker() {
  if (!globalThis.isSecureContext || !navigator.serviceWorker) return Promise.reject(new Error('当前浏览器或地址不支持分享接收，请使用 HTTPS 或手动上传。'))
  workerRegistration ||= navigator.serviceWorker.register('/share-worker.js', { scope: '/', updateViaCache: 'none' }).catch(error => {
    workerRegistration = null
    throw error
  })
  return workerRegistration
}

export async function shareInboxRequest(action, id) {
  await registerShareWorker()
  return new Promise((resolve, reject) => {
    const channel = new MessageChannel()
    const timer = setTimeout(() => finish(new Error('分享接收尚未就绪，请刷新后重试，或手动上传。')), 8000)
    function finish(error, value) {
      clearTimeout(timer)
      channel.port1.close()
      if (error) reject(error)
      else resolve(value)
    }
    channel.port1.onmessage = event => {
      if (event.data?.ok) finish(null, event.data.value)
      else finish(new Error(event.data?.error || '分享资料读取失败，请重新分享。'))
    }
    navigator.serviceWorker.ready.then(registration => {
      registration.active?.postMessage({ action, id }, [channel.port2])
    }).catch(error => finish(error))
  })
}

export function sharedMaterialOptions(material) {
  const text = [material.text, material.url].filter(Boolean).join('\n') || material.title || ''
  const links = [...new Set([material.url, material.text, material.title].filter(Boolean)
    .flatMap(value => String(value).match(/https:\/\/[^\s<>"“”]+/gi) || [])
    .map(value => value.replace(/[.,;!?，。；！？）)》」』]+$/g, '')))]
    .filter(value => { try { return Boolean(new URL(value).hostname) } catch { return false } })
  return { text, links, file: material.files?.[0] || null }
}

export function createSharedMaterialDraft(material, context, previousDraft = {}) {
  if (material.type === 'resume') {
    return {
      ...previousDraft,
      step: 1, resumeMode: /\.(jpe?g|png|webp)$/i.test(material.file.name) ? 'image' : 'upload',
      resumeFile: material.file, resumePreview: null, confirmedExisting: false,
      resumeDisplayName: material.file.name.replace(/\.[^.]+$/, '') || '我的基准简历'
    }
  }
  return {
    ...previousDraft,
    step: 2, selectedCandidate: previousDraft.selectedCandidate ?? context?.candidate_profile ?? null,
    selectedCandidateId: previousDraft.selectedCandidateId ?? context?.candidate_profile?.id ?? '',
    confirmedExisting: previousDraft.confirmedExisting ?? Boolean(context?.candidate_profile), selectedTarget: null,
    companyName: '', roleName: '', jobText: material.mode === 'text' ? material.text : '',
    jobFile: material.mode === 'file' ? material.file : null,
    jobImportMode: material.mode, jobUrlText: material.mode === 'url' ? material.url : '',
    jobSourceKind: 'text', jobSourceLabel: '系统分享', jobSourceUrl: '', jobFetchedAt: '',
    jobImportNotices: [], jobImportMessage: '已接收分享资料。请解析或补充信息，校对后再确认。'
  }
}

export function shareReturnLocation(location) {
  const parameters = new URLSearchParams(location.search)
  const id = parameters.get('share')
  const error = parameters.get('share-error')
  if (location.pathname === '/career/import') return `${location.pathname}${location.search}`
  if (['/login', '/register', '/forgot-password'].includes(location.pathname) && (id || error)) return `/career/import?${parameters}`
  return ''
}
