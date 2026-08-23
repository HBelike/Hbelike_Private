import test from 'node:test'
import assert from 'node:assert/strict'

import {
  floatToPcm16,
  pcm16ToBase64,
  resampleToPcm16
} from './browser-live-interview/pcm-framer.js'
import {
  createInterviewState,
  reduceInterviewEvent
} from './browser-live-interview/session-state.js'
import {
  LiveInterviewSocket,
  buildLiveInterviewWebSocketUrl
} from './browser-live-interview/socket.js'

test('browser live interview converts and resamples PCM safely', () => {
  const pcm = floatToPcm16(new Float32Array([-2, -1, 0, 1, 2]))
  assert.deepEqual([...pcm], [-32768, -32768, 0, 32767, 32767])
  assert.equal(resampleToPcm16(new Float32Array(480), 48_000, 24_000).length, 240)
  assert.equal(pcm16ToBase64(new Int16Array([1, -1])), 'AQD//w==')
})

test('browser live interview ignores late and duplicate answer deltas', () => {
  let state = createInterviewState()
  state = reduceInterviewEvent(state, {
    type: 'question.detected',
    question_version: 3,
    question: '如何设计限流器？',
    intent: 'system_design'
  })
  state = reduceInterviewEvent(state, {
    type: 'answer.started',
    question_version: 3,
    attempt: 1
  })
  state = reduceInterviewEvent(state, {
    type: 'answer.delta',
    question_version: 3,
    attempt: 1,
    delta_index: 1,
    delta: '直接结论'
  })
  state = reduceInterviewEvent(state, {
    type: 'answer.delta',
    question_version: 3,
    attempt: 1,
    delta_index: 1,
    delta: '直接结论'
  })
  state = reduceInterviewEvent(state, {
    type: 'answer.delta',
    question_version: 2,
    attempt: 1,
    delta_index: 2,
    delta: '迟到内容'
  })

  assert.equal(state.answerText, '直接结论')
})

test('browser live interview derives same-origin WebSocket URL', () => {
  assert.equal(
    buildLiveInterviewWebSocketUrl('session-1', { protocol: 'https:', host: 'example.com' }),
    'wss://example.com/api/career/live-interviews/session-1/stream'
  )
})

test('browser live interview socket waits for ready and applies backpressure', () => {
  class FakeWebSocket {
    static OPEN = 1

    constructor(url) {
      this.url = url
      this.readyState = 1
      this.bufferedAmount = 0
      this.sent = []
    }

    send(payload) {
      this.sent.push(JSON.parse(payload))
    }

    close() {}
  }

  const states = []
  const socket = new LiveInterviewSocket({
    sessionId: 'session-2',
    location: { protocol: 'http:', host: 'localhost:5173' },
    WebSocketImpl: FakeWebSocket,
    onEvent: () => {},
    onStateChange: (value) => states.push(value)
  })
  socket.connect()
  socket.socket.onopen()
  assert.equal(socket.sendAudio('interviewer', 0, new Int16Array([1])), false)
  socket.socket.onmessage({ data: JSON.stringify({ type: 'session.ready', active_channels: ['interviewer'] }) })
  assert.equal(socket.sendAudio('interviewer', 0, new Int16Array([1])), true)
  socket.socket.bufferedAmount = 1_000_001
  assert.equal(socket.sendAudio('interviewer', 1, new Int16Array([2])), false)
  assert.ok(states.includes('ready'))
  socket.close()
})
