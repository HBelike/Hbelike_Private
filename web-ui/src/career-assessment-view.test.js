import test from 'node:test'
import assert from 'node:assert/strict'
import {
  assessmentCanvasSections,
  buildAssessmentCards,
  isAssessmentPending,
  itemsForDimension
} from './career-assessment-view.js'

const assessment = {
  status: 'ready',
  algorithm_version: 'llm-judge-v1',
  dimensions: [
    { id: 'performance', label: '才艺与表达', description: '考察演唱和表达', status: 'ready', score: 75, numerator: 1.5, denominator: 2 },
    { id: 'interaction', label: '互动能力', description: '考察直播互动', status: 'insufficient_data', score: null },
    { id: 'critical_gap', label: '关键要求缺口率', description: '硬性要求缺口', status: 'ready', score: 25, numerator: 0.5, denominator: 2 }
  ],
  items: [
    { id: 'one', dimension_id: 'performance', requirement_type: 'required', factor: 0.5 },
    { id: 'two', dimension_id: 'interaction', requirement_type: 'preferred', factor: null }
  ],
  job_sections: [
    { category: 'responsibility', items: ['唱歌和聊天互动'] },
    { category: 'required_qualification', items: ['具备稳定表达能力'] },
    { category: 'company_information', items: ['公司拥有十年行业经验'] }
  ]
}

test('动态指标不再出现技术岗位固定模板', () => {
  assert.deepEqual(buildAssessmentCards(assessment).map((item) => item.short), ['才艺与表达', '关键要求缺口率'])
})

test('关键缺口只展示尚未充分满足的硬性要求', () => {
  assert.deepEqual(itemsForDimension(assessment, 'critical_gap').map((item) => item.id), ['one'])
})

test('只有运行中状态需要轮询', () => {
  assert.equal(isAssessmentPending({ status: 'queued' }), true)
  assert.equal(isAssessmentPending({ status: 'analyzing' }), true)
  assert.equal(isAssessmentPending({ status: 'ready' }), false)
})

test('Judge 分区把公司历史留在公司信息而不是任职要求', () => {
  const sections = assessmentCanvasSections(assessment)
  assert.deepEqual(sections.responsibilities, ['唱歌和聊天互动'])
  assert.deepEqual(sections.requirements, [{ text: '具备稳定表达能力', type: '必须' }])
  assert.deepEqual(sections.supporting[0], {
    key: 'company_information',
    label: '公司信息',
    items: ['公司拥有十年行业经验']
  })
})
