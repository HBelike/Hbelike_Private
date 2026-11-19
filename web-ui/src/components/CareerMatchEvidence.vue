<script setup>
import { TIER_LABELS, STATUS_LABELS } from '../career-match-report.js'
defineProps({ selected: { type: Object, default: null }, modern: Boolean })
const emit = defineEmits(['source', 'manage'])
const symbols = { supported: '✓', partial: '◐', unsupported: '×', needs_confirmation: '?', conflict: '!' }
const percent = value => `${Number((Number(value) * 100).toFixed(1))}%`
const number = value => Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 3 })
const logicLabel = rule => ({ ALL: '全部条件', ANY: '任一条件', AT_LEAST_N: `至少 ${rule?.minimum} 项` })[rule?.operator] || '单项条件'
</script>
<template>
  <section class="match-evidence-content">
              <template v-if="selected">
                <div class="match-evidence-caption"><span>证据与判断</span><span>{{ modern ? TIER_LABELS[selected.tier] : '旧版证据' }}</span></div>
                <h3>{{ selected.title }}</h3><span class="match-status" :class="selected.display_status">{{ symbols[selected.display_status] }} {{ STATUS_LABELS[selected.display_status] }}</span>
                <h4>岗位怎么要求</h4><blockquote v-for="(text, index) in selected.jd_evidence" :key="index">{{ text }}</blockquote><button type="button" class="match-text-button" @click="emit('source', 'jd')">定位岗位原文 →</button>
                <h4>简历怎么写</h4><blockquote v-for="(text, index) in selected.cv_evidence" :key="index">{{ text }}</blockquote><p v-if="!selected.cv_evidence?.length">当前简历未提供对应证据。</p><button v-if="selected.cv_evidence?.length" type="button" class="match-text-button" @click="emit('source', 'cv')">定位简历原文 →</button>
                <h4>判断依据</h4><p>{{ selected.reason }}</p>
                <template v-if="modern"><h4>{{ logicLabel(selected.rule) }}</h4><div v-for="(leaf, index) in selected.leaves" :key="index" class="match-leaf"><span>{{ leaf.label }}</span><span class="match-status" :class="leaf.verdict">{{ symbols[leaf.verdict] }} {{ STATUS_LABELS[leaf.verdict] }}</span></div>
                  <p class="match-calculation">{{ selected.tier === 'hard' ? '按条件逻辑核验，不计入核心分数。' : `覆盖 ${percent(selected.lower)}—${percent(selected.upper)} · 父级权重 ${selected.weight}` }}</p>
                  <div v-for="(leaf, index) in selected.leaves.filter(leaf => leaf.calculation?.source_url)" :key="`source-${index}`" class="match-calculation"><a :href="leaf.calculation.source_url" target="_blank" rel="noopener noreferrer">查看{{ leaf.calculation.edition ? ` QS ${leaf.calculation.edition}` : '院校名单' }}核验来源</a><span v-if="leaf.calculation.verified_on"> · 核验于 {{ leaf.calculation.verified_on }}</span></div>
                  <p v-if="selected.tier !== 'hard'" class="match-calculation">得分贡献：{{ number(selected.lower * selected.weight) }}—{{ number(selected.upper * selected.weight) }} / {{ selected.weight }}；{{ selected.weight_source === 'jd_explicit' ? 'JD 明确权重' : '系统默认等权' }}。</p>
                  <div v-if="selected.questions?.length" class="match-questions"><strong>需要补充的事实</strong><p v-for="question in selected.questions" :key="question">{{ question }}</p><button type="button" class="match-text-button" @click="emit('manage')">管理资料并补充事实 →</button></div>
                </template>
              </template><p v-else class="match-empty">选择一项要求，查看证据与计算依据。</p>
  </section>
</template>
<style scoped>
.match-evidence-content{min-width:0;overflow-wrap:anywhere;font-size:13px;line-height:1.7}.match-evidence-caption,.match-leaf{display:flex;justify-content:space-between;gap:10px;font-size:12px}.match-evidence-caption,.match-calculation{color:var(--ui-text-muted)}h3{margin:8px 0;font-size:17px}h4{margin:18px 0 6px;font-size:13px;font-weight:500;color:var(--ui-text-secondary)}p{margin:6px 0;white-space:pre-wrap}blockquote{margin:8px 0;border-left:2px solid var(--ui-line-strong);padding-left:10px;white-space:pre-wrap}.match-text-button{border:0;background:transparent;color:var(--ui-accent-ink);padding:8px 0;font:inherit;font-size:13px;cursor:pointer}.match-leaf{align-items:baseline;padding:8px 0;border-bottom:1px solid var(--ui-line)}.match-leaf .match-status{flex:none}.match-calculation{font-size:12px}.match-questions{margin-top:16px;border:1px solid var(--ui-line);border-radius:8px;background:var(--ui-surface-soft);padding:12px}.match-status.supported{color:var(--ui-success,#247a5a)}.match-status.partial,.match-status.needs_confirmation{color:var(--ui-warning,#946012)}.match-status.unsupported,.match-status.conflict{color:var(--ui-danger,#b54747)}button:focus-visible{outline:2px solid var(--ui-accent);outline-offset:3px}
@media(max-width:640px){.match-evidence-content{font-size:15px}.match-text-button{min-height:44px}.match-leaf{flex-wrap:wrap}}
</style>
