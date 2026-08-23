import test from 'node:test'
import assert from 'node:assert/strict'

import {
  INTERVIEW_MASTER_WINDOW_NAME,
  openBrowserInterviewMaster
} from './browser-interview-launcher.js'

test('browser interview launcher opens a named window with current model', () => {
  const calls = []
  let focused = false
  const result = openBrowserInterviewMaster({
    answerModelProfileId: 'model-1',
    origin: 'https://example.com',
    openWindow(url, name, features) {
      calls.push({ url, name, features })
      return { focus() { focused = true } }
    }
  })

  assert.equal(result, true)
  assert.equal(calls[0].name, INTERVIEW_MASTER_WINDOW_NAME)
  assert.equal(new URL(calls[0].url).searchParams.get('answer_model_profile_id'), 'model-1')
  assert.equal(focused, true)
})

test('browser interview launcher falls back to a new tab when popup is blocked', () => {
  let fallback = ''
  const result = openBrowserInterviewMaster({
    origin: 'https://example.com',
    openWindow: () => null,
    fallbackOpen: (url) => { fallback = url }
  })

  assert.equal(result, false)
  assert.equal(new URL(fallback).pathname, '/career/interview-master')
})
