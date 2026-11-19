/* 仅接收用户主动分享的材料，不缓存页面、API 或第三方请求。 */
const INBOX_DATABASE = 'career-share-inbox'
const INBOX_STORE = 'materials'
const MAX_SHARE_AGE = 24 * 60 * 60 * 1000

function inboxOperation(mode, operation) {
  return new Promise((resolve, reject) => {
    const opening = indexedDB.open(INBOX_DATABASE, 1)
    opening.onupgradeneeded = () => opening.result.createObjectStore(INBOX_STORE, { keyPath: 'id' })
    opening.onerror = () => reject(opening.error)
    opening.onsuccess = () => {
      const database = opening.result
      const transaction = database.transaction(INBOX_STORE, mode)
      let value
      const request = operation(transaction.objectStore(INBOX_STORE))
      request.onsuccess = () => { value = request.result }
      transaction.oncomplete = () => { database.close(); resolve(value) }
      transaction.onabort = transaction.onerror = () => { database.close(); reject(transaction.error) }
    }
  })
}

async function readMaterial(id) {
  const material = await inboxOperation('readonly', store => store.get(id))
  if (material && Date.now() - material.createdAt >= MAX_SHARE_AGE) {
    await inboxOperation('readwrite', store => store.delete(id))
    return null
  }
  return material || null
}

async function receiveMaterial(request) {
  try {
    // 空 multipart 在部分浏览器中会解析失败，仍按“无有效材料”给出可操作提示。
    const form = await request.formData().catch(() => new FormData())
    const fields = Object.fromEntries(['title', 'text', 'url'].map(name => [name, String(form.get(name) || '').trim()]))
    const files = form.getAll('files').filter(file => file instanceof File && file.name)
    let error = ''
    if (files.length > 1) error = '本次只能导入一个文件，请逐份分享；多张截图可先合成长图。'
    else if (files.some(file => file.size > 10 * 1024 * 1024)) error = '文件超过 10MB，请压缩后重新分享，或手动上传。'
    else if (files.some(file => !/\.(pdf|docx?|jpe?g|png|webp)$/i.test(file.name))) error = '仅支持 PDF、Word、JPG、PNG 或 WebP，请更换文件。'
    else if (fields.text.length > 30_000 || fields.url.length > 2_000 || fields.title.length > 500) error = '分享内容超过导入长度，请选择具体岗位或手动粘贴所需正文。'
    else if (!files.length && !fields.title && !fields.text && !fields.url) error = '没有收到可导入的内容，请重新分享或手动选择资料。'
    const id = crypto.randomUUID()
    // 删除过期的未处理材料；文件只临时留在当前浏览器。
    await inboxOperation('readwrite', store => {
      const cursor = store.openCursor()
      cursor.onsuccess = () => {
        const entry = cursor.result
        if (!entry) return
        if (Date.now() - entry.value.createdAt >= MAX_SHARE_AGE) entry.delete()
        entry.continue()
      }
      return store.put({ id, createdAt: Date.now(), ...(error ? { error } : { ...fields, files }) })
    })
    return Response.redirect(new URL(`/career/import?share=${id}`, self.location.origin), 303)
  } catch {
    return Response.redirect(new URL('/career/import?share-error=storage', self.location.origin), 303)
  }
}

self.addEventListener('install', event => event.waitUntil(self.skipWaiting()))
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()))
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url)
  if (event.request.method === 'POST' && url.origin === self.location.origin && url.pathname === '/share-target') {
    event.respondWith(receiveMaterial(event.request))
  }
})
self.addEventListener('message', event => {
  const { action, id } = event.data || {}
  const port = event.ports[0]
  if (!port || !['share:read', 'share:delete', 'share:clear'].includes(action)) return
  event.waitUntil((async () => {
    try {
      const value = action === 'share:read' ? await readMaterial(id)
        : await inboxOperation('readwrite', store => action === 'share:clear' ? store.clear() : store.delete(id))
      port.postMessage({ ok: true, value })
    } catch {
      port.postMessage({ ok: false, error: '暂时无法读取分享资料，请重新分享或手动上传。' })
    }
  })())
})
