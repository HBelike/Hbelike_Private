import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildBossChatListEndpoint,
  buildBossChatMessagesEndpoint,
  classifyBossChatFailure,
  normalizeBossChatAccount,
  normalizeBossChatConversations,
  normalizeBossChatMessages
} from '../boss-chat.js'

const account = { platform: 'boss', platform_account_id: '100', display_name: '当前用户' }
const response = (data) => ({ code: 0, zpData: data })
const rawMessage = (patch = {}) => ({ mid: '9223372036854775807', from: { uid: 200 }, to: { uid: 100 }, time: 1794196800000, body: { type: 1, text: '您好，请问什么时候方便沟通？' }, ...patch })
const normalizeMessages = (messages) => normalizeBossChatMessages(response({ messages }), { account, peerId: '200' })

test('源分页缺失与未知值保持未知，只有明确真假才表示继续或结束', () => {
  for (const [value, expected] of [[undefined, null], [null, null], ['false', null], [false, false], [0, false], [true, true], [1, true]]) {
    const data = normalizeBossChatConversations(response({ result: [], ...(value === undefined ? {} : { hasMore: value }) }), { account })
    assert.equal(data.has_more, expected)
  }
})

test('端点仅构造求职者只读路径并编码当前标识', () => {
  assert.equal(buildBossChatListEndpoint(0), '/wapi/zprelation/friend/getGeekFriendList.json?page=1')
  assert.equal(buildBossChatListEndpoint(999), '/wapi/zprelation/friend/getGeekFriendList.json?page=100')
  const url = new URL(buildBossChatMessagesEndpoint({ peerId: '200', securityId: 'token/&?', count: 999, page: 2 }), 'https://www.zhipin.com')
  assert.equal(url.pathname, '/wapi/zpchat/geek/historyMsg')
  assert.equal(url.searchParams.get('bossId'), '200')
  assert.equal(url.searchParams.get('securityId'), 'token/&?')
  assert.equal(url.searchParams.get('c'), '100')
  assert.equal(url.searchParams.get('page'), '2')
  assert.throws(() => buildBossChatMessagesEndpoint({ peerId: '200' }), (error) => error.code === 'invalid_chat_request')
})

test('账号必须存在稳定 UID，canonical 账号可反复归一且切号明确失败', () => {
  assert.deepEqual(normalizeBossChatAccount(account, '100'), account)
  assert.equal(normalizeBossChatAccount({ uid: 100, name: '候选人' }).platform_account_id, '100')
  for (const value of [{}, { uid: 0 }, { uid: 'unknown' }, { uid: Number.MAX_SAFE_INTEGER + 1 }]) {
    assert.throws(() => normalizeBossChatAccount(value), (error) => error.code === 'login_required')
  }
  assert.throws(() => normalizeBossChatAccount(account, '101'), (error) => error.code === 'account_changed')
})

test('会话保留招聘者 UID、未知未读和真实时间，动态标识仅存在会话读取结果', () => {
  const data = normalizeBossChatConversations(response({ hasMore: true, result: [
    { uid: 200, name: '陈女士', brandName: '测试公司', jobName: '开发工程师', lastMsg: '方便聊聊吗', lastTS: 1794196800, securityId: 'fresh-token' },
    { uid: '201', name: '陈女士', unreadMsgCount: 0, lastTime: '昨天' }
  ] }), { account, observedAt: '2026-11-09T04:00:00Z' })
  assert.equal(data.has_more, true)
  assert.equal(data.observed_at, '2026-11-09T04:00:00.000Z')
  assert.equal(data.conversations[0].peer_id, '200')
  assert.equal(data.conversations[0].unread_count, null)
  assert.equal(data.conversations[0].security_id, 'fresh-token')
  assert.equal(data.conversations[0].last_message_at, '2026-11-09T04:00:00.000Z')
  assert.equal(data.conversations[1].unread_count, 0)
  assert.equal(data.conversations[1].last_message_at, null)
  assert.equal(data.conversations[0].source_url.includes('fresh-token'), false)
})

test('会话与消息不按姓名或文本去重', () => {
  const conversations = normalizeBossChatConversations(response({ friendList: [{ uid: 200, name: '同名招聘者' }, { uid: 201, name: '同名招聘者' }] }), { account })
  assert.equal(conversations.conversations.length, 2)
  const messages = normalizeMessages([rawMessage({ mid: '1' }), rawMessage({ mid: '2' }), rawMessage({ mid: '1' })])
  assert.deepEqual(messages.messages.map((item) => item.platform_message_id), ['1', '2'])
})

test('真实 mid 保留大整数字符串，方向相对当前账号，秒与毫秒时间一致', () => {
  const data = normalizeMessages([rawMessage(), rawMessage({ mid: 'outgoing-2', from: { uid: 100 }, to: { uid: 200 }, time: 1794196800 })])
  assert.equal(data.messages[0].platform_message_id, '9223372036854775807')
  assert.equal(data.messages[0].direction, 'incoming')
  assert.equal(data.messages[1].direction, 'outgoing')
  assert.equal(data.messages[0].sent_at, data.messages[1].sent_at)
  assert.equal(data.messages[0].sender_id, '200')
})

test('正文、图片、附件、语音和未知卡片分别归一，媒体不导出源文件地址', () => {
  const data = normalizeMessages([
    rawMessage({ mid: '1', body: { type: 1, text: '纯文本' } }),
    rawMessage({ mid: '2', body: { type: 2, image: { originImage: { url: 'https://private.example/image' } } } }),
    rawMessage({ mid: '3', body: { type: 99, resume: { url: 'https://private.example/resume.pdf' } } }),
    rawMessage({ mid: '4', body: { sound: { url: 'https://private.example/audio' } } }),
    rawMessage({ mid: '5', body: { type: 8, jobDesc: { title: '职位卡片' } } })
  ])
  assert.deepEqual(data.messages.map((item) => item.message_type), ['text', 'image', 'file', 'audio', 'unknown'])
  assert.deepEqual(data.messages.map((item) => item.text), ['纯文本', '', '', '', ''])
  assert.equal(JSON.stringify(data).includes('private.example'), false)
})

test('未知时间、丢失发送方或不安全数字 ID 不伪造为新消息', () => {
  for (const row of [rawMessage({ time: '刚刚' }), rawMessage({ from: {} }), rawMessage({ mid: Number.MAX_SAFE_INTEGER + 1 }), rawMessage({ time: '2026-11-09 12:00:00' })]) {
    assert.throws(() => normalizeMessages([row]), (error) => error.code === 'chat_response_invalid')
  }
})

test('消息所属账号与招聘者变化时停止而不是导入其他会话', () => {
  assert.throws(() => normalizeMessages([rawMessage({ to: { uid: 101 } })]), (error) => error.code === 'account_changed')
  assert.throws(() => normalizeMessages([rawMessage({ from: { uid: 201 } })]), (error) => error.code === 'conversation_changed')
})

test('平台拒绝、身份失效与风险错误保持不同分类', () => {
  assert.equal(classifyBossChatFailure({ code: 37 }).code, 'login_required')
  assert.equal(classifyBossChatFailure({ code: 37, message: '环境存在异常，请验证' }).code, 'verification_required')
  assert.equal(classifyBossChatFailure({ code: 9 }).code, 'rate_limited')
  assert.equal(classifyBossChatFailure({ code: 24 }).code, 'login_required')
  assert.throws(() => normalizeBossChatConversations({ code: 36 }, { account }), (error) => error.code === 'verification_required')
  assert.throws(() => normalizeBossChatConversations(response({}), { account }), (error) => error.code === 'chat_response_invalid')
})
