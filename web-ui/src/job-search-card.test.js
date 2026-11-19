import test from 'node:test'
import assert from 'node:assert/strict'
import { canAutoSearchCard, findSearchCity, summarizeSearchJobs } from './job-search-card.js'

test('历史卡片、非活动卡片、已搜索卡片不会自动查询', () => {
  const payload = { auto_search: true, status: 'editing', conditions: { keyword: 'Java' } }
  assert.equal(canAutoSearchCard({ fresh: true, active: true, payload }), true)
  assert.equal(canAutoSearchCard({ fresh: false, active: true, payload }), false)
  assert.equal(canAutoSearchCard({ fresh: true, active: false, payload }), false)
  assert.equal(canAutoSearchCard({ fresh: true, active: true, payload: { ...payload, status: 'searched' } }), false)
  assert.equal(canAutoSearchCard({ fresh: true, active: true, payload: { ...payload, conditions: {} } }), false)
})

test('城市只按明确名称匹配目录，不回退到默认上海', () => {
  const cities = [{ code: 'hz', name: '杭州' }, { code: 'sh', name: '上海' }]
  assert.equal(findSearchCity(cities, '杭州市'), cities[0])
  assert.equal(findSearchCity(cities, ''), null)
  assert.equal(findSearchCity(cities, '不存在的城市'), null)
})

test('历史摘要限制岗位数量，只保留匹配需要的JD摘录，不包含招聘者', () => {
  const jobs = Array.from({ length: 15 }, (_, index) => ({ id: index, title: 'Java 开发', companyShort: '某公司', salary: '20K', city: '杭州', description: '完整正文', recruiter: '招聘者' }))
  const summary = summarizeSearchJobs(jobs)
  assert.equal(summary.count, 15)
  assert.equal(summary.jobs.length, 10)
  assert.deepEqual(summary.jobs[0], { id: '0', title: 'Java 开发', company: '某公司', salary: '20K', city: '杭州', description_excerpt: '完整正文' })
})

test('岗位摘要优先完整jobId，securityId超过200字符也不截断标识', () => {
  const securityId = 'security-' + 'x'.repeat(480)
  const jobs = [
    { id: securityId, securityId, jobId: 'job-stable-123', title: '开发工程师' },
    { id: securityId, securityId, title: '开发工程师' },
    { securityId, title: '开发工程师' }
  ]
  const summary = summarizeSearchJobs(jobs)
  assert.equal(summary.jobs[0].id, 'job-stable-123')
  assert.equal(summary.jobs[1].id, securityId)
  assert.equal(summary.jobs[2].id, securityId)
})

test('摘要字段按独立显示上限截取，完整JD和原岗位对象不修改', () => {
  const original = { id: 'id', jobId: 'job', title: '岗'.repeat(300), company: '司'.repeat(310),
    salary: '薪'.repeat(150), city: '城'.repeat(160), description: '完整JD'.repeat(1000) }
  const before = structuredClone(original)
  const job = summarizeSearchJobs([original]).jobs[0]
  assert.equal(Array.from(job.title).length, 240)
  assert.equal(Array.from(job.company).length, 240)
  assert.equal(Array.from(job.salary).length, 120)
  assert.equal(Array.from(job.city).length, 120)
  assert.equal('description' in job, false)
  assert.equal(job.description_excerpt, original.description.slice(0, 2000))
  assert.deepEqual(original, before)
})

test('匹配上下文保留经验学历技能和JD摘录，缺失字段不编造', () => {
  const job = summarizeSearchJobs([{ id: 'job', experience: '3-5年', degree: '本科',
    skills: ['LangGraph', 'RAG'], description: '要求'.repeat(2000) }]).jobs[0]
  assert.equal(job.experience, '3-5年')
  assert.equal(job.education, '本科')
  assert.equal(job.skills, 'LangGraph、RAG')
  assert.equal(Array.from(job.description_excerpt).length, 2000)
  assert.equal('experience' in summarizeSearchJobs([{ id: 'old-job' }]).jobs[0], false)
})

test('emoji等补充字符按Unicode代码点截取，不拆开代理对', () => {
  const job = summarizeSearchJobs([{ id: 'job', title: 'a'.repeat(239) + '🧑' + '尾',
    company: '🏢'.repeat(241), salary: '💰'.repeat(121), city: '🌆'.repeat(121) }]).jobs[0]
  assert.equal(job.title, 'a'.repeat(239) + '🧑')
  assert.equal(job.company, '🏢'.repeat(240))
  assert.equal(job.salary, '💰'.repeat(120))
  assert.equal(job.city, '🌆'.repeat(120))
  assert.equal(Array.from(job.title).length, 240)
})

test('单个确认岗位和搜索列表使用相同摘要约束', () => {
  const job = { id: 'security'.repeat(90), jobId: 'job-42', title: '😀'.repeat(245),
    company: '🏢'.repeat(250), salary: '1'.repeat(121), city: '杭州' }
  const selected = summarizeSearchJobs([job]).jobs[0]
  const search = summarizeSearchJobs(Array.from({ length: 15 }, () => job))
  assert.deepEqual(selected, search.jobs[0])
  assert.equal(search.count, 15)
  assert.equal(search.jobs.length, 10)
})
