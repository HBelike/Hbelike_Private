import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const component = readFileSync(
  new URL('./components/InterviewLibraryPage.vue', import.meta.url),
  'utf8'
)
const themeCss = readFileSync(new URL('./theme.css', import.meta.url), 'utf8')

test('面经编辑支持公司名称和面试岗位并在保存后刷新树', () => {
  assert.match(component, /const editorCompanyName = ref\(''\)/)
  assert.match(component, /const editorRoleName = ref\(''\)/)
  assert.match(component, /<span>公司名称<\/span>/)
  assert.match(component, /<span>面试岗位<\/span>/)
  assert.match(component, /company_name:\s*editorCompanyName\.value\.trim\(\)/)
  assert.match(component, /role_name:\s*editorRoleName\.value\.trim\(\)/)
  assert.match(component, /await loadTree\(\)/)
})

test('编辑态和预览态使用同一正文宽度且不显示英文装饰眉题', () => {
  assert.match(component, /\.editor-identity-panel,[^]*?\.editor-support-panel\s*\{[^}]*width:\s*min\(900px,\s*100%\)/)
  assert.match(component, /\.editor-layout\s*\{[^}]*width:\s*100%/)
  assert.doesNotMatch(component, /CONTENT EDITOR/)
})

test('蓝色主题完整覆盖编辑归属、正文、字数和补充信息', () => {
  const editorTheme = themeCss.match(
    /\/\* 面经编辑器：[^]*?\*\/([^]*?)(?:\/\* 面经文件导入弹窗|$)/
  )?.[1]

  assert.ok(editorTheme, '应提供独立的面经编辑器蓝色主题覆盖')
  for (const selector of [
    '.editor-identity-panel',
    '.editor-field input',
    '.editor-canvas textarea',
    '.editor-canvas > span em',
    '.editor-support-panel summary'
  ]) {
    assert.match(editorTheme, new RegExp(selector.replaceAll('.', '\\.')))
  }
  assert.match(editorTheme, /color:\s*var\(--ui-accent-ink\)/)
  assert.match(editorTheme, /border-color:\s*var\(--ui-line\)/)
})
