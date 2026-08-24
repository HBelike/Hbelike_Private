import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildBossChatUrl,
  classifyOutgoingMessageEvidence,
  classifyFriendAddResponse,
  normalizeGreetingPayload,
  shouldStopGreetingBatch
} from '../boss-greeting.js'

const payload = {
  securityId: 'secure/1',
  jobId: 'job 1',
  bossId: 'boss+1',
  lid: 'lid?1',
  message: '您好，期待进一步沟通。'
}

test('真实发送 payload 只要求岗位标识和文案', () => {
  assert.deepEqual(normalizeGreetingPayload(payload), payload)
  assert.throws(() => normalizeGreetingPayload({ ...payload, message: '' }), /招呼语不能为空/)
  assert.throws(() => normalizeGreetingPayload({ ...payload, bossId: '' }), /招聘者标识/)
})

test('聊天 URL 只使用 BOSS 官方聊天页并编码岗位标识', () => {
  const url = new URL(buildBossChatUrl(payload))
  assert.equal(url.origin, 'https://www.zhipin.com')
  assert.equal(url.pathname, '/web/geek/chat')
  assert.equal(url.searchParams.get('id'), 'boss+1')
  assert.equal(url.searchParams.get('jobId'), 'job 1')
  assert.equal(url.searchParams.get('securityId'), 'secure/1')
  assert.equal(url.searchParams.get('lid'), 'lid?1')
})

test('默认招呼语发送后仍继续发送定制文案', () => {
  assert.deepEqual(classifyFriendAddResponse({ code: 0, zpData: { showGreeting: false } }), { ok: true })
  assert.deepEqual(classifyFriendAddResponse({ code: 0, zpData: { showGreeting: true } }), {
    ok: true,
    defaultGreetingSent: true
  })
})

test('建立沟通结果严格区分限流、验证和正常继续', () => {
  assert.equal(classifyFriendAddResponse({ code: 1, message: '操作过于频繁' }).code, 'rate_limited')
  assert.equal(classifyFriendAddResponse({ code: 1, message: '请完成安全验证' }).code, 'verification_required')
  assert.equal(classifyFriendAddResponse({ code: 1, message: '职位已下线' }).code, 'job_unavailable')
  assert.equal(classifyFriendAddResponse({ code: 1, message: '已经沟通过该招聘者' }).code, 'already_contacted')
  assert.equal(classifyFriendAddResponse({ code: 37 }).code, 'login_required')
})

test('聊天回执优先识别明确送达与明确失败状态', () => {
  assert.equal(classifyOutgoingMessageEvidence({ statusClasses: 'status status-delivery' }), 'sent')
  assert.equal(classifyOutgoingMessageEvidence({ statusText: '已读' }), 'sent')
  assert.equal(classifyOutgoingMessageEvidence({ statusClasses: 'status status-fail' }), 'failed')
  assert.equal(classifyOutgoingMessageEvidence({ statusText: '发送失败，请重试' }), 'failed')
})

test('BOSS 不显示旧送达 class 时以稳定的新本人消息确认提交', () => {
  assert.equal(classifyOutgoingMessageEvidence({
    isNew: true,
    inputCleared: true,
    stableForMs: 1999
  }), 'pending')
  assert.equal(classifyOutgoingMessageEvidence({
    isNew: true,
    inputCleared: true,
    stableForMs: 2000
  }), 'sent')
})

test('历史同文案和未清空输入框不能伪造新发送成功', () => {
  assert.equal(classifyOutgoingMessageEvidence({
    isNew: false,
    inputCleared: true,
    stableForMs: 5000
  }), 'pending')
  assert.equal(classifyOutgoingMessageEvidence({
    isNew: true,
    inputCleared: false,
    stableForMs: 5000
  }), 'pending')
})

test('安全状态与未知发送结果会停止整个批次', () => {
  for (const code of ['verification_required', 'rate_limited', 'login_required', 'send_unknown']) {
    assert.equal(shouldStopGreetingBatch(code), true)
  }
  assert.equal(shouldStopGreetingBatch('job_unavailable'), false)
  assert.equal(shouldStopGreetingBatch('already_contacted'), false)
})
