import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  runExtensionTask,
  settleExtensionCalls
} from '../extension-lifecycle.js'

const workerSource = await readFile(new URL('../service-worker.js', import.meta.url), 'utf8')

test('后台独立任务捕获同步抛错和异步拒绝', async () => {
  await assert.doesNotReject(runExtensionTask(() => {
    throw new Error('Extension context invalidated.')
  }))
  await assert.doesNotReject(runExtensionTask(() => Promise.reject(new Error('Extension context invalidated.'))))
})

test('后台任务的失败反馈再次失效时仍不会产生未处理拒绝', async () => {
  let feedbackCalls = 0
  await assert.doesNotReject(runExtensionTask(
    () => Promise.reject(new Error('当前页面没有返回可识别的题面内容。')),
    () => {
      feedbackCalls += 1
      throw new Error('Extension context invalidated.')
    }
  ))
  assert.equal(feedbackCalls, 1)
})

test('扩展 API 批量反馈逐项隔离同步异常与异步拒绝', async () => {
  const completed = []
  const results = await settleExtensionCalls([
    () => { throw new Error('Extension context invalidated.') },
    () => Promise.reject(new Error('Extension context invalidated.')),
    async () => { completed.push('ok') }
  ])

  assert.deepEqual(completed, ['ok'])
  assert.equal(results.length, 3)
  assert.equal(results.every((result) => ['fulfilled', 'rejected'].includes(result.status)), true)
})

test('Service Worker 生命周期和点击入口统一使用安全任务边界', () => {
  assert.match(workerSource, /onInstalled\.addListener\(\(\) => \{\s*runExtensionTask\(injectBridgeIntoOpenAppTabs\)/s)
  assert.match(workerSource, /onStartup\.addListener\(\(\) => \{\s*runExtensionTask\(injectBridgeIntoOpenAppTabs\)/s)
  assert.match(workerSource, /onClicked\.addListener\(\(tab\) => \{[\s\S]*runExtensionTask\(/)
  assert.doesNotMatch(workerSource, /void injectBridgeIntoOpenAppTabs\(\)/)
  assert.doesNotMatch(workerSource, /void captureAssessmentTab\(/)
})
