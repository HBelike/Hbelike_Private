// 匹配报告展示适配：分数直接来自服务端，旧版不转换成新版算法。
export const TIER_LABELS = { hard: '硬性条件', core: '核心要求', bonus: '加分项' }
export const STATUS_LABELS = { supported: '满足', partial: '部分满足', unsupported: '不满足', needs_confirmation: '待确认', conflict: '资料冲突' }
export const RELATION_LABELS = { direct: '直接相关', transferable: '可迁移', mention_only: '仅提及', none: '无相关证据' }
export const isMatchV2 = assessment => assessment?.algorithm_version === 'jd-evidence-rules-v2'

export function formatMatchScore(metric) {
  if (metric?.display_value) return metric.display_value
  if (metric?.status !== 'ready' || metric?.score == null) return '暂不可计算'
  const number = value => Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 1 })
  return metric.score_upper != null && metric.score_upper !== metric.score
    ? `${number(metric.score)}–${number(metric.score_upper)}%`
    : `${number(metric.score)}%`
}

export function matchCards(assessment) {
  const summaries = assessment?.summaries || {}
  return Object.entries(TIER_LABELS).map(([key, short]) => {
    const metric = summaries[key] || { status: 'insufficient_data', score: null }
    let displayValue
    if (key === 'hard') displayValue = ({ supported: '条件均满足', unsupported: '存在明确差距', needs_confirmation: '条件待核实', not_specified: 'JD 未列明' })[metric.verdict] || '待分析'
    if (key === 'bonus') displayValue = metric.item_count ? `${metric.supported_count} / ${metric.item_count} 项` : 'JD 未列明'
    return { ...metric, id: key, key, short: key === 'core' ? '核心要求匹配度' : short, display_value: displayValue,
      description: key === 'hard' ? '逐项核验准入条件，加分不能抵消差距。' : key === 'core' ? '已支持下限至待确认上限，点击核对证据。' : '单独展示 JD 明确的优先条件。' }
  })
}

export function reportItems(assessment) {
  if (isMatchV2(assessment)) return assessment.items || []
  // 旧版没有硬门槛/核心的区别，保留“旧版必须项”，不声称已完成新版核验。
  const source = Array.isArray(assessment?.items) ? assessment.items : assessment?.requirements || []
  return source.map((item, index) => {
    const factor = item.factor ?? item.match_factor
    const verdict = item.verdict || (factor === 1 ? 'supported' : factor === 0.5 ? 'partial' : 'needs_confirmation')
    const evidence = item.cv_evidence || (item.evidence ? [item.evidence] : [])
    return { ...item, id: item.id || `legacy-${index}`, title: item.title || item.jd_evidence?.join('；') || item.text,
      tier: item.requirement_type === 'preferred' || item.priority === 'bonus' ? 'bonus' : 'core',
      display_status: verdict, verdict, cv_evidence: evidence, jd_evidence: item.jd_evidence || [item.text].filter(Boolean),
      lower: factor, upper: factor, leaves: [], questions: [],
      reason: item.reason || '旧版报告未提供细分判断规则，可重新分析生成新版报告。' }
  })
}

export function filterReportItems(items, { tier = 'all', dimension = '', status = 'all' } = {}) {
  return items.filter(item => (tier === 'all' || item.tier === tier)
    && (!dimension || item.dimension_id === dimension || item.dimensions?.includes(dimension))
    && (status === 'all' || item.display_status === status))
}
