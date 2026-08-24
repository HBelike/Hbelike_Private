import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import {
  BOSS_EXTENSION_DOWNLOAD_URL,
  BOSS_EXTENSION_GUIDE_URL,
  BOSS_EXTENSION_VERSION,
  bossExtensionGuideUrl,
  normalizeBossExtensionConnection
} from './boss-extension-onboarding.js'

const manifestUrl = new URL('../../browser-extension/job-library/manifest.json', import.meta.url)

test('扩展下载地址与真实 manifest 版本保持一致', async () => {
  const manifest = JSON.parse(await readFile(manifestUrl, 'utf8'))
  assert.equal(BOSS_EXTENSION_VERSION, manifest.version)
  assert.equal(
    BOSS_EXTENSION_DOWNLOAD_URL,
    `/downloads/find-job-boss-helper-v${manifest.version}.zip`
  )
})

test('连接结果保留当前浏览器扩展版本', () => {
  assert.deepEqual(normalizeBossExtensionConnection({ connected: true, version: '0.2.2' }), {
    status: 'ready',
    version: '0.2.2'
  })
  assert.deepEqual(normalizeBossExtensionConnection(null), {
    status: 'missing',
    version: ''
  })
})

test('Chrome 与 Edge 使用同一教程页的独立步骤锚点', () => {
  assert.equal(BOSS_EXTENSION_GUIDE_URL, '/boss-extension-guide.html')
  assert.equal(bossExtensionGuideUrl('chrome'), '/boss-extension-guide.html#chrome')
  assert.equal(bossExtensionGuideUrl('edge'), '/boss-extension-guide.html#edge')
})
