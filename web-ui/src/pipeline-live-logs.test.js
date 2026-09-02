import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const panel = readFileSync(new URL('./components/ManualPipelinePanel.vue', import.meta.url), 'utf8')
const dialog = readFileSync(new URL('./components/PipelineLogDialog.vue', import.meta.url), 'utf8')

test('工作流运行记录提供查看日志按钮和独立弹窗', () => {
  assert.match(panel, /查看日志/)
  assert.match(panel, /<PipelineLogDialog/)
  assert.match(dialog, /role="dialog"/)
  assert.match(dialog, /工作流日志/)
})

test('手动生成流程在审核预览结束，不宣称自动创建草稿', () => {
  assert.match(panel, /运行生成流程/)
  assert.match(panel, /审核通过后才创建公众号草稿/)
  assert.doesNotMatch(panel, /运行完整流程/)
  assert.doesNotMatch(panel, /完整流水线/)
})

test('手动生成会先刷新 GitHub 周榜且没有历史快照也可启动', () => {
  assert.match(panel, /运行时会先刷新 GitHub 周榜/)
  assert.doesNotMatch(panel, /:disabled="runningPipeline \|\| snapshotLoading \|\| !githubSnapshot"/)
})

test('日志弹窗先读取历史再使用 SSE 游标实时续传', () => {
  assert.match(dialog, /\/api\/admin\/pipeline-runs\/\$\{props\.run\.id\}\/logs/)
  assert.match(dialog, /new EventSource\(streamUrl\)/)
  assert.match(dialog, /after_id/)
  assert.match(dialog, /lastEventId/)
})

test('日志弹窗覆盖链路事件并在终态或卸载时关闭连接', () => {
  assert.match(dialog, /task_started/)
  assert.match(dialog, /task_succeeded/)
  assert.match(dialog, /task_failed/)
  assert.match(dialog, /run_succeeded/)
  assert.match(dialog, /run_failed/)
  assert.match(dialog, /onBeforeUnmount\(\(\) =>/)
  assert.match(dialog, /removeEventListener\('keydown', handleKeydown\)/)
})
