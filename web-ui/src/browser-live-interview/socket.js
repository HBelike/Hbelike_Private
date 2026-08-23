import { pcm16ToBase64 } from './pcm-framer.js'

const MAX_BUFFERED_BYTES = 1_000_000

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
    WebSocketImpl = window.WebSocket
  }) {
    this.sessionId = sessionId
    this.onEvent = onEvent
    this.onStateChange = onStateChange
    this.location = location
    this.WebSocketImpl = WebSocketImpl
    this.socket = null
    this.ready = false
    this.heartbeat = null
  }

  connect() {
    this.close()
    this.ready = false
    this.onStateChange?.('connecting')
    const socket = new this.WebSocketImpl(buildLiveInterviewWebSocketUrl(this.sessionId, this.location))
    this.socket = socket
    socket.onopen = () => {
      this.onStateChange?.('connected')
      this._startHeartbeat()
    }
    socket.onmessage = (message) => {
      let event
      try {
        event = JSON.parse(message.data)
      } catch {
        this.onEvent?.({ type: 'error', code: 'invalid_server_event', message: '服务端返回了无法识别的消息' })
        return
      }
      if (event.type === 'session.ready') {
        this.ready = true
        this.onStateChange?.('ready')
      }
      this.onEvent?.(event)
    }
    socket.onerror = () => this.onStateChange?.('error')
    socket.onclose = () => {
      this.ready = false
      this._stopHeartbeat()
      this.onStateChange?.('disconnected')
    }
    return socket
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
      this.onStateChange?.('backpressure')
      return false
    }
    return this.send({
      type: 'audio.append',
      channel,
      sequence,
      pcm_base64: pcm16ToBase64(pcm)
    })
  }

  requestAnswer(mode = 'manual', question = null) {
    return this.send({ type: 'answer.request', mode, ...(question ? { question } : {}) })
  }

  commit(channel) {
    return this.send({ type: 'audio.commit', channel })
  }

  end() {
    this.send({ type: 'session.end' })
    this.close()
  }

  close() {
    this.ready = false
    this._stopHeartbeat()
    const socket = this.socket
    this.socket = null
    if (socket && socket.readyState < 2) socket.close()
  }

  _startHeartbeat() {
    this._stopHeartbeat()
    this.heartbeat = globalThis.setInterval(() => this.send({ type: 'ping' }), 20_000)
  }

  _stopHeartbeat() {
    if (this.heartbeat) globalThis.clearInterval(this.heartbeat)
    this.heartbeat = null
  }
}

export { MAX_BUFFERED_BYTES }
