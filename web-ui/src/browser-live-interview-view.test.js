import test from 'node:test'
import assert from 'node:assert/strict'

import {
  advanceSetupProgress,
  estimateAsrCost,
  formatDuration,
  isNearScrollEnd,
  isInterviewMasterPath,
  pickInitialAnswerModel
} from './browser-live-interview/view.js'

test('transcript list only follows when the reader stays near the latest message', () => {
  assert.equal(isNearScrollEnd({ scrollHeight: 1000, scrollTop: 650, clientHeight: 320 }), true)
  assert.equal(isNearScrollEnd({ scrollHeight: 1000, scrollTop: 300, clientHeight: 320 }), false)
})

test('setup progress advances smoothly without claiming completion', () => {
  assert.equal(advanceSetupProgress(24), 30)
  assert.equal(advanceSetupProgress(91), 92)
  assert.equal(advanceSetupProgress(92), 92)
})

test('interview master view recognizes its single-task route', () => {
  assert.equal(isInterviewMasterPath('/career/interview-master'), true)
  assert.equal(isInterviewMasterPath('/career'), false)
})

test('interview master view formats duration and Qwen estimate', () => {
  assert.equal(formatDuration(125), '02:05')
  assert.equal(estimateAsrCost(3600, 1), 1.188)
  assert.equal(estimateAsrCost(3600, 2), 2.376)
})

test('interview master view inherits a ready answer model only', () => {
  const models = [
    { id: 'blocked', readiness: 'blocked' },
    { id: 'ready-1', readiness: 'ready' }
  ]
  assert.equal(pickInitialAnswerModel(models, 'blocked'), 'ready-1')
  assert.equal(pickInitialAnswerModel(models, 'ready-1'), 'ready-1')
})
