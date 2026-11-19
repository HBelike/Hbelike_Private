import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createSharedMaterialDraft, sharedMaterialOptions, shareReturnLocation } from './material-share.js'

test('Android 分享链接可来自 text 或 title，保留定位岗位的查询和片段', () => {
  const url = 'https://jobs.example.org/#/role/123?city=beijing'
  assert.deepEqual(sharedMaterialOptions({ text: `看看这个职位：${url}。` }).links, [url])
  assert.deepEqual(sharedMaterialOptions({ title: url }).links, [url])
  assert.deepEqual(sharedMaterialOptions({ url, text: url }).links, [url])
})

test('多个链接保留全部候选，非 HTTPS 内容只作为正文', () => {
  assert.equal(sharedMaterialOptions({ text: 'https://a.example/job https://b.example/job' }).links.length, 2)
  assert.deepEqual(sharedMaterialOptions({ text: 'javascript:alert(1) http://jobs.example.org/job' }).links, [])
})

test('文件和正文同时收到时保留两种材料供用户明确选择', () => {
  const file = { name: 'job.png' }
  const options = sharedMaterialOptions({ title: '岗位', text: '完整职位正文', files: [file] })
  assert.equal(options.file, file)
  assert.equal(options.text, '完整职位正文')
})

test('岗位分享草稿清空旧岗位名称，复用当前简历，等待解析和确认', () => {
  const candidate = { id: 'candidate-1' }
  const draft = createSharedMaterialDraft({ type: 'job', mode: 'url', url: 'https://jobs.example.org/job' }, { candidate_profile: candidate })
  assert.equal(draft.step, 2)
  assert.equal(draft.selectedCandidate, candidate)
  assert.equal(draft.selectedTarget, null)
  assert.equal(draft.companyName, '')
  assert.equal(draft.jobText, '')
  assert.equal(draft.jobUrlText, 'https://jobs.example.org/job')
})

test('简历图片移交同一个文件对象，不自动解析或写岗位字段', () => {
  const file = { name: '真实简历.png' }
  const draft = createSharedMaterialDraft({ type: 'resume', file }, null)
  assert.equal(draft.resumeFile, file)
  assert.equal(draft.resumeMode, 'image')
  assert.equal(draft.resumePreview, null)
  assert.equal(draft.resumeDisplayName, '真实简历')
  assert.equal(Object.hasOwn(draft, 'jobText'), false)
})

test('只替换选定用途，岗位分享保留待解析简历，简历分享保留岗位草稿', () => {
  const resume = { name: 'pending-resume.pdf' }
  const old = { resumeFile: resume, resumeDisplayName: '原简历', jobText: '原岗位正文', jobUrlText: 'https://old.example/job' }
  const nextJob = createSharedMaterialDraft({ type: 'job', mode: 'text', text: '新岗位正文' }, null, old)
  assert.equal(nextJob.resumeFile, resume)
  assert.equal(nextJob.resumeDisplayName, '原简历')
  assert.equal(nextJob.jobText, '新岗位正文')
  const nextResume = createSharedMaterialDraft({ type: 'resume', file: { name: 'new.pdf' } }, null, old)
  assert.equal(nextResume.jobText, old.jobText)
  assert.equal(nextResume.jobUrlText, old.jobUrlText)
})

test('分享确认页经登录仍回到相同材料，普通登录不改变入口', () => {
  assert.equal(shareReturnLocation({ pathname: '/career/import', search: '?share=material-1' }), '/career/import?share=material-1')
  for (const pathname of ['/login', '/register', '/forgot-password']) {
    assert.equal(shareReturnLocation({ pathname, search: '?share=material-1' }), '/career/import?share=material-1')
  }
  assert.equal(shareReturnLocation({ pathname: '/login', search: '?next=https://example.org' }), '')
})
