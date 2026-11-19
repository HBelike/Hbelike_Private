import { test, expect } from '@playwright/test'
import { installPlatformFixtures, openFixtureConversation, mobileUserAgent } from './support/platform-fixtures.js'

const jobUrl = 'https://jobs.example.org/#/role/123?city=beijing'

async function receiveShare(page, payload = {}, { install = true } = {}) {
  if (install) await installPlatformFixtures(page)
  await page.goto('/career')
  await page.waitForFunction(() => Boolean(navigator.serviceWorker?.controller))
  const redirect = await page.evaluate(async payload => {
    const form = new FormData()
    for (const field of ['title', 'text', 'url']) if (payload[field]) form.set(field, payload[field])
    for (const file of payload.files || []) form.append('files', new File([file.body], file.name, { type: file.type || 'application/octet-stream' }))
    const response = await fetch('/share-target', { method: 'POST', body: form })
    return { url: response.url, redirected: response.redirected, status: response.status }
  }, payload)
  expect(redirect.redirected).toBe(true)
  expect(redirect.status).toBe(200)
  expect(redirect.url).toContain('/career/import?share=')
  await page.goto(redirect.url)
  await expect(page.getByRole('heading', { name: '导入分享资料', exact: true })).toBeVisible()
  return redirect.url
}

async function openSharedSheet(page) {
  await page.getByRole('button', { name: '前往对话校对', exact: true }).click()
  await expect(page.getByRole('region', { name: '待导入分享资料' })).toBeVisible()
  await expect(page.getByRole('button', { name: '导入到当前会话', exact: true })).toBeDisabled()
  await openFixtureConversation(page)
  await page.getByRole('button', { name: '导入到当前会话', exact: true }).click()
  return page.getByRole('dialog', { name: '建立求职上下文', exact: true })
}

test('PC 真实分享 POST 经过 303 接收 Android text 链接，明确选择会话后移交', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const writes = []
  page.on('request', request => {
    if (request.method() === 'POST' && request.url().includes('/api/career/')) writes.push(request.url())
  })
  await receiveShare(page, { title: '工程师职位', text: `推荐一个职位 ${jobUrl}` })
  await expect(page.getByLabel('选择具体职位链接')).toHaveValue(jobUrl)
  await page.screenshot({ path: '../tmp/regression-20261109/pwa-share-pc.png', fullPage: true })
  const sheet = await openSharedSheet(page)
  await expect(sheet.getByLabel('职位详情链接')).toHaveValue(jobUrl)
  await expect(sheet.getByRole('button', { name: '解析链接', exact: true })).toBeEnabled()
  await expect(sheet.locator('.target-meta-grid input').first()).toHaveValue('')
  expect(writes).toEqual([])
})

test('PC 分享简历文件保持原始字节，点击解析前零上传', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const body = '%PDF-1.4 PWA-original-file-bytes'
  let uploaded = ''
  await installPlatformFixtures(page)
  await page.route('**/api/career/candidate-profiles/parse', route => {
    uploaded = route.request().postDataBuffer().toString()
    return route.fulfill({ json: { resume_outline: '真实个人经历', source_character_count: 6, sections: [] } })
  })
  await receiveShare(page, { files: [{ name: 'shared-resume.pdf', body }] }, { install: false })
  await page.getByRole('radio', { name: /个人简历/ }).check()
  const sheet = await openSharedSheet(page)
  await expect(sheet.locator('.file-drop')).toContainText('shared-resume.pdf')
  expect(uploaded).toBe('')
  await sheet.getByRole('button', { name: '解析简历', exact: true }).click()
  await expect(sheet.getByRole('textbox', { name: '基准简历解析预览', exact: true })).toHaveValue('真实个人经历')
  expect(uploaded).toContain(body)
})

test('PC 登录和刷新后仍回到原分享材料，普通登录路由不受影响', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await installPlatformFixtures(page)
  let loggedIn = false
  await page.route('**/api/auth/me', route => route.fulfill(loggedIn
    ? { json: { user: { id: 'share-user', display_name: '分享验收', role: 'admin' } } }
    : { status: 401, json: {} }))
  await page.route('**/api/auth/login', route => { loggedIn = true; return route.fulfill({ json: { user: { id: 'share-user', display_name: '分享验收', role: 'admin' } } }) })
  // 接收分享时处于未登录页面，Service Worker 仍能接收文件和文字。
  await page.goto('/career')
  await page.waitForFunction(() => Boolean(navigator.serviceWorker?.controller))
  const location = await page.evaluate(async url => {
    const form = new FormData()
    form.set('text', url)
    return (await fetch('/share-target', { method: 'POST', body: form })).url
  }, jobUrl)
  await page.goto(location)
  await expect(page).toHaveURL(/\/login\?share=/)
  await page.reload()
  await page.getByRole('tab', { name: '密码登录', exact: true }).click()
  await page.getByLabel('邮箱', { exact: true }).fill('share@example.com')
  await page.getByLabel('密码', { exact: true }).fill('fixture-password')
  await page.locator('.login-submit').click()
  await expect(page).toHaveURL(/\/career\/import\?share=/)
  await expect(page.getByLabel('选择具体职位链接')).toHaveValue(jobUrl)
  const query = new URL(page.url()).search
  for (const route of ['/register', '/forgot-password']) {
    await page.goto(`${route}${query}`)
    await expect(page).toHaveURL(/\/career\/import\?share=/)
    await expect(page.getByLabel('选择具体职位链接')).toHaveValue(jobUrl)
  }
})

test('PC 多个链接需要选择，取消后不能重新读取已丢弃材料', async ({ page }) => {
  const location = await receiveShare(page, { text: `${jobUrl}\nhttps://jobs.example.org/another` })
  await expect(page.getByRole('button', { name: '前往对话校对', exact: true })).toBeDisabled()
  await page.getByLabel('选择具体职位链接').selectOption(jobUrl)
  await expect(page.getByRole('button', { name: '前往对话校对', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: '取消导入', exact: true }).click()
  await expect(page).toHaveURL(/\/career$/)
  await page.goto(location)
  await expect(page.getByRole('alert')).toContainText('已处理或已过期')
})

test('PC 多文件不静默丢弃，退出登录清理未处理材料', async ({ page }) => {
  const location = await receiveShare(page, { files: [{ name: 'a.png', body: 'one' }, { name: 'b.png', body: 'two' }] })
  await expect(page.getByRole('alert')).toContainText('只能导入一个文件')
  await expect(page.getByRole('button', { name: '前往对话校对', exact: true })).toHaveCount(0)
  await page.locator('.account-breadcrumb').click()
  await page.getByRole('menuitem', { name: '个人信息 查看账户与权限', exact: true }).click()
  await page.getByRole('button', { name: '退出登录', exact: true }).click()
  await expect(page).toHaveURL(/\/login$/)
  // 模拟重新登录后打开先前分享地址，临时记录已清除。
  await page.goto(location)
  await expect(page.getByRole('alert')).toContainText('已处理或已过期')
})

test('PC 安装说明入口沿用模块权限，未安装仍可手动导入', async ({ page }) => {
  await installPlatformFixtures(page)
  await page.goto('/career')
  await page.locator('.account-breadcrumb').click()
  await page.getByRole('menuitem', { name: '手机分享导入 安装与分享使用方法', exact: true }).click()
  await expect(page).toHaveURL(/\/career\/import$/)
  await expect(page.getByRole('heading', { name: '从手机浏览器分享职位' })).toBeVisible()
  await page.getByRole('button', { name: '返回求职助手，手动导入', exact: true }).click()
  await expect(page).toHaveURL(/\/career$/)
})

test('PC 普通用户无求职模块权限时不能访问分享导入页', async ({ page }) => {
  await installPlatformFixtures(page, { role: 'user' })
  await page.route('**/api/navigation/modules', route => route.fulfill({ json: { items: [{ key: 'skill_library', enabled: true, accessible: true }] } }))
  await page.goto('/career/import')
  await expect(page).toHaveURL(/\/skills$/)
  await expect(page.getByRole('heading', { name: '导入分享资料', exact: true })).toHaveCount(0)
  await page.locator('.account-breadcrumb').click()
  await expect(page.getByRole('menuitem', { name: '手机分享导入 安装与分享使用方法', exact: true })).toHaveCount(0)
})

test('同路由切换分享材料和浏览器返回时，正文与当前分享地址一致', async ({ page }) => {
  const first = await receiveShare(page, { title: '第一份分享', text: jobUrl })
  await receiveShare(page, { title: '第二份分享', text: 'https://jobs.example.org/second' })
  await page.evaluate(location => {
    history.pushState({}, '', location)
    dispatchEvent(new PopStateEvent('popstate'))
  }, first)
  await expect(page.locator('.share-received strong')).toHaveText('第一份分享')
  await page.goBack()
  await expect(page.locator('.share-received strong')).toHaveText('第二份分享')
})

test('PC 无效格式、超大文件和空分享给出回退，不保存部分内容', async ({ page }) => {
  for (const [payload, reason] of [
    [{ files: [{ name: 'resume.exe', body: 'unsupported' }] }, '仅支持 PDF'],
    [{ files: [{ name: 'resume.pdf', body: 'x'.repeat(10 * 1024 * 1024 + 1) }] }, '超过 10MB'],
    [{}, '没有收到可导入的内容']
  ]) {
    await test.step(reason, async () => {
      await receiveShare(page, payload)
      await expect(page.getByRole('alert')).toContainText(reason)
      await expect(page.getByRole('button', { name: '前往对话校对', exact: true })).toHaveCount(0)
    })
  }
})

test.describe('手机分享接收', () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, userAgent: mobileUserAgent })
  test('长链接和文字在短屏可读，移交后跨模块保留，原请求不缓存', async ({ page }) => {
    await receiveShare(page, { title: '手机端岗位资料', text: `岗位职责：负责系统开发和维护。任职要求：熟悉 Python 与 SQL。\n${jobUrl}` })
    for (const viewport of [{ width: 390, height: 844 }, { width: 390, height: 430 }, { width: 844, height: 390 }]) {
      await page.setViewportSize(viewport)
      const panel = page.locator('.material-share-page')
      expect(await panel.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true)
      await page.getByRole('button', { name: '前往对话校对', exact: true }).scrollIntoViewIfNeeded()
      await expect(page.getByRole('button', { name: '前往对话校对', exact: true })).toBeInViewport({ ratio: 1 })
    }
    await page.setViewportSize({ width: 390, height: 844 })
    await page.locator('.material-share-page').evaluate(el => {
      for (let parent = el; parent; parent = parent.parentElement) parent.scrollTop = 0
    })
    await page.screenshot({ path: '../tmp/regression-20261109/pwa-share-mobile.png', fullPage: true })
    await page.getByRole('button', { name: '前往对话校对', exact: true }).scrollIntoViewIfNeeded()
    await page.screenshot({ path: '../tmp/regression-20261109/pwa-share-mobile-actions.png', fullPage: true })
    const sheet = await openSharedSheet(page)
    await expect(sheet.getByLabel('职位详情链接')).toHaveValue(jobUrl)
    await sheet.getByRole('button', { name: '关闭', exact: true }).click()
    for (const name of ['技能库', '求职助手']) {
      await page.getByRole('button', { name: '打开导航', exact: true }).click()
      await page.locator('#app-navigation .nav-item').filter({ hasText: name }).click()
    }
    await page.locator('.composer-material-button').filter({ hasText: '目标岗位' }).click()
    await expect(sheet.getByLabel('职位详情链接')).toHaveValue(jobUrl)
    expect(await page.evaluate(async () => (await caches.keys()).length)).toBe(0)
  })

  test('移交分享资料前保护当前未确认草稿，取消替换后仍可继续', async ({ page }) => {
    await receiveShare(page, { text: jobUrl })
    await page.getByRole('button', { name: '前往对话校对', exact: true }).click()
    await openFixtureConversation(page)
    const tools = page.getByRole('button', { name: '资料与工具', exact: true })
    if (await tools.isVisible()) await tools.click()
    await page.locator('.active-context-chip').click()
    const sheet = page.getByRole('dialog', { name: '建立求职上下文', exact: true })
    await sheet.getByRole('button', { name: '保留当前简历', exact: true }).click()
    const draft = '未确认的原岗位正文，不得被分享资料静默覆盖。'
    await sheet.getByRole('textbox', { name: '职位信息正文', exact: true }).fill(draft)
    await sheet.getByRole('button', { name: '关闭', exact: true }).click()
    page.once('dialog', dialog => dialog.dismiss())
    await page.getByRole('button', { name: '导入到当前会话', exact: true }).click()
    await expect(sheet).toHaveCount(0)
    await expect(page.getByRole('region', { name: '待导入分享资料' })).toBeVisible()
    await page.locator('.composer-material-button').filter({ hasText: '目标岗位' }).click()
    await expect(sheet.getByRole('textbox', { name: '职位信息正文', exact: true })).toHaveValue(draft)
    await sheet.getByRole('button', { name: '关闭', exact: true }).click()
    page.once('dialog', dialog => dialog.accept())
    await page.getByRole('button', { name: '导入到当前会话', exact: true }).click()
    await expect(sheet.getByLabel('职位详情链接')).toHaveValue(jobUrl)
    await expect(page.getByRole('region', { name: '待导入分享资料' })).toHaveCount(0)
  })
})
