import assert from 'node:assert/strict'
import test from 'node:test'
import { EVALUATION_VIEWS, evaluationViewForRoute, evaluationRouteForView } from './evaluation-navigation.js'
import { normalizeAppRoute } from './navigation-access.js'

test('评测子页面可直接访问，未知子页面回到总览', () => {
  for (const [key, { route }] of Object.entries(EVALUATION_VIEWS)) {
    assert.equal(normalizeAppRoute(route), route)
    assert.equal(evaluationViewForRoute(route), key)
    assert.equal(evaluationRouteForView(key), route)
  }
  assert.equal(normalizeAppRoute('/evaluations/unknown'), '/evaluations')
})
