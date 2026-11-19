export function findSearchCity(cities, name) {
  const normalize = (value) => String(value ?? '').trim().replace(/市$/, '')
  const target = normalize(name)
  return target ? cities.find((city) => normalize(city.name) === target) ?? null : null
}

export function summarizeSearchJobs(jobs = []) {
  const summarizeText = (value, maximum) => Array.from(String(value ?? '')).slice(0, maximum).join('')
  return {
    count: jobs.length,
    jobs: jobs.slice(0, 10).map((job) => ({
      // 标识保留完整内容，优先使用岗位 ID；只对用于展示的文案做摘要。
      id: [job.jobId, job.id, job.securityId].map((value) => String(value ?? '')).find(Boolean) || '',
      title: summarizeText(job.title, 240),
      company: summarizeText(job.company || job.companyShort, 240),
      salary: summarizeText(job.salary, 120), city: summarizeText(job.city, 120),
      ...Object.fromEntries([
        ['experience', job.experience, 120], ['education', job.degree || job.education, 120],
        ['skills', Array.isArray(job.skills) ? job.skills.join('、') : job.skills, 600],
        ['description_excerpt', job.description, 2000]
      ].filter(([, value]) => value != null && String(value).trim())
        .map(([key, value, maximum]) => [key, summarizeText(value, maximum)]))
    }))
  }
}

export function canAutoSearchCard({ fresh, active, payload }) {
  return Boolean(fresh && active && payload?.auto_search
    && payload.status === 'editing' && String(payload.conditions?.keyword ?? '').trim())
}
