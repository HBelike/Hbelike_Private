import { test, expect } from '@playwright/test'
import { installPlatformFixtures } from './support/platform-fixtures.js'

test('转写超过百句后仍能提示新消息，未确认的结束会提示尾句风险', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await installPlatformFixtures(page)
  await page.addInitScript(() => {
    navigator.mediaDevices.getDisplayMedia = async () => {
      const context = new AudioContext()
      const destination = context.createMediaStreamDestination()
      const oscillator = context.createOscillator()
      oscillator.connect(destination)
      oscillator.start()
      return destination.stream
    }
  })
  await page.route('**/api/career/live-interviews/setup-options', route => route.fulfill({
    json: { environment_asr: { readiness: 'ready' }, answer_models: [] }
  }))
  await page.route('**/api/career/live-interviews/sessions', route => route.fulfill({ json: { session: { id: 'long-case' } } }))
  await page.route('**/api/career/live-interviews/sessions/long-case/end', route => route.fulfill({ json: {} }))
  await page.route('**/api/career/live-interviews/archive/preview', route => route.fulfill({ json: { questions: [] } }))
  let stream
  await page.routeWebSocket('**/long-case/stream', connection => {
    stream = connection
    stream.send(JSON.stringify({ type: 'session.ready', active_channels: ['interviewer'] }))
    stream.onMessage(raw => {
      if (JSON.parse(raw).type === 'session.end') stream.close({ code: 1000 })
    })
  })
  await page.goto('/career/interview-master')
  await page.getByText('我已了解采集范围', { exact: false }).click()
  await page.getByRole('button', { name: '开始面试' }).click()
  await expect(page.getByRole('heading', { name: '实时转写' })).toBeVisible()
  await expect.poll(() => Boolean(stream)).toBe(true)
  const sendFinal = sequence => stream.send(JSON.stringify({
    type: 'transcript.final', channel: 'interviewer', sequence, text: `第${sequence}句面试发言`
  }))
  for (let sequence = 1; sequence <= 100; sequence += 1) sendFinal(sequence)
  const list = page.locator('.transcript-list')
  await expect(list).toContainText('第100句面试发言')
  await list.evaluate(element => { element.scrollTop = 0 })
  await expect(page.getByRole('button', { name: '回到最新' })).toBeVisible()
  sendFinal(101)
  sendFinal(102)
  await page.getByRole('button', { name: '2 条新消息' }).click()
  await expect.poll(() => list.evaluate(element => element.scrollHeight - element.scrollTop - element.clientHeight)).toBeLessThan(3)
  await page.getByRole('button', { name: '结束面试', exact: true }).click()
  await expect(page.getByRole('dialog').getByRole('alert')).toHaveText('最后一句未收到服务端保存确认，归档内容可能不完整。')
})
