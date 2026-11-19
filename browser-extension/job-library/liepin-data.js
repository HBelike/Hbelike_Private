const text = (value) => typeof value === 'string' || typeof value === 'number' ? String(value).trim() : ''

function firstText(...values) {
  for (const value of values) {
    const result = text(value)
    if (result) return result
  }
  return ''
}

function stripMarkup(value) {
  return text(value)
    .replace(/<br\s*\/?\s*>/gi, '\n')
    .replace(/<\/(?:p|div|li|section)\s*>/gi, '\n')
    .replace(/<[^>]*>/g, ' ')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/&quot;/gi, '"')
    .replace(/[ \t]+/g, ' ')
    .replace(/\n\s+/g, '\n')
    .trim()
}

function identifier(value) {
  const result = text(value)
  return /^[\w-]{1,160}$/.test(result) ? result : ''
}

function safeLiepinUrl(value) {
  try {
    const url = new URL(text(value))
    return url.protocol === 'https:' && (url.hostname === 'liepin.com' || url.hostname.endsWith('.liepin.com'))
      ? url.toString()
      : ''
  } catch {
    return ''
  }
}

function parseTextContent(value) {
  if (!Array.isArray(value?.content)) return value
  for (const item of value.content) {
    if (item?.type !== 'text' || typeof item.text !== 'string') continue
    try {
      return JSON.parse(item.text)
    } catch {
      continue
    }
  }
  return value
}

function unwrap(value) {
  let result = parseTextContent(value)
  if (result?.structuredContent && typeof result.structuredContent === 'object') {
    result = result.structuredContent
  }
  if (result?.data && typeof result.data === 'object') result = result.data
  if (result?.result && typeof result.result === 'object') result = result.result
  return result
}

function findRows(value) {
  const result = unwrap(value)
  const candidates = [
    result,
    result?.data,
    result?.result,
    result?.jobList,
    result?.list,
    result?.jobs,
    result?.records,
    result?.items
  ]
  for (const candidate of candidates) {
    if (Array.isArray(candidate)) return { rows: candidate, payload: result }
    if (!candidate || typeof candidate !== 'object') continue
    for (const key of ['jobList', 'list', 'jobs', 'records', 'items', 'resultList']) {
      if (Array.isArray(candidate[key])) return { rows: candidate[key], payload: candidate }
    }
  }
  return { rows: null, payload: result }
}

function normalizeJob(row) {
  const raw = row?.jobInfo && typeof row.jobInfo === 'object' ? { ...row, ...row.jobInfo } : row
  const jobId = identifier(raw?.jobId ?? raw?.id ?? raw?.jobCode ?? raw?.positionId)
  if (!jobId) return null
  const description = stripMarkup(firstText(
    raw.jobDesc,
    raw.jobDescription,
    raw.description,
    raw.jobContent,
    raw.content
  ))
  const skills = Array.isArray(raw.skills)
    ? raw.skills.map(text).filter(Boolean)
    : Array.isArray(raw.jobTags)
      ? raw.jobTags.map((item) => text(item?.name ?? item)).filter(Boolean)
      : []
  const sourceUrl = safeLiepinUrl(firstText(raw.jobUrl, raw.detailUrl, raw.sourceUrl, raw.url))
  const title = firstText(raw.jobName, raw.title, raw.positionName) || '未命名岗位'
  const company = firstText(raw.companyName, raw.compName, raw.company?.name, typeof raw.company === 'string' ? raw.company : '')
  const address = firstText(raw.address, raw.cityName, raw.location, raw.workAddress)
  return {
    platform: 'liepin',
    id: `liepin:${jobId}`,
    jobId,
    jobKey: `liepin:${jobId}`,
    title,
    salary: firstText(raw.salaryDesc, raw.salary, raw.salaryRange, raw.salaryText) || '薪资面议',
    experience: firstText(raw.workExperience, raw.experience, raw.workYear, raw.expRequirement) || '经验不限',
    degree: firstText(raw.eduLevel, raw.education, raw.degree, raw.educationRequirement) || '学历不限',
    city: address,
    district: firstText(raw.district, raw.areaName),
    address,
    company: company || '公司信息待补充',
    companyShort: company || '公司',
    companyLogo: firstText(raw.companyLogo, raw.logo),
    industry: firstText(raw.industry, raw.industryName),
    scale: firstText(raw.companyScale, raw.compScale, raw.scale),
    stage: firstText(raw.financingStage, raw.companyStage, raw.stage),
    recruiter: firstText(raw.recruiterName, raw.hrName, raw.recruiter),
    recruiterOnline: false,
    recruiterAvatar: firstText(raw.recruiterAvatar, raw.hrAvatar),
    skills,
    labels: Array.isArray(raw.labels) ? raw.labels.map(text).filter(Boolean) : [],
    welfare: Array.isArray(raw.welfare) ? raw.welfare.map(text).filter(Boolean) : [],
    description,
    sourceUrl,
    adapterData: { jobKind: firstText(raw.jobKind, raw.jobType) },
    fetchedAt: new Date().toISOString()
  }
}

export function buildLiepinSearchPayload(query, page = 1, cityName = '') {
  const jobName = text(query).slice(0, 80)
  if (!jobName) throw new Error('岗位名称不能为空。')
  return {
    jobName,
    address: text(cityName).slice(0, 80) || undefined,
    page: Math.max(0, Math.min(99, Math.floor(Number(page) || 1) - 1))
  }
}

export function classifyLiepinFailure(response, status = 0) {
  const result = unwrap(response)
  const code = text(result?.code ?? result?.error?.code ?? result?.status)
  const message = firstText(result?.message, result?.msg, result?.error?.message, result?.error)
  if (status === 401 || status === 403 || /token|auth|授权|认证|登录/i.test(`${code} ${message}`)) {
    return { code: 'liepin_authorization_required', message: message || '猎聘授权已失效，请重新配置官方授权。' }
  }
  if (status === 429 || /频繁|限流|rate.?limit/i.test(`${code} ${message}`)) {
    return { code: 'liepin_rate_limited', message: message || '猎聘限制了当前搜索频率，请稍后再试。' }
  }
  if (result?.isError === true || result?.error || (code && !['0', '0000', '200', 'success', 'SUCCESS'].includes(code))) {
    return { code: 'liepin_request_failed', message: message || '猎聘未能完成职位搜索。' }
  }
  return null
}

export function normalizeLiepinSearchResponse(response, context = {}) {
  const failure = classifyLiepinFailure(response)
  if (failure) throw Object.assign(new Error(failure.message), { code: failure.code })
  const { rows, payload } = findRows(response)
  if (!rows) throw Object.assign(new Error('猎聘返回的数据结构无法识别，本次未展示岗位。'), { code: 'liepin_response_invalid' })
  const jobs = rows.map(normalizeJob).filter(Boolean)
  const page = Math.max(1, Math.floor(Number(context.page) || 1))
  const total = Number(payload?.total ?? payload?.totalCount ?? payload?.totalNum)
  const pageSize = Number(payload?.pageSize ?? payload?.size) || Math.max(jobs.length, 1)
  const hasMore = typeof payload?.hasMore === 'boolean'
    ? payload.hasMore
    : typeof payload?.has_more === 'boolean'
      ? payload.has_more
      : Number.isFinite(total) && total > page * pageSize
  return {
    platform: 'liepin',
    query: text(context.query),
    city: text(context.cityName),
    page,
    jobs,
    hasMore: Boolean(hasMore),
    rawJobCount: rows.length,
    fetchedAt: new Date().toISOString()
  }
}
