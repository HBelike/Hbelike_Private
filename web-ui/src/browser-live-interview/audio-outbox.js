import { TARGET_SAMPLE_RATE } from './pcm-framer.js'

const MAX_PENDING_SAMPLES = 5 * TARGET_SAMPLE_RATE
const REPLAY_INTERVAL_MS = 50

// 仅保留尚未交给浏览器 WebSocket 的帧。已发送的帧缺少上游确认，不能盲目重播。
export class LiveAudioOutbox {
  constructor({ send, onGap, timers = globalThis }) {
    this.send = send
    this.onGap = onGap
    this.timers = timers
    this.channels = new Map()
    this.timer = null
    this.closed = false
  }

  append({ channel, sequence, pcm }) {
    if (this.closed || !pcm.length) return
    let track = this.channels.get(channel)
    if (!track) {
      track = { queue: [], samples: 0, lastSequence: -1, dropped: 0 }
      this.channels.set(channel, track)
    }
    if (sequence <= track.lastSequence) return
    track.lastSequence = sequence
    if (track.queue.length === 0 && this.send(channel, sequence, pcm)) return
    track.queue.push({ sequence, pcm })
    track.samples += pcm.length
    let dropped = 0
    while (track.samples > MAX_PENDING_SAMPLES) {
      const expired = track.queue.shift()
      track.samples -= expired.pcm.length
      dropped += expired.pcm.length
    }
    if (dropped) {
      track.dropped += dropped
      this.onGap?.(channel, track.dropped / TARGET_SAMPLE_RATE)
    }
    if (this.timer === null) {
      this.timer = this.timers.setInterval(() => this.flush(), REPLAY_INTERVAL_MS)
    }
  }

  flush() {
    // 每轨每个节拍最多一帧，100ms 常规帧按最高双倍速度追赶，避免压满后端。
    for (const [channel, track] of this.channels) {
      const frame = track.queue[0]
      if (frame && this.send(channel, frame.sequence, frame.pcm)) {
        track.queue.shift()
        track.samples -= frame.pcm.length
      }
    }
    if (!this.pending && this.timer !== null) {
      this.timers.clearInterval(this.timer)
      this.timer = null
    }
  }

  get pending() {
    for (const track of this.channels.values()) if (track.queue.length) return true
    return false
  }

  discard(channel) {
    this.channels.delete(channel)
  }

  async drain(timeoutMs = 5_000, channel = null) {
    const pending = () => channel ? Boolean(this.channels.get(channel)?.queue.length) : this.pending
    const deadline = Date.now() + timeoutMs
    while (!this.closed && pending() && Date.now() < deadline) {
      await new Promise(resolve => this.timers.setTimeout(resolve, REPLAY_INTERVAL_MS))
    }
    return !pending()
  }

  close() {
    this.closed = true
    this.channels.clear()
    if (this.timer !== null) this.timers.clearInterval(this.timer)
    this.timer = null
  }
}
