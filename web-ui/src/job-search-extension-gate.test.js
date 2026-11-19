import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const componentUrl = new URL('./components/JobSearchWorkspace.vue', import.meta.url)

test('岗位检测统一走连接控制器，安装说明只通过明确点击打开', async () => {
  const source = await readFile(componentUrl, 'utf8')

  assert.match(source, /createJobLibraryConnection/)
  assert.match(source, /subscribeJobLibraryConnection\(handleConnectionSignal\)/)
  assert.match(source, /await connection\.check\(\)/)
  assert.match(source, /checkBridge\(\{ loadCatalog: false \}\)/)
  const check = source.slice(source.indexOf('async function checkBridge('), source.indexOf('function refreshForExtensionCheck'))
  assert.doesNotMatch(check, /installDialogOpen\.value = true/)
  assert.match(source, /@click="extensionDialogMode = 'install'; installDialogOpen = true">查看安装说明/)
  assert.match(source, /unsubscribeConnection\(\)/)
  assert.match(source, /connection\.dispose\(\)/)
})
