import { installApiFixtures } from './api-fixtures.js'

export const mobileUserAgent = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1'

const skill = { id: 'skill-1', name: '示例技能', description_zh: '用于验证阅读、编辑与保存状态。',
  source_label: '项目技能', editable: true, path_hint: 'skills/example/SKILL.md' }
const experience = { id: 'experience-1', company_name: '示例科技', role_name: 'Agent 工程师面经',
  markdown_content: '# 项目问答\n\n请说明项目职责与技术取舍。', tags: ['工程'], sources: [], status: 'ready', can_write: true,
  updated_at: '2026-11-06T06:00:00Z', source_type: 'manual', summary_text: '项目与工程问题。' }

export async function installPlatformFixtures(page, { role = 'admin' } = {}) {
  await installApiFixtures(page)
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/auth/me') return route.fulfill({ json: { user: { id: 'ui-user', display_name: '界面验收', role } } })
    if (path === '/api/navigation/modules') return route.fulfill({ json: { items: [
      'career_assistant','workbench','resume_assistant','interview_library','job_library','skill_library',
      'evaluation_center','langsmith','admin_console','career_interview_master','career_online_assessment'
    ].map(key => ({ key, enabled: true, accessible: role === 'admin' || !['evaluation_center','langsmith','admin_console'].includes(key) })) } })
    if (path === '/api/career/model-profiles') return route.fulfill({ json: { items: [{ readiness: 'ready', profile: {
      id: 'model-1', provider_key: 'deepseek', model_id: 'deepseek-v4-pro', display_name: '界面验收模型', capabilities: ['text'], cost_tier: 'paid'
    } }] } })
    if (path === '/api/career/interview-library/tree') return route.fulfill({ json: { items: [{ id: 'company-1', label: '示例科技', children: [{ ...experience }] }] } })
    if (path === '/api/career/interview-library/experiences/experience-1') return route.fulfill({ json: experience })
    if (path.startsWith('/api/career/')) return route.fallback()
    if (path === '/api/skills') return route.fulfill({ json: { items: [skill] } })
    if (path === '/api/skills/skill-1') return route.fulfill({ json: { skill, markdown: '# 技能说明\n\n这是可编辑的测试内容。' } })
    if (path === '/api/observability/status') return route.fulfill({ json: { enabled: false, project: 'local-ui', ui_url: 'https://smith.langchain.com' } })
    return route.fulfill({ json: { items: [], summary: {}, source_distribution: [], experiments: [], config: {}, content: null } })
  })
}

export async function openFixtureConversation(page) {
  const history = page.locator('.history-trigger')
  if (await history.isVisible()) await history.click()
  await page.locator('.conversation-item-main').first().click()
  await page.getByRole('textbox', { name: '求职咨询内容' }).waitFor()
}
