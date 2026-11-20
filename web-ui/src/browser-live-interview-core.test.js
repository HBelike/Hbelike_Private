import test from 'node:test'
import assert from 'node:assert/strict'

import {
  floatToPcm16,
  PcmFrameAccumulator,
  StreamingPcmResampler,
  pcm16ToBase64,
  resampleToPcm16
} from './browser-live-interview/pcm-framer.js'
import {
  createInterviewState,
  reduceInterviewEvent
} from './browser-live-interview/session-state.js'
import {
  MAX_BUFFERED_BYTES,
  LiveInterviewSocket,
  buildLiveInterviewWebSocketUrl
} from './browser-live-interview/socket.js'
import { LiveAudioOutbox } from './browser-live-interview/audio-outbox.js'

test('browser live interview converts and resamples PCM safely', () => {
  const pcm = floatToPcm16(new Float32Array([-2, -1, 0, 1, 2]))
  assert.deepEqual([...pcm], [-32768, -32768, 0, 32767, 32767])
  assert.equal(resampleToPcm16(new Float32Array(480), 48_000, 24_000).length, 240)
  assert.equal(pcm16ToBase64(new Int16Array([1, -1])), 'AQD//w==')
})

test('browser live interview batches worklet chunks into 100ms frames', () => {
  const accumulator = new PcmFrameAccumulator(4)
  assert.deepEqual(accumulator.append(new Int16Array([1, 2, 3])), [])
  const frames = accumulator.append(new Int16Array([4, 5, 6, 7, 8, 9]))
  assert.deepEqual(frames.map((frame) => [...frame]), [[1, 2, 3, 4], [5, 6, 7, 8]])
  assert.deepEqual([...accumulator.pending], [9])
  assert.deepEqual([...accumulator.flush()], [9])
  assert.equal(accumulator.flush().length, 0)
})

test('44.1kHz worklet blocks keep the same samples and duration as uninterrupted input', () => {
  const source = Float32Array.from({ length: 441_000 }, (_, i) => Math.sin(i / 40))
  const expected = new StreamingPcmResampler(44_100).append(source)
  const resampler = new StreamingPcmResampler(44_100)
  const received = new Int16Array(expected.length)
  let written = 0
  for (let start = 0; start < source.length; start += 128) {
    const block = resampler.append(source.subarray(start, start + 128))
    received.set(block, written)
    written += block.length
  }
  assert.equal(written, 240_000)
  assert.deepEqual(received, expected)
})

test('unsent audio survives a short outage and each track reports overflow beyond five seconds', () => {
  let connected = false
  const received = []
  const gaps = []
  const buffer = new LiveAudioOutbox({
    send: (channel, sequence) => {
      if (connected) received.push([channel, sequence])
      return connected
    },
    onGap: (channel, seconds) => gaps.push([channel, seconds]),
    timers: { setInterval: () => 1, clearInterval() {} }
  })
  const pcm = new Int16Array(2400)
  for (let sequence = 0; sequence < 51; sequence++) {
    buffer.append({ channel: 'interviewer', sequence, pcm })
    buffer.append({ channel: 'interviewer', sequence, pcm })
    if (sequence < 2) buffer.append({ channel: 'candidate', sequence, pcm })
  }
  assert.deepEqual(gaps, [['interviewer', 0.1]])
  connected = true
  for (let step = 0; step < 50; step++) buffer.flush()
  assert.deepEqual(received.filter(item => item[0] === 'interviewer').map(item => item[1]), Array.from({ length: 50 }, (_, i) => i + 1))
  assert.deepEqual(received.filter(item => item[0] === 'candidate'), [['candidate', 0], ['candidate', 1]])
  assert.equal(buffer.pending, false)
  buffer.close()
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

function createFakeWebSocketClass() {
  return class FakeWebSocket {
    static OPEN = 1
    static instances = []

    constructor(url) {
      this.url = url
      this.readyState = 1
      this.bufferedAmount = 0
      this.sent = []
      this.constructor.instances.push(this)
    }

    send(payload) {
      this.sent.push(JSON.parse(payload))
    }

    close(code = 1000, reason = '') {
      this.readyState = 3
      this.onclose?.({ code, reason })
    }
  }
}

test('browser live interview socket waits for ready and applies backpressure', async () => {
  const FakeWebSocket = createFakeWebSocketClass()

  const states = []
  const socket = new LiveInterviewSocket({
    sessionId: 'session-2',
    location: { protocol: 'http:', host: 'localhost:5173' },
    WebSocketImpl: FakeWebSocket,
    onEvent: () => {},
    onStateChange: (value) => states.push(value)
  })
  const ready = socket.connect()
  socket.socket.onopen()
  assert.equal(socket.sendAudio('interviewer', 0, new Int16Array([1])), false)
  socket.socket.onmessage({ data: JSON.stringify({ type: 'session.ready', active_channels: ['interviewer'] }) })
  await ready
  assert.equal(socket.sendAudio('interviewer', 0, new Int16Array([1])), true)
  assert.equal(MAX_BUFFERED_BYTES, 256_000)
  socket.socket.bufferedAmount = MAX_BUFFERED_BYTES + 1
  assert.equal(socket.sendAudio('interviewer', 1, new Int16Array([2])), false)
  socket.socket.bufferedAmount = 0
  assert.equal(socket.sendAudio('interviewer', 1, new Int16Array([2])), true)
  assert.deepEqual(states.slice(-2), ['backpressure', 'ready'])
  socket.close()
})

test('graceful pause accepts a late final before session.ended and reconnects the same session', async () => {
  const FakeWebSocket = createFakeWebSocketClass()
  const events = []
  const socket = new LiveInterviewSocket({
    sessionId: 'one-session', WebSocketImpl: FakeWebSocket, location: { protocol: 'http:', host: 'localhost' },
    onEvent: event => events.push(event.type)
  })
  const open = socket.connect()
  const ws = socket.socket
  const emit = event => ws.onmessage({ data: JSON.stringify(event) })
  emit({ type: 'session.ready' })
  await open
  let finished = false
  const ending = socket.end({ pause: true }).then(result => { finished = result })
  assert.deepEqual(ws.sent.at(-1), { type: 'session.pause' })
  emit({ type: 'transcript.final', text: '尾句' })
  await Promise.resolve()
  assert.equal(finished, false)
  emit({ type: 'session.ended' })
  await ending
  assert.equal(finished, true)
  assert.deepEqual(events, ['session.ready', 'transcript.final', 'session.ended'])
  const reopen = socket.connect()
  assert.equal(socket.socket.url, ws.url)
  socket.socket.onmessage({ data: '{"type":"session.ready"}' })
  await reopen
  socket.close()
})

test('a normal WebSocket close without session.ended leaves the last utterance unconfirmed', async () => {
  const FakeWebSocket = createFakeWebSocketClass()
  const socket = new LiveInterviewSocket({
    sessionId: 'unconfirmed-end', WebSocketImpl: FakeWebSocket, location: { protocol: 'http:', host: 'localhost' }
  })
  const ready = socket.connect()
  socket.socket.onmessage({ data: '{"type":"session.ready"}' })
  await ready
  const ending = socket.end()
  socket.socket.close(1000)
  assert.equal(await ending, false)
})

test('browser live interview socket reports a close before session ready', async () => {
  const FakeWebSocket = createFakeWebSocketClass()
  const states = []
  const socket = new LiveInterviewSocket({
    sessionId: 'session-close-before-ready',
    location: { protocol: 'http:', host: 'localhost:5173' },
    WebSocketImpl: FakeWebSocket,
    onEvent: () => {},
    onStateChange: (value, detail) => states.push({ value, detail })
  })

  const ready = socket.connect({ readyTimeoutMs: 100 })
  socket.socket.onclose({ code: 1011, reason: 'asr unavailable' })

  await assert.rejects(ready, /asr unavailable/)
  assert.deepEqual(states.at(-1), {
    value: 'disconnected',
    detail: { code: 1011, reason: 'asr unavailable', wasReady: false }
  })
})

test('browser live interview socket ignores a stale close after reconnecting', async () => {
  const FakeWebSocket = createFakeWebSocketClass()
  const states = []
  const socket = new LiveInterviewSocket({
    sessionId: 'session-reconnect',
    location: { protocol: 'http:', host: 'localhost:5173' },
    WebSocketImpl: FakeWebSocket,
    onEvent: () => {},
    onStateChange: (value) => states.push(value)
  })

  const firstReady = socket.connect({ readyTimeoutMs: 100 })
  const firstSocket = socket.socket
  const firstRejected = assert.rejects(firstReady, /已取消/)
  const secondReady = socket.connect({ readyTimeoutMs: 100 })
  await firstRejected
  const secondSocket = socket.socket
  firstSocket.onclose?.({ code: 1006, reason: '' })
  secondSocket.onmessage({ data: JSON.stringify({ type: 'session.ready', active_channels: ['interviewer'] }) })
  await secondReady

  assert.equal(socket.ready, true)
  assert.equal(states.filter((value) => value === 'disconnected').length, 0)
  socket.close()
})
