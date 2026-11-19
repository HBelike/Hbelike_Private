import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const appSource = readFileSync(new URL('./App.vue', import.meta.url), 'utf8')
const pageSource = readFileSync(new URL('./components/CareerAssistantPage.vue', import.meta.url), 'utf8')
const dialogSource = readFileSync(new URL('./components/CareerContextSetupDialog.vue', import.meta.url), 'utf8')

test('求职助手向简历选择框传递管理员身份', () => {
  assert.match(appSource, /<CareerAssistantPage[\s\S]*?:current-user="authUser"/)
  assert.match(pageSource, /currentUser:\s*\{\s*type:\s*Object/)
  assert.match(pageSource, /:show-resume-owner="currentUser\?\.role === 'admin'"/)
})

test('管理员简历列表展示服务端返回的所有者名称', () => {
  assert.match(dialogSource, /showResumeOwner:\s*\{\s*type:\s*Boolean/)
  assert.match(dialogSource, /v-if="showResumeOwner" class="resume-owner"/)
  assert.match(dialogSource, /profile\.owner_display_name/)
})
