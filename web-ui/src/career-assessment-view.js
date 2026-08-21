const LEGACY_KEYS = ['skill_coverage', 'experience_coverage', 'project_relevance', 'critical_gap']

export function isAssessmentPending(assessment) {
  return ['queued', 'analyzing'].includes(assessment?.status)
}

export function buildAssessmentCards(assessment) {
  if (!assessment || !['ready', 'fallback_ready'].includes(assessment.status)) return []
  if (Array.isArray(assessment.dimensions)) {
    return assessment.dimensions
      .filter((item) => item?.status === 'ready' && item.score !== null)
      .map((item) => ({
        key: item.id,
        short: item.label,
        description: item.description,
        ...item
      }))
  }
  const dimensions = assessment.dimensions ?? {}
  return LEGACY_KEYS
    .filter((key) => dimensions[key])
    .map((key) => ({
      key,
      short: dimensions[key].label,
      description: legacyDescription(key),
      ...dimensions[key]
    }))
}

export function itemsForDimension(assessment, dimensionId) {
  if (!assessment) return []
  if (Array.isArray(assessment.items)) {
    if (dimensionId === 'critical_gap') {
      return assessment.items.filter((item) => item.requirement_type === 'required' && item.factor !== 1)
    }
    return assessment.items.filter((item) => item.dimension_id === dimensionId)
  }
  const requirements = assessment.requirements ?? []
  if (dimensionId === 'critical_gap') {
    return requirements.filter((item) => item.priority === 'must' && item.match_factor !== 1)
  }
  const category = {
    skill_coverage: 'skill',
    experience_coverage: 'experience',
    project_relevance: 'project'
  }[dimensionId]
  return requirements.filter((item) => (item.dimensions ?? [item.category]).includes(category))
}

export function assessmentCanvasSections(assessment) {
  if (assessment?.algorithm_version !== 'llm-judge-v1' || !Array.isArray(assessment.job_sections)) return null
  const sections = assessment.job_sections
  const items = (...categories) => sections
    .filter((section) => categories.includes(section.category))
    .flatMap((section) => section.items ?? [])
  const supporting = [
    { key: 'work_condition', label: '工作条件', items: items('work_condition') },
    { key: 'compensation_benefit', label: '薪资福利', items: items('compensation_benefit') },
    { key: 'company_information', label: '公司信息', items: items('company_information') },
    { key: 'other', label: '其他信息', items: items('other') }
  ].filter((section) => section.items.length)
  return {
    responsibilities: items('responsibility'),
    requirements: [
      ...items('required_qualification', 'experience_condition', 'education_condition', 'credential_condition').map((text) => ({ text, type: '必须' })),
      ...items('preferred_qualification').map((text) => ({ text, type: '加分' }))
    ],
    supporting,
    hasStructure: sections.length > 0
  }
}

function legacyDescription(key) {
  return {
    skill_coverage: '简历证据覆盖了多少项岗位技能要求。',
    experience_coverage: '工作年限、教育背景等经验条件的达成情况。',
    project_relevance: '过往项目与岗位业务场景的关联程度。',
    critical_gap: '必须项中仍缺少明确简历证据的比例。'
  }[key] ?? '查看该项分析所依据的岗位要求和简历证据。'
}
