import assert from 'node:assert/strict'
import test from 'node:test'
import { isResumeReviewTurn, visibleConversationMessages } from './career-resume-review.js'
import { restoreServerTurns } from './career-turn-client-queue.js'
import { observedTurnStatus } from './career-turn-presentation.js'

test('自动初评刷新后仍隐藏合成用户输入，保留助手初评和真正提问', () => {
  const items = [
    { id: 'a', role: 'user', source: 'resume_review', content: '后台动作' },
    { id: 'b', role: 'assistant', source: 'resume_review', content: '建议' },
    { id: 'c', role: 'user', content: '请改项目' }
  ]
  assert.deepEqual(visibleConversationMessages(items).map(x => x.id), ['b', 'c'])
  assert.equal(items.length, 3)
})

test('恢复自动初评队列时展示阅读简历状态而不是要求用户再次输入', () => {
  const turn = { id: 'review', status: 'running', input_kind_codes: ['resume_review'] }
  const restored = restoreServerTurns([turn])
  assert.equal(restored[0].content, '简历初评')
  assert.equal(isResumeReviewTurn(restored[0].serverTurn), true)
  assert.match(observedTurnStatus(turn), /正在阅读简历/)
  assert.match(observedTurnStatus({ ...turn, status:'queued' }), /即将/)
  assert.equal(isResumeReviewTurn({input_kind_codes:['text']}), false)
})
