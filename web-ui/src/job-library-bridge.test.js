import assert from 'node:assert/strict'
import test from 'node:test'

import { createJobDetailPayload } from './job-library-bridge.js'

test('createJobDetailPayload converts a reactive-style job into clone-safe data', () => {
  const source = new Proxy({
    id: 'job-1',
    securityId: 'security-1',
    jobId: 'encrypt-job-1',
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
