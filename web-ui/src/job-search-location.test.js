import test from 'node:test'
import assert from 'node:assert/strict'
import { matchLocatedCity, resolveJobSearchCity } from './job-search-location.js'

const cities = [{ code: '101210100', name: '杭州' }]

function browser(state = 'granted', fail = null) {
  let requests = 0
  return {
    get requests() { return requests },
    permissions: { query: async () => ({ state }) },
    geolocation: {
      getCurrentPosition(resolve, reject) {
        requests += 1
        if (fail) reject(fail)
        else resolve({ coords: { latitude: 30.25, longitude: 120.15 } })
      }
    }
  }
}

test('未授权时先询问启用定位，不触发浏览器权限请求或网络请求', async () => {
  const navigator = browser('prompt')
  const answer = await resolveJobSearchCity({ cities, navigator, fetch: () => assert.fail('不应查询坐标') })
  assert.equal(answer.status, 'permission_required')
  assert.equal(answer.city, null)
  assert.equal(navigator.requests, 0)
})

test('已有定位授权时按坐标查询城市并匹配真实招聘城市目录', async () => {
  const answer = await resolveJobSearchCity({ cities, navigator: browser(), fetch: async (url, options) => {
    assert.match(url, /^https:\/\/api\.bigdatacloud\.net\/data\/reverse-geocode-client\?/)
    assert.match(url, /latitude=30.25/)
    assert.equal(options.credentials, 'omit')
    assert.equal(options.referrerPolicy, 'no-referrer')
    return { ok: true, json: async () => ({ city: '杭州市' }) }
  } })
  assert.equal(answer.status, 'located')
  assert.equal(answer.city, cities[0])
})

test('点击启用定位后允许主动请求权限', async () => {
  const navigator = browser('prompt')
  const answer = await resolveJobSearchCity({ cities, navigator, requestPermission: true,
    fetch: async () => ({ ok: true, json: async () => ({ city: '杭州' }) }) })
  assert.equal(answer.status, 'located')
  assert.equal(navigator.requests, 1)
})

test('拒绝权限或定位超时不使用缓存城市和默认上海', async () => {
  const denied = await resolveJobSearchCity({ cities, navigator: browser('denied') })
  assert.equal(denied.status, 'denied')
  assert.equal(denied.city, null)
  const timeout = await resolveJobSearchCity({ cities, navigator: browser('granted', { code: 3 }) })
  assert.equal(timeout.status, 'unavailable')
  assert.equal(timeout.city, null)
})

test('不支持Permissions API时仍需用户主动启用', async () => {
  const navigator = browser()
  delete navigator.permissions
  assert.equal((await resolveJobSearchCity({ cities, navigator })).status, 'permission_required')
  assert.equal(navigator.requests, 0)
})

test('逆地理服务失败或目录未包含定位城市时允许手动选择', async () => {
  const unavailable = await resolveJobSearchCity({ cities, navigator: browser(), fetch: async () => ({ ok: false }) })
  assert.equal(unavailable.status, 'unavailable')
  const unmatched = await resolveJobSearchCity({ cities, navigator: browser(),
    fetch: async () => ({ ok: true, json: async () => ({ city: '南京市' }) }) })
  assert.equal(unmatched.city, null)
  assert.equal(unmatched.status, 'unavailable')
})

test('城市匹配不把空城市或区县当成有效招聘城市', () => {
  assert.equal(matchLocatedCity('', cities), null)
  assert.equal(matchLocatedCity('西湖区', cities), null)
  assert.equal(matchLocatedCity('杭州市', cities), cities[0])
})

test('浏览器不支持定位时直接保留手选入口', async () => {
  const answer = await resolveJobSearchCity({ cities, navigator: {}, fetch: () => assert.fail('不应调用服务') })
  assert.equal(answer.status, 'unavailable')
  assert.equal(answer.city, null)
})

test('主动启用后拒绝授权不发送任何坐标', async () => {
  const answer = await resolveJobSearchCity({ cities, navigator: browser('prompt', { code: 1 }),
    requestPermission: true, fetch: () => assert.fail('拒绝授权不能发送坐标') })
  assert.equal(answer.status, 'denied')
})

test('每次使用设备当前坐标，不允许读取历史坐标缓存', async () => {
  const navigator = browser()
  navigator.geolocation.getCurrentPosition = (resolve, reject, options) => {
    assert.equal(options.maximumAge, 0)
    resolve({ coords: { latitude: 30.25, longitude: 120.15 } })
  }
  const answer = await resolveJobSearchCity({ cities, navigator,
    fetch: async () => ({ ok: true, json: async () => ({ city: '杭州市' }) }) })
  assert.equal(answer.status, 'located')
})
