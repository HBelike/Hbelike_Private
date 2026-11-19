import assert from 'node:assert/strict'
import test from 'node:test'

const WEB_CHANNEL = 'find-job-job-library-web-v1'
const nextTurn = () => new Promise((resolve) => setImmediate(resolve))

test('超过200字的招呼语能通过预检、发送和重发入口的本地校验', async (t) => {
  const originalChrome = globalThis.chrome
  t.after(() => { globalThis.chrome = originalChrome })
  let dispatcher
  let tabCalls = 0
  const event = () => ({ addListener() {} })
  globalThis.chrome = {
    runtime: { getManifest: () => ({ version: '0.4.2' }),
      onMessage: { addListener(listener) { dispatcher = listener } }, onInstalled: event(), onStartup: event() },
    tabs: { query() { tabCalls++; throw new Error('已到达模拟标签页查询，未访问真实BOSS页面') } },
    windows: { onRemoved: event() }, action: { onClicked: event() }
  }
  await import(`../service-worker.js?message-target-test=${Date.now()}`)
  for (const action of ['preflight_greeting', 'send_greeting', 'retry_greeting_message']) {
    const response = await new Promise((resolve) => dispatcher({ channel: WEB_CHANNEL, action,
      payload: { securityId: 's', jobId: 'j', bossId: 'b', lid: 'l', message: '字'.repeat(201) }
    }, { url: 'http://127.0.0.1:5173/' }, resolve))
    assert.equal(response.ok, false)
    assert.equal(response.error.code, 'extension_error')
    assert.match(response.error.message, /已到达模拟标签页查询/)
  }
  assert.equal(tabCalls, 3)
})

test('业务请求阻塞时 ping 立即响应，非法来源及通道仍被拒绝，业务继续串行', async (t) => {
  const originalChrome = globalThis.chrome
  t.after(() => { globalThis.chrome = originalChrome })
  let dispatcher
  let rejectBusiness
  let queryCalls = 0
  const event = () => ({ addListener() {} })
  globalThis.chrome = {
    runtime: {
      getManifest: () => ({ version: '0.4.1' }),
      onMessage: { addListener(listener) { dispatcher = listener } },
      onInstalled: event(),
      onStartup: event()
    },
    tabs: {
      query() {
        queryCalls += 1
        if (queryCalls === 1) return new Promise((resolve, reject) => { rejectBusiness = reject })
        return Promise.reject(new Error('测试结束第二个业务请求。'))
      }
    },
    windows: { onRemoved: event() },
    action: { onClicked: event() }
  }
  // 导入真实后台模块并捕获其注册的消息入口，不复刻调度逻辑。
  await import(`../service-worker.js?dispatcher-test=${Date.now()}`)
  const sender = { url: 'http://127.0.0.1:5173/' }
  const request = (action, channel = WEB_CHANNEL) => ({ channel, action, payload: {} })
  const businessResponses = []
  assert.equal(dispatcher(request('list_cities'), sender, (value) => businessResponses.push(value)), true)
  await nextTurn()
  assert.equal(queryCalls, 1)
  dispatcher(request('list_cities'), sender, (value) => businessResponses.push(value))

  const pingResponses = []
  dispatcher(request('ping'), sender, (value) => pingResponses.push(value))
  dispatcher(request('ping'), { url: 'https://other.example/' }, (value) => pingResponses.push(value))
  dispatcher(request('ping', 'wrong-channel'), sender, (value) => pingResponses.push(value))
  await nextTurn()

  assert.equal(queryCalls, 1)
  assert.equal(businessResponses.length, 0)
  assert.equal(pingResponses.length, 3)
  assert.equal(pingResponses[0].ok, true)
  assert.equal(pingResponses[0].data.version, '0.4.1')
  assert.equal(pingResponses[0].data.connected, true)
  assert.equal(pingResponses[0].data.capabilities.includes('retry_greeting_message'), true)
  assert.equal(pingResponses[0].data.capabilities.includes('greeting_submission_state'), true)
  assert.equal(pingResponses[0].data.capabilities.includes('online_assessment_capture_v2'), true)
  assert.equal(pingResponses[1].error.code, 'forbidden_sender')
  assert.equal(pingResponses[2].error.code, 'forbidden_sender')

  rejectBusiness(new Error('测试释放首个业务请求。'))
  await nextTurn()
  assert.equal(queryCalls, 2)
  assert.equal(businessResponses.length, 2)
  assert.equal(businessResponses.every((value) => value.error.code === 'extension_error'), true)

  const invalidBusiness = []
  dispatcher(request('list_cities'), { url: 'https://other.example/' }, (value) => invalidBusiness.push(value))
  await nextTurn()
  assert.equal(queryCalls, 2)
  assert.equal(invalidBusiness[0].error.code, 'forbidden_sender')
})
