import assert from 'node:assert/strict'
import test from 'node:test'
import vm from 'node:vm'
import { readFile } from 'node:fs/promises'
import { readBossChatPage } from '../boss-chat-page.js'
import { normalizeBossChatMessages } from '../boss-chat.js'

function sandbox({ page = { uid: 100, name: '本地用户' }, pathname = '/web/geek/chat', fetchImpl, challenge = false } = {}) {
  const calls = []
  const location = new URL(`https://www.zhipin.com${pathname}`)
  const window = { _PAGE: page, getComputedStyle: () => ({ display: 'block', visibility: 'visible' }) }
  const context = {
    window,
    location,
    document: { querySelector: () => null, querySelectorAll: () => challenge ? [{ getBoundingClientRect: () => ({ width: 100, height: 50 }) }] : [] },
    URL,
    URLSearchParams,
    AbortController,
    setTimeout: (callback, milliseconds) => setTimeout(callback, milliseconds === 200 ? 0 : milliseconds),
    clearTimeout,
    fetch: async (url, options) => {
      calls.push({ url, options })
      if (fetchImpl) return fetchImpl(url, options, window)
      return { ok: true, status: 200, url: `https://www.zhipin.com${url}`, text: async () => JSON.stringify({ code: 0, zpData: { result: [] } }) }
    }
  }
  vm.createContext(context)
  return { calls, context, run: async (request) => JSON.parse(JSON.stringify(await vm.runInContext(`(${readBossChatPage.toString()})(${JSON.stringify(request)})`, context))) }
}

test('MAIN 读取函数序列化后不依赖闭包，账号检测不访问网络', async () => {
  const fixture = sandbox()
  const result = await fixture.run({ action: 'state' })
  assert.equal(result.ok, true)
  assert.equal(result.data.account.platform_account_id, '100')
  assert.equal(fixture.calls.length, 0)
})

test('稳定账号 UID 缺失时拒绝，不从对方身份或存储拼凑账号', async () => {
  const fixture = sandbox({ page: {} })
  fixture.context.peerId = '200'
  const result = await fixture.run({ action: 'list', peerId: '200' })
  assert.equal(result.error.code, 'login_required')
  assert.equal(fixture.calls.length, 0)
})

test('应用延迟挂载时有限等待稳定 UID，不把标签页加载完成误判为未登录', async () => {
  const fixture = sandbox({ page: {} })
  let waits = 0
  fixture.context.setTimeout = (callback, milliseconds) => setTimeout(() => {
    if (milliseconds === 200 && ++waits === 2) fixture.context.window._PAGE.uid = 100
    callback()
  }, milliseconds === 200 ? 0 : milliseconds)
  const result = await fixture.run({ action: 'state' })
  assert.equal(result.data.account.platform_account_id, '100')
  assert.equal(waits, 2)
  assert.equal(fixture.calls.length, 0)
})

test('请求仅到求职者列表 GET；任意 endpoint 参数不能覆盖白名单', async () => {
  const fixture = sandbox()
  const result = await fixture.run({ action: 'list', endpoint: 'https://evil.example/mark-read', page: 2 })
  assert.equal(result.ok, true)
  assert.equal(fixture.calls[0].url, '/wapi/zprelation/friend/getGeekFriendList.json?page=2')
  assert.equal(fixture.calls[0].options.method, 'GET')
  assert.equal(fixture.calls[0].options.credentials, 'include')
  assert.equal(fixture.calls[0].options.body, undefined)
})

test('读取消息必须冻结账号和会话，并编码求职者 historyMsg 参数', async () => {
  const fixture = sandbox()
  const rejected = await fixture.run({ action: 'messages', peerId: '200', securityId: 'current' })
  assert.equal(rejected.error.code, 'invalid_chat_request')
  assert.equal(fixture.calls.length, 0)
  const result = await fixture.run({ action: 'messages', accountId: '100', peerId: '200', securityId: 'token/&?', count: 20 })
  assert.equal(result.ok, true)
  const endpoint = new URL(fixture.calls[0].url, 'https://www.zhipin.com')
  assert.equal(endpoint.pathname, '/wapi/zpchat/geek/historyMsg')
  assert.equal(endpoint.searchParams.get('bossId'), '200')
  assert.equal(endpoint.searchParams.get('securityId'), 'token/&?')
})

test('请求前与响应后切号都明确失败，不返回旧账号名下的新数据', async () => {
  const before = sandbox()
  assert.equal((await before.run({ action: 'list', accountId: '101' })).error.code, 'account_changed')
  assert.equal(before.calls.length, 0)
  const after = sandbox({ fetchImpl: async (_url, _options, window) => {
    window._PAGE.uid = 101
    return { ok: true, status: 200, text: async () => '{"code":0,"zpData":{"result":[]}}' }
  } })
  assert.equal((await after.run({ action: 'list', accountId: '100' })).error.code, 'account_changed')
})

test('可见验证码、登录页、身份错误在请求前停止', async () => {
  for (const config of [{ challenge: true }, { pathname: '/web/user/' }, { page: { uid: 100, identity: 1 } }]) {
    const fixture = sandbox(config)
    const result = await fixture.run({ action: 'list' })
    assert.equal(result.ok, false)
    assert.equal(fixture.calls.length, 0)
  }
})

test('HTTP 和业务限流、环境拒绝、登录失效保持平台级错误', async () => {
  const cases = [
    [{ status: 429, ok: false }, 'rate_limited'],
    [{ status: 403, ok: false }, 'verification_required'],
    [{ status: 401, ok: false }, 'login_required'],
    [{ status: 200, ok: true, body: { code: 37, message: '环境存在异常' } }, 'verification_required'],
    [{ status: 200, ok: true, body: { code: 37 } }, 'login_required'],
    [{ status: 200, ok: true, body: { code: 9 } }, 'rate_limited']
  ]
  for (const [sample, code] of cases) {
    const fixture = sandbox({ fetchImpl: async () => ({ ...sample, text: async () => JSON.stringify(sample.body || {}) }) })
    assert.equal((await fixture.run({ action: 'list' })).error.code, code)
    assert.equal(fixture.calls.length, 1)
  }
})

test('同源 HTTP200 验证页不当作普通 JSON 错误或空消息', async () => {
  const fixture = sandbox({ fetchImpl: async () => ({ ok: true, status: 200, text: async () => '<html><title>安全验证</title><div id="captcha"></div></html>' }) })
  assert.equal((await fixture.run({ action: 'list' })).error.code, 'verification_required')
})

test('长 mid 在 JSON 解析前保真，不改写正文中转义的类似数字', async () => {
  const fixture = sandbox({ fetchImpl: async () => ({ ok: true, status: 200, text: async () => '{"code":0,"zpData":{"messages":[{"mid":9223372036854775807,"from":{"uid":200},"to":{"uid":100},"time":1794196800000,"body":{"type":1,"text":"正文中的\\\"mid\\\":9223372036854775807"}}]}}' }) })
  const result = await fixture.run({ action: 'messages', accountId: '100', peerId: '200', securityId: 'current' })
  assert.equal(result.data.response.zpData.messages[0].mid, '9223372036854775807')
  assert.equal(result.data.response.zpData.messages[0].body.text, '正文中的"mid":9223372036854775807')
  const normalized = normalizeBossChatMessages(result.data.response, { account: result.data.account, peerId: '200' })
  assert.equal(normalized.messages[0].platform_message_id, '9223372036854775807')
})

test('模块不包含发送、好友建立、标已读或登录凭据读取行为', async () => {
  const source = await readFile(new URL('../boss-chat-page.js', import.meta.url), 'utf8')
  assert.doesNotMatch(source, /\.click\(|\.focus\(|document\.cookie|localStorage|friend\/add|mark.?read|messageRead|method:\s*['"]POST/i)
})
