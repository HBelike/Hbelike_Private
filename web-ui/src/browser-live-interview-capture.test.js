import test from 'node:test'
import assert from 'node:assert/strict'

import {
  BrowserAudioCapture,
  detectBrowserInterviewSupport
} from './browser-live-interview/capture.js'

function track(kind) {
  return {
    kind,
    stopped: 0,
    addEventListener() {},
    removeEventListener() {},
    stop() { this.stopped += 1 }
  }
}

test('browser audio capture only accepts desktop Chrome capabilities', () => {
  const mediaDevices = { getDisplayMedia() {}, getUserMedia() {} }
  assert.equal(detectBrowserInterviewSupport({
    userAgent: 'Mozilla/5.0 Firefox/140', mediaDevices, AudioContextImpl: class {}, WebSocketImpl: class {}
  }).supported, false)
  assert.equal(detectBrowserInterviewSupport({
    userAgent: 'Mozilla/5.0 Windows NT 10.0 Chrome/140.0 Safari/537.36',
    mediaDevices,
    AudioContextImpl: class {},
    WebSocketImpl: class {}
  }).supported, true)
})

test('browser capability detection does not invoke the AudioContext prototype getter', () => {
  class BrowserAudioContext {}
  Object.defineProperty(BrowserAudioContext.prototype, 'audioWorklet', {
    get() {
      throw new TypeError('Illegal invocation')
    }
  })
  const mediaDevices = {
    getDisplayMedia() {},
    getUserMedia() {}
  }

  assert.deepEqual(detectBrowserInterviewSupport({
    userAgent: 'Mozilla/5.0 Chrome/140.0.0.0 Safari/537.36',
    mediaDevices,
    AudioContextImpl: BrowserAudioContext,
    WebSocketImpl: class {}
  }), { supported: true, missing: [] })
})

test('browser audio capture stops an invalid shared stream without audio', async () => {
  const video = track('video')
  let requestedOptions = null
  const mediaDevices = {
    async getDisplayMedia(options) {
      requestedOptions = options
      return {
        getAudioTracks: () => [],
        getVideoTracks: () => [video],
        getTracks: () => [video]
      }
    }
  }
  const capture = new BrowserAudioCapture({ mediaDevices, AudioContextImpl: class {} })

  await assert.rejects(
    capture.start({ candidateEnabled: false, onFrame() {}, onEnded() {} }),
    /共享电脑声音/
  )
  assert.equal(requestedOptions.systemAudio, 'include')
  assert.equal(requestedOptions.video.displaySurface, 'monitor')
  assert.equal(requestedOptions.monitorTypeSurfaces, 'include')
  assert.equal(requestedOptions.surfaceSwitching, 'include')
  assert.equal(video.stopped, 1)
})

test('browser audio capture degrades when microphone permission fails', async () => {
  const audio = track('audio')
  const video = track('video')
  const mediaDevices = {
    async getDisplayMedia() {
      return {
        getAudioTracks: () => [audio],
        getVideoTracks: () => [video],
        getTracks: () => [audio, video]
      }
    },
    async getUserMedia() {
      throw new Error('denied')
    }
  }
  const capture = new BrowserAudioCapture({ mediaDevices, AudioContextImpl: class {} })
  capture._connectTrack = async () => {}

  const result = await capture.start({ candidateEnabled: true, onFrame() {}, onEnded() {} })

  assert.equal(result.candidateEnabled, false)
  assert.match(result.warning, /麦克风/)
  assert.equal(video.stopped, 1)
  await capture.stop()
  assert.equal(audio.stopped, 1)
})
