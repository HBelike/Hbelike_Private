import test from 'node:test'
import assert from 'node:assert/strict'
import { buildAssessmentCards, assessmentCanvasSections, itemsForDimension } from './career-assessment-view.js'
import { filterReportItems, formatMatchScore, reportItems } from './career-match-report.js'

const assessment = {
  status: 'ready', algorithm_version: 'jd-evidence-rules-v2',
  summaries: {
    hard: { verdict: 'needs_confirmation', status: 'ready', score: 50, item_count: 2, supported_count: 1 },
    core: { status: 'ready', score: 62.5, score_upper: 100, item_count: 4 },
    bonus: { status: 'insufficient_data', score: null, item_count: 0, supported_count: 0 }
  },
  items: [{ id: 'age', tier: 'hard', dimension_id: 'qualification', display_status: 'needs_confirmation', title: '年龄条件' },
    { id: 'activity', tier: 'core', dimension_id: 'operations', display_status: 'partial', title: '活动与复盘' }],
  job_sections: [{ category: 'responsibility', items: ['组织用户活动'] }]
}

test('三层卡片保留硬门槛与未知区间，无加分项不显示零分', () => {
  const cards = buildAssessmentCards(assessment)
  assert.deepEqual(cards.map(x => x.key), ['hard', 'core', 'bonus'])
  assert.equal(formatMatchScore(cards[0]), '条件待核实')
  assert.equal(formatMatchScore(cards[1]), '62.5–100%')
  assert.equal(formatMatchScore(cards[2]), 'JD 未列明')
})

test('点击卡片可按层级或具体维度定位', () => {
  assert.deepEqual(itemsForDimension(assessment, 'hard').map(x => x.id), ['age'])
  assert.deepEqual(itemsForDimension(assessment, 'operations').map(x => x.id), ['activity'])
  assert.deepEqual(filterReportItems(reportItems(assessment), { tier: 'core', status: 'partial' }).map(x => x.id), ['activity'])
  assert.deepEqual(filterReportItems(reportItems(assessment), { status: 'conflict' }), [])
})

test('新版岗位画布使用真实层级而不是把任职要求全标必须', () => {
  assert.deepEqual(assessmentCanvasSections(assessment).requirements, [{ text: '年龄条件', type: '硬性' }, { text: '活动与复盘', type: '核心' }])
})

test('旧版词法结果仍可查看证据且不伪装成新版门槛', () => {
  const rows = reportItems({ requirements: [{ id: 'one', text: '相关经验', match_factor: 0, evidence: '经历原文', priority: 'must' }] })
  assert.equal(rows[0].tier, 'core')
  assert.equal(rows[0].display_status, 'needs_confirmation')
  assert.deepEqual(rows[0].cv_evidence, ['经历原文'])
})
