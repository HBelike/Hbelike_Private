import assert from 'node:assert/strict'
import test from 'node:test'
import vm from 'node:vm'
import { fetchBossJobData } from '../boss-data.js'
import { BOSS_CHAT_PAGE_EVIDENCE } from '../boss-greeting.js'

function fixture({ text = '', route = '/web/geek/jobs', challenge = '', payload = { code: 0, zpData: { jobInfo: { postDescription: '完整岗位正文' } } } } = {}) {
  const calls = []
  const context = vm.createContext({
    document: {
      body: { innerText: text },
      querySelectorAll: () => challenge ? [{ getBoundingClientRect: () => ({ width: 100, height: 50 }) }] : []
    },
    window: { getComputedStyle: () => ({ display: challenge === 'hidden' ? 'none' : 'block', visibility: 'visible' }) },
    location: { pathname: route }, AbortController, setTimeout, clearTimeout,
    fetch: async (endpoint) => { calls.push(endpoint); return { ok: true, json: async () => payload } }
  })
  // 和 chrome.scripting.executeScript 一样序列化执行，验证 MAIN world 不依赖模块闭包。
  const run = (endpoint = '/wapi/zpgeek/job/detail.json?securityId=test') => vm.runInContext(
    `(${fetchBossJobData.toString()})(${JSON.stringify(endpoint)}, ${JSON.stringify(BOSS_CHAT_PAGE_EVIDENCE)})`, context)
  return { calls, run }
}

test('岗位正常页面包含安全说明或请稍候时仍读取真实接口', async () => {
  for (const text of ['请注意账号安全，平台可能要求安全验证。', '加载中，请稍候', '安全验证由平台提供']) {
    const page = fixture({ text })
    const result = await page.run()
    assert.equal(result.kind, 'api')
    assert.equal(result.response.zpData.jobInfo.postDescription, '完整岗位正文')
    assert.equal(page.calls.length, 1)
  }
})

test('隐藏的验证码容器不拦截，真正可见的挑战会停止读取', async () => {
  const hidden = fixture({ challenge: 'hidden' })
  assert.equal((await hidden.run()).kind, 'api')
  const visible = fixture({ challenge: 'visible' })
  assert.equal((await visible.run()).kind, 'verification')
  assert.equal(visible.calls.length, 0)
})

test('明确验证提示和验证路由仍拦截，不请求岗位接口', async () => {
  for (const evidence of [{ text: '请完成安全验证后继续' }, { text: '拖动滑块完成验证' }, { route: '/web/geek/verify' }]) {
    const page = fixture(evidence)
    assert.equal((await page.run()).kind, 'verification')
    assert.equal(page.calls.length, 0)
  }
})

test('页面正常但接口明确要求验证时仍返回验证状态', async () => {
  const page = fixture({ payload: { code: 32, message: '请完成安全验证后继续' } })
  assert.equal((await page.run()).kind, 'verification')
  assert.equal(page.calls.length, 1)
})

test('登录页仍阻止读取，不访问未支持接口', async () => {
  const login = fixture({ route: '/web/user/' })
  assert.equal((await login.run()).kind, 'login')
  assert.equal(login.calls.length, 0)
  const page = fixture()
  assert.equal((await page.run('/wapi/zpgeek/friend/add.json')).kind, 'network')
  assert.equal(page.calls.length, 0)
})
