import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const componentUrl = new URL('./components/CareerAssistantPage.vue', import.meta.url)

function functionSource(source, name) {
  const start = source.indexOf(`function ${name}`)
  assert.notEqual(start, -1, `未找到函数 ${name}`)
  const boundary = /\r?\n}\r?\n/g
  boundary.lastIndex = start
  const match = boundary.exec(source)
  assert.ok(match, `函数 ${name} 缺少结束边界`)
  return source.slice(start, match.index + match[0].length)
}

test('@ 只检索面经，/ 才能唤醒 Skill', async () => {
  const source = await readFile(componentUrl, 'utf8')
  const activeSkillInvocation = functionSource(source, 'activeSkillInvocation')
  const selectSkillMention = functionSource(source, 'selectSkillMention')
  const removeSkillInvocation = functionSource(source, 'removeSkillInvocation')
  const skillInvocationPresent = functionSource(source, 'skillInvocationPresent')

  assert.doesNotMatch(activeSkillInvocation, /\[@\/\]/)
  assert.ok(activeSkillInvocation.includes('text.match(/(?:^|\\s)\\/('))
  assert.match(selectSkillMention, /const trigger = '\/'/)
  assert.doesNotMatch(selectSkillMention, /\[@\/\]/)
  assert.doesNotMatch(removeSkillInvocation, /\[@\/\]/)
  assert.doesNotMatch(skillInvocationPresent, /\[@\/\]/)
  assert.match(source, /输入 @ 可引用面经，输入 \/ 可调用 Skill/)
  assert.match(source, /输入你想咨询的问题…/)
  assert.doesNotMatch(source, /基于当前简历与目标岗位提问/)
})
