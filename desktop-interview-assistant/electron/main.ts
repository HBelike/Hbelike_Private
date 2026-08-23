import { app, BrowserWindow, desktopCapturer, ipcMain, session } from 'electron'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import WebSocket from 'ws'

const here = path.dirname(fileURLToPath(import.meta.url))
let mainWindow: BrowserWindow | null = null
let currentSocket: WebSocket | null = null
const hasSingleInstanceLock = app.requestSingleInstanceLock()

function requireWindows(): void {
  if (process.platform !== 'win32') {
    throw new Error('实时面试助手仅支持 Windows 10/11')
  }
}

function normalizeApiBase(value: string): URL {
  const url = new URL(value.trim())
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error('服务地址必须使用 HTTP 或 HTTPS')
  return url
}

async function createWindow(): Promise<void> {
  requireWindows()
  mainWindow = new BrowserWindow({
    width: 1380,
    height: 900,
    minWidth: 1080,
    minHeight: 700,
    autoHideMenuBar: true,
    backgroundColor: '#0b1630',
    title: '实时面试助手',
    webPreferences: {
      preload: path.join(here, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })
  mainWindow.webContents.on('did-fail-load', (_event, code, description, url) => {
    console.error(`renderer-load-failed code=${code} description=${description} url=${url}`)
  })
  mainWindow.webContents.on('render-process-gone', (_event, details) => {
    console.error(`renderer-process-gone reason=${details.reason}`)
  })
  mainWindow.webContents.on('did-finish-load', () => {
    console.log(`renderer-ready url=${mainWindow?.webContents.getURL()}`)
  })

  session.defaultSession.setPermissionCheckHandler((_contents, permission) => permission === 'media')
  session.defaultSession.setPermissionRequestHandler((_contents, permission, callback) => {
    callback(permission === 'media')
  })
  session.defaultSession.setDisplayMediaRequestHandler(async (_request, callback) => {
    const sources = await desktopCapturer.getSources({ types: ['screen'] })
    const primary = sources[0]
    if (!primary) return callback({})
    callback({ video: primary, audio: 'loopback' })
  })

  const devServer = process.env.VITE_DEV_SERVER_URL
  if (devServer) await mainWindow.loadURL(devServer)
  else await mainWindow.loadFile(path.join(here, '../dist-renderer/index.html'))
}

ipcMain.handle('auth:open', async (_event, apiBaseUrl: string) => {
  const apiBase = normalizeApiBase(apiBaseUrl)
  const loginWindow = new BrowserWindow({
    width: 1080,
    height: 760,
    parent: mainWindow ?? undefined,
    title: '登录求职助手',
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  })
  await loginWindow.loadURL(apiBase.toString())
})

ipcMain.handle(
  'api:request',
  async (_event, apiBaseUrl: string, requestPath: string, init?: { method?: string; body?: unknown }) => {
    const base = normalizeApiBase(apiBaseUrl)
    if (!requestPath.startsWith('/api/')) throw new Error('只允许访问求职助手 API')
    const response = await session.defaultSession.fetch(new URL(requestPath, base).toString(), {
      method: init?.method ?? 'GET',
      credentials: 'include',
      headers: init?.body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: init?.body === undefined ? undefined : JSON.stringify(init.body),
    })
    const text = await response.text()
    const payload = text ? JSON.parse(text) : {}
    if (!response.ok) throw new Error(payload.detail ?? `请求失败（${response.status}）`)
    return payload
  },
)

ipcMain.handle('socket:open', async (event, apiBaseUrl: string, sessionId: string) => {
  const base = normalizeApiBase(apiBaseUrl)
  const wsProtocol = base.protocol === 'https:' ? 'wss:' : 'ws:'
  const socketUrl = `${wsProtocol}//${base.host}/api/career/live-interviews/${encodeURIComponent(sessionId)}/stream`
  const cookies = await session.defaultSession.cookies.get({ url: base.toString() })
  const cookieHeader = cookies.map((item) => `${item.name}=${item.value}`).join('; ')
  currentSocket?.close()
  currentSocket = new WebSocket(socketUrl, { headers: cookieHeader ? { Cookie: cookieHeader } : undefined })
  await new Promise<void>((resolve, reject) => {
    const handleOpen = () => resolve()
    const handleError = (error: Error) => reject(error)
    currentSocket?.once('open', handleOpen)
    currentSocket?.once('error', handleError)
  })
  const sender = event.sender
  currentSocket.on('message', (data) => {
    try {
      sender.send('socket:event', JSON.parse(data.toString()))
    } catch {
      sender.send('socket:event', { type: 'error', code: 'invalid_server_event', message: '服务端事件格式无效' })
    }
  })
  currentSocket.on('close', (code, reason) => {
    sender.send('socket:closed', { code, reason: reason.toString() })
  })
})

ipcMain.on('socket:send', (_event, payload: unknown) => {
  if (currentSocket?.readyState !== WebSocket.OPEN) throw new Error('实时连接尚未建立')
  currentSocket.send(JSON.stringify(payload))
})

ipcMain.on('socket:close', () => {
  currentSocket?.close()
  currentSocket = null
})

if (!hasSingleInstanceLock) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (!mainWindow) return
    if (mainWindow.isMinimized()) mainWindow.restore()
    mainWindow.show()
    mainWindow.focus()
  })

  app.whenReady().then(createWindow).catch((error) => {
    console.error(error)
    app.quit()
  })
}

app.on('window-all-closed', () => {
  currentSocket?.close()
  app.quit()
})
