import { pcm16ToBase64 } from './pcm-framer.js'

const MAX_BUFFERED_BYTES = 256_000
const DEFAULT_READY_TIMEOUT_MS = 15_000
const HEARTBEAT_INTERVAL_MS = 20_000
const HEARTBEAT_TIMEOUT_MS = 45_000

export class LiveInterviewConnectionError extends Error {
  constructor(message, { code = 0, reason = '', wasReady = false } = {}) {
    super(message)
    this.name = 'LiveInterviewConnectionError'
    this.code = code
    this.reason = reason
    this.wasReady = wasReady
  }
}

export function buildLiveInterviewWebSocketUrl(sessionId, location = window.location) {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${location.host}/api/career/live-interviews/${encodeURIComponent(sessionId)}/stream`
}

export class LiveInterviewSocket {
  constructor({
    sessionId,
    onEvent,
    onStateChange,
    location = window.location,
    WebSocketImpl = window.WebSocket,
    timers = globalThis,
    now = () => globalThis.performance?.now?.() ?? Date.now()
  }) {
    this.sessionId = sessionId
    this.onEvent = onEvent
    this.onStateChange = onStateChange
    this.location = location
    this.WebSocketImpl = WebSocketImpl
    this.timers = timers
    this.now = now
    this.socket = null
    this.ready = false
    this.heartbeat = null
    this.pendingReady = null
    this.pendingEnd = null
    this.backpressured = false
    this.lastServerError = ''
    this.awaitingPongAt = null
    this.lastHeartbeatTick = null
  }

  connect({ readyTimeoutMs = DEFAULT_READY_TIMEOUT_MS } = {}) {
    this.close()
    this.ready = false
    this.lastServerError = ''
    this.onStateChange?.('connecting')
    const socket = new this.WebSocketImpl(buildLiveInterviewWebSocketUrl(this.sessionId, this.location))
    this.socket = socket
    const readyPromise = new Promise((resolve, reject) => {
      const timeout = this.timers.setTimeout(() => {
        if (this.socket !== socket || this.ready) return
        const error = new LiveInterviewConnectionError(
          '实时服务连接超时，尚未完成语音识别准备。',
          { code: 4000, reason: 'session_ready_timeout', wasReady: false }
        )
        this._rejectPendingReady(error)
        this.onStateChange?.('timeout', {
          code: error.code,
          reason: error.reason,
          wasReady: false
        })
        if (socket.readyState < 2) socket.close(error.code, error.reason)
      }, Math.max(1, readyTimeoutMs))
      this.pendingReady = { socket, resolve, reject, timeout }
    })
    socket.onopen = () => {
      if (this.socket !== socket) return
      this.onStateChange?.('connected')
      this._startHeartbeat()
    }
    socket.onmessage = (message) => {
      if (this.socket !== socket) return
      let event
      try {
        event = JSON.parse(message.data)
      } catch {
        this.onEvent?.({ type: 'error', code: 'invalid_server_event', message: '服务端返回了无法识别的消息' })
        return
      }
      if (event.type === 'session.ready') {
        this.ready = true
        this._resolvePendingReady(event)
        this.onStateChange?.('ready')
      }
      if (event.type === 'error' && event.message) this.lastServerError = String(event.message)
      if (event.type === 'pong') this.awaitingPongAt = null
      this.onEvent?.(event)
      if (event.type === 'session.ended') this.pendingEnd?.finish(true)
      if (event.type === 'error' && event.fatal === true && this.socket === socket) {
        socket.close(4010, '语音连接已中断')
      }
    }
    socket.onerror = () => {
      if (this.socket !== socket) return
      this.onStateChange?.('error', { wasReady: this.ready })
    }
    socket.onclose = (event = {}) => {
      if (this.socket !== socket) return
      const wasReady = this.ready
      const code = Number(event.code) || 0
      const reason = String(event.reason || this.lastServerError || '').trim()
      this.socket = null
      this.ready = false
      this._stopHeartbeat()
      // 传输正常关闭也可能早于尾句落库，只有 session.ended 确认收尾。
      this.pendingEnd?.finish(false)
      const detail = { code, reason, wasReady }
      if (!wasReady) {
        const suffix = reason ? `：${reason}` : code ? `（关闭代码 ${code}）` : ''
        this._rejectPendingReady(new LiveInterviewConnectionError(
          `实时连接在准备完成前中断${suffix}`,
          detail
        ))
      }
      this.onStateChange?.('disconnected', detail)
    }
    return readyPromise
  }

  send(event) {
    const socket = this.socket
    if (!socket || socket.readyState !== this.WebSocketImpl.OPEN) return false
    socket.send(JSON.stringify(event))
    return true
  }

  sendAudio(channel, sequence, pcm) {
    const socket = this.socket
    if (!this.ready || !socket || socket.readyState !== this.WebSocketImpl.OPEN) return false
    if (socket.bufferedAmount > MAX_BUFFERED_BYTES) {
      if (!this.backpressured) this.onStateChange?.('backpressure')
      this.backpressured = true
      return false
    }
    const sent = this.send({
      type: 'audio.append',
      channel,
      sequence,
      pcm_base64: pcm16ToBase64(pcm)
    })
    if (sent && this.backpressured) {
      this.backpressured = false
      this.onStateChange?.('ready')
    }
    return sent
  }

  requestAnswer(mode = 'manual', question = null) {
    return this.send({ type: 'answer.request', mode, ...(question ? { question } : {}) })
  }

  commit(channel) {
    return this.send({ type: 'audio.commit', channel })
  }

  end({ timeoutMs = 18_000, pause = false } = {}) {
    if (this.pendingEnd) return this.pendingEnd.promise
    if (!this.ready) {
      this.close()
      return Promise.resolve(false)
    }
    let settle
    const promise = new Promise(resolve => { settle = resolve })
    const timer = this.timers.setTimeout(() => {
      finish(false)
      this.close()
    }, timeoutMs)
    const finish = (complete) => {
      if (this.pendingEnd?.promise !== promise) return
      this.timers.clearTimeout(timer)
      this.pendingEnd = null
      settle(complete)
    }
    this.pendingEnd = { promise, finish }
    if (!this.send({ type: pause ? 'session.pause' : 'session.end' })) finish(false)
    return promise
  }

  close() {
    this.ready = false
    this.backpressured = false
    this.pendingEnd?.finish(false)
    this._stopHeartbeat()
    const socket = this.socket
    this.socket = null
    if (this.pendingReady?.socket === socket) {
      this._rejectPendingReady(new LiveInterviewConnectionError('实时连接已取消'))
    }
    if (socket && socket.readyState < 2) socket.close()
  }

  _resolvePendingReady(event) {
    const pending = this.pendingReady
    if (!pending || pending.socket !== this.socket) return
    this.timers.clearTimeout(pending.timeout)
    this.pendingReady = null
    pending.resolve(event)
  }

  _rejectPendingReady(error) {
    const pending = this.pendingReady
    if (!pending) return
    this.timers.clearTimeout(pending.timeout)
    this.pendingReady = null
    pending.reject(error)
  }

  _startHeartbeat() {
    this._stopHeartbeat()
    this.lastHeartbeatTick = this.now()
    this.heartbeat = this.timers.setInterval(() => {
      const now = this.now()
      // 后台标签页恢复时，旧计时器可能迟到；先重新探测，给本次 Pong 完整响应窗口。
      if (this.lastHeartbeatTick !== null && now - this.lastHeartbeatTick > HEARTBEAT_INTERVAL_MS * 2) {
        this.awaitingPongAt = null
      }
      this.lastHeartbeatTick = now
      if (this.awaitingPongAt !== null && now - this.awaitingPongAt >= HEARTBEAT_TIMEOUT_MS) {
        this.socket?.close(4001, 'heartbeat_timeout')
        return
      }
      if (this.awaitingPongAt === null && this.send({ type: 'ping' })) this.awaitingPongAt = now
    }, HEARTBEAT_INTERVAL_MS)
  }

  _stopHeartbeat() {
    if (this.heartbeat) this.timers.clearInterval(this.heartbeat)
    this.heartbeat = null
    this.awaitingPongAt = null
    this.lastHeartbeatTick = null
  }
}

export { DEFAULT_READY_TIMEOUT_MS, MAX_BUFFERED_BYTES, HEARTBEAT_INTERVAL_MS, HEARTBEAT_TIMEOUT_MS }
