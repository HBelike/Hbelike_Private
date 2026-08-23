import assert from 'node:assert/strict'
import test from 'node:test'

import { createGreetingRequestPayload, createJobDetailPayload } from './job-library-bridge.js'

test('createJobDetailPayload converts a reactive-style job into clone-safe data', () => {
  const source = new Proxy({
    id: 'job-1',
    securityId: 'security-1',
    jobId: 'encrypt-job-1',
    bossId: 'encrypt-boss-1',
    lid: 'lid-1',
    title: '全栈开发工程师',
    salary: '15-30K·14薪',
    skills: new Proxy(['全栈开发', '后端开发经验'], {}),
    welfare: new Proxy(['五险一金'], {}),
    recruiterOnline: true,
    ignoredFunction() {}
  }, {})

  assert.throws(() => structuredClone(source), /could not be cloned/)

  const payload = createJobDetailPayload(source)

  assert.deepEqual(structuredClone(payload), {
    securityId: 'security-1',
    fallback: {
      id: 'job-1',
      securityId: 'security-1',
      jobId: 'encrypt-job-1',
      bossId: 'encrypt-boss-1',
      lid: 'lid-1',
      title: '全栈开发工程师',
      salary: '15-30K·14薪',
      experience: '',
      degree: '',
      city: '',
      district: '',
      skills: ['全栈开发', '后端开发经验'],
      welfare: ['五险一金'],
      recruiter: '',
      recruiterOnline: true,
      recruiterAvatar: '',
      company: '',
      companyShort: '',
      companyLogo: '',
      industry: '',
      scale: '',
      stage: '',
      sourceUrl: ''
    }
  })
})

test('createGreetingRequestPayload 只发送单个岗位标识和最终文案', () => {
  const payload = createGreetingRequestPayload({
    securityId: 'security-1',
    jobId: 'job-1',
    bossId: 'boss-1',
    lid: 'lid-1',
    description: '完整 JD 不应发送',
    resume: '简历正文不应发送'
  }, '您好，期待沟通。', true)

  assert.deepEqual(payload, {
    securityId: 'security-1',
    jobId: 'job-1',
    bossId: 'boss-1',
    lid: 'lid-1',
    message: '您好，期待沟通。',
    defaultGreetingDisabled: true
  })
})
