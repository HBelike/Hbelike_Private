import assert from 'node:assert/strict'
import test from 'node:test'
import { assessmentBindingKey, updateAssessmentSnapshot } from './career-assessment-snapshot.js'

const context = { binding_id: 'binding-1', candidate_profile: { id: 'cv-1', version: 1 }, target_role: { id: 'jd-1', version: 1 }, assessment: { status: 'ready', algorithm_version: 'v2', items: [{ id: 'old' }] } }

test('排队或异步失败保留当前绑定的成功报告，成功后替换', () => {
  const snapshot = updateAssessmentSnapshot(null, context, 'conversation-1')
  assert.equal(updateAssessmentSnapshot(snapshot, { ...context, assessment: { status: 'queued' } }, 'conversation-1'), snapshot)
  assert.equal(updateAssessmentSnapshot(snapshot, { ...context, assessment: { status: 'failed' } }, 'conversation-1'), snapshot)
  const next = { ...context, assessment: { status: 'ready', items: [{ id: 'new' }] } }
  assert.equal(updateAssessmentSnapshot(snapshot, next, 'conversation-1').assessment, next.assessment)
})

test('会话、绑定或资料版本变化不能复用旧报告，也能识别迟到响应', () => {
  const snapshot = updateAssessmentSnapshot(null, context, 'conversation-1')
  for (const changed of [
    { ...context, binding_id: 'binding-2' },
    { ...context, candidate_profile: { id: 'cv-1', version: 2 } },
    { ...context, target_role: { id: 'jd-2', version: 1 } },
    { ...context, target_role: { id: 'jd-1', version: 2 } }
  ]) {
    assert.notEqual(assessmentBindingKey(changed, 'conversation-1'), snapshot.key)
    assert.equal(updateAssessmentSnapshot(snapshot, { ...changed, assessment: { status: 'queued' } }, 'conversation-1'), null)
  }
  assert.equal(updateAssessmentSnapshot(snapshot, { ...context, assessment: { status: 'failed' } }, 'conversation-2'), null)
})
