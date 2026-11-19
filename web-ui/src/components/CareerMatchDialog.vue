<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import CareerMatchEvidence from './CareerMatchEvidence.vue'
import { buildAssessmentCards } from '../career-assessment-view.js'
import { filterReportItems, formatMatchScore, isMatchV2, RELATION_LABELS, reportItems, STATUS_LABELS, TIER_LABELS } from '../career-match-report.js'

const props = defineProps({
  assessment: { type: Object, default: null },
  retrying: { type: Boolean, default: false },
  retryError: { type: String, default: '' },
  currentStatus: { type: String, default: '' },
  showingPrevious: { type: Boolean, default: false },
  candidate: { type: Object, default: null },
  targetRole: { type: Object, default: null },
  initialMetric: { type: String, default: '' }
})
const emit = defineEmits(['close', 'retry', 'manage'])
const dialog = ref(null)
const tab = ref('compare')
const tier = ref('all')
const dimension = ref('')
const status = ref('all')
const selectedId = ref('')
const sourceFocus = ref([])
const modern = computed(() => isMatchV2(props.assessment))
const pending = computed(() => props.retrying || ['queued', 'analyzing'].includes(props.currentStatus || props.assessment?.status))
const cards = computed(() => buildAssessmentCards(props.assessment))
const items = computed(() => reportItems(props.assessment))
const dimensions = computed(() => (Array.isArray(props.assessment?.dimensions) ? props.assessment.dimensions : []).filter(x => x.status === 'ready' && x.item_count && x.id !== 'critical_gap'))
const visibleItems = computed(() => {
  if (!modern.value && dimension.value === 'critical_gap') return items.value.filter(x => x.tier === 'core' && x.lower !== 1)
  return filterReportItems(items.value, { tier: tier.value, dimension: dimension.value, status: status.value })
})
const selected = computed(() => items.value.find(x => x.id === selectedId.value))
const originalLines = computed(() => {
  const source = props.assessment?.source_lines?.[tab.value]
  if (source) return Object.entries(source).map(([id, text]) => ({ id, text }))
  if (tab.value === 'jd' && props.targetRole?.job_text) return props.targetRole.job_text.split(/\n+/).filter(Boolean).map((text, index) => ({ id: `JD-${String(index + 1).padStart(3, '0')}`, text }))
  const key = tab.value === 'jd' ? 'jd_evidence' : 'cv_evidence'
  return [...new Set(items.value.flatMap(x => x[key] || []))].map((text, index) => ({ id: `旧版片段-${index + 1}`, text }))
})
const symbols = { supported: '✓', partial: '◐', unsupported: '×', needs_confirmation: '?', conflict: '!' }
const number = value => Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 3 })

function chooseMetric(key) {
  tab.value = 'compare'
  tier.value = key in TIER_LABELS ? key : 'all'
  dimension.value = key in TIER_LABELS ? '' : key
  status.value = 'all'
}
function chooseTier(key) { tier.value = key; dimension.value = '' }
function chooseDimension(key) {
  tier.value = 'core'
  dimension.value = dimension.value === key ? '' : key
}
function close() { dialog.value?.close() }
async function showSource(kind) {
  sourceFocus.value = selected.value?.[`${kind}_source_ids`] || []
  tab.value = kind
  await nextTick()
  dialog.value?.querySelector('.match-original.is-highlighted')?.scrollIntoView({ block: 'nearest' })
}
function retry() { emit('retry') }
function manage() { close(); emit('manage') }
watch(visibleItems, rows => {
  if (!rows.some(x => x.id === selectedId.value)) selectedId.value = rows[0]?.id || ''
}, { immediate: true })
watch(() => props.initialMetric, key => chooseMetric(key), { immediate: true })
let previousOverflow = ''
onMounted(async () => {
  previousOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  await nextTick()
  dialog.value?.showModal()
})
onBeforeUnmount(() => { document.body.style.overflow = previousOverflow })
</script>

<template>
  <Teleport to="body">
    <dialog ref="dialog" class="career-match-dialog" aria-labelledby="match-dialog-title" @close="emit('close')" @keydown.esc.stop.prevent="close" @cancel.prevent="close" @click="event => { if (event.target === dialog) close() }">
      <div class="match-window">
        <header class="match-header">
          <div><h2 id="match-dialog-title">岗位匹配分析</h2><p>{{ targetRole?.role_name || '目标岗位' }} × {{ candidate?.display_name || '基准简历' }}<span v-if="candidate?.version"> · 简历版本 {{ candidate.version }}</span></p></div>
          <div class="match-header-actions"><button type="button" @click="retry" :disabled="pending">{{ pending ? '分析中…' : '重新分析' }}</button><button type="button" class="match-close" aria-label="关闭岗位匹配分析" autofocus @click="close">×</button></div>
        </header>
        <p v-if="retryError" class="match-retry-error" role="alert">{{ retryError }}。当前仍显示上一次报告，可稍后重试。</p>
        <p v-if="showingPrevious" class="match-previous-report" role="status">上一次成功报告 · 简历 v{{ candidate?.version || '未知' }}／岗位 v{{ targetRole?.version || '未知' }}。当前展示旧结果，待本次分析成功后更新。</p>
        <div class="match-scroll">
          <p v-if="pending" class="match-notice" role="status">正在分析当前 JD 与简历，完成后将自动更新。</p>
          <p v-else-if="(currentStatus || assessment?.status) === 'failed'" class="match-notice" role="status">本次分析未完成，请重新分析。</p>
          <p v-else-if="!modern" class="match-notice">这是旧版{{ assessment?.status === 'fallback_ready' ? '基础规则' : '' }}报告，分数沿用旧算法。重新分析后可查看硬性条件、核心要求和加分项的独立结果。</p>
          <div class="match-summaries" aria-label="匹配结果摘要">
            <button v-for="metric in cards" :key="metric.key" type="button" @click="chooseMetric(metric.key)">
              <span>{{ metric.short }}</span><strong>{{ formatMatchScore(metric) }}</strong>
              <small v-if="metric.key === 'hard'">{{ metric.supported_count || 0 }} 项满足 · {{ metric.unsupported_count || 0 }} 项不满足 · {{ (metric.item_count || 0) - (metric.supported_count || 0) - (metric.unsupported_count || 0) }} 项待核实</small>
              <small v-else>{{ metric.item_count || 0 }} 项要求 · {{ metric.key === 'bonus' ? '不抵消硬性条件差距' : modern ? '依据当前简历证据' : '旧版算法' }}</small>
            </button>
          </div>
          <nav class="match-tabs" aria-label="报告内容">
            <button v-for="entry in [{ key: 'compare', label: '要求对照' }, { key: 'jd', label: 'JD 原文' }, { key: 'cv', label: modern ? '简历原文' : '简历证据片段' }, { key: 'rules', label: '计算规则' }]" :key="entry.key" type="button" :aria-pressed="tab === entry.key" @click="tab = entry.key">{{ entry.label }}</button>
          </nav>
          <div class="match-content">
            <section class="match-primary" aria-label="岗位要求对照">
              <template v-if="tab === 'compare'">
                <div v-if="modern && dimensions.length" class="match-dimensions" aria-label="核心维度匹配度">
                  <button v-for="metric in dimensions" :key="metric.id" type="button" :aria-pressed="dimension === metric.id" @click="chooseDimension(metric.id)"><span>{{ metric.label }}</span><span class="match-bar" aria-hidden="true"><i class="match-upper" :style="{ width: `${metric.score_upper}%` }"></i><i :style="{ width: `${metric.score}%` }"></i></span><span>{{ formatMatchScore(metric) }}</span></button>
                </div>
                <div class="match-filters">
                  <div aria-label="要求类别"><button v-for="[key, label] in [['all', '全部'], ...Object.entries(TIER_LABELS)]" :key="key" type="button" :aria-pressed="tier === key" @click="chooseTier(key)">{{ label }}</button></div>
                  <label>状态 <select v-model="status" aria-label="筛选匹配状态"><option value="all">全部状态</option><option v-for="[key, label] in Object.entries(STATUS_LABELS)" :key="key" :value="key">{{ label }}</option></select></label>
                </div>
                <p v-if="dimension" class="match-dimension-filter">当前维度：{{ dimensions.find(x => x.id === dimension)?.label || cards.find(x => x.key === dimension)?.short || dimension }} <button type="button" @click="dimension = ''">清除筛选</button></p>
                <table class="match-table"><thead><tr><th scope="col">JD 要求</th><th scope="col">简历事实与证据</th><th scope="col">判断</th></tr></thead><tbody>
                  <tr v-for="item in visibleItems" :key="item.id" :class="{ 'is-selected': selectedId === item.id }">
                    <td><button type="button" :aria-pressed="selectedId === item.id" @click="selectedId = item.id">{{ item.title }}</button><small>{{ modern ? TIER_LABELS[item.tier] : '旧版要求' }}</small></td>
                    <td class="match-evidence-cell"><p>{{ item.cv_evidence?.join('；') || '当前简历没有对应证据' }}</p><small v-if="item.leaves?.length">{{ [...new Set(item.leaves.map(leaf => RELATION_LABELS[leaf.relation]).filter(Boolean))].join(' · ') }}</small></td>
                    <td><span class="match-status" :class="item.display_status">{{ symbols[item.display_status] }} {{ STATUS_LABELS[item.display_status] || '待确认' }}</span></td>
                    <td v-if="selectedId === item.id" class="match-mobile-evidence" colspan="3">
                      <CareerMatchEvidence :selected="selected" :modern="modern" @source="showSource" @manage="manage" />
                    </td>
                  </tr>
                </tbody></table>
                <p v-if="!visibleItems.length" class="match-empty">{{ items.length ? '当前筛选下没有要求，请调整筛选。' : '尚无可核对的要求，请确认 JD 正文与简历后重新分析。' }}</p>
                <p class="match-count" aria-live="polite">显示 {{ visibleItems.length }} / {{ items.length }} 项 · 点击要求核对证据</p>
              </template>
              <section v-else-if="tab === 'rules'" class="match-rules" aria-label="计算规则">
                <h3>本次计算规则</h3>
                <template v-if="modern">
                  <h4>硬性条件独立核验</h4><p>只有 JD 明确的准入或排除条件才列为硬性条件。任何明确不满足均提示差距；未知与资料冲突保持待核实，加分项不能抵消。</p>
                  <h4>核心与加分项分开计算</h4><p>{{ assessment.weight_policy }}</p>
                  <div v-for="key in ['core', 'bonus']" :key="key" class="match-formula"><strong>{{ TIER_LABELS[key] }}</strong><template v-if="assessment.summaries[key].denominator"><p>已支持下限：{{ number(assessment.summaries[key].numerator) }} ÷ {{ number(assessment.summaries[key].denominator) }} × 100 = {{ assessment.summaries[key].score }}%</p><p>待确认上限：{{ number(assessment.summaries[key].upper_numerator) }} ÷ {{ number(assessment.summaries[key].denominator) }} × 100 = {{ assessment.summaries[key].score_upper }}%</p></template><p v-else>JD 没有可计算的此类要求，不显示零分。</p></div>
                  <h4>复合要求保留父级权重</h4><p>全部条件按子条件覆盖汇总；任一条件取最高覆盖；至少 N 项按已满足与仍可能满足的数量分别计算。硬性条件必须逻辑上满足，不按平均覆盖率判通过。部分匹配不统一给 50%。</p>
                  <h4>未知与冲突保留区间</h4><p>满足计 [1,1]，有明确反证的不满足计 [0,0]，待确认或资料冲突计 [0,1]。未写明不代表没有能力；可迁移或仅提及不会自动获得固定分数。</p>
                  <h4>本次评估口径</h4><p>基准日期：{{ assessment.as_of }}。年限按符合 JD 口径的经历月份去重计算，“至今”不计未结束的当月。院校分类使用教育部名单；QS 只核对有来源的对应年份资料，未收录或无法明确对应学历阶段时保留待确认。</p>
                </template>
                <template v-else><p>此报告使用旧版覆盖率算法：必须项权重 2，加分项权重 1，部分证据按 0.5 折算。该算法未区分新版硬门槛与核心能力。请重新分析查看新版可复算规则。</p></template>
              </section>
              <section v-else class="match-originals" :aria-label="tab === 'jd' ? 'JD 原文' : '简历原文'">
                <h3>{{ tab === 'jd' ? '岗位原文' : modern ? '本次分析使用的简历原文' : '旧版可用证据片段' }}</h3>
                <article v-for="line in originalLines" :key="line.id" class="match-original" :class="{ 'is-highlighted': sourceFocus.includes(line.id) }"><small>{{ line.id }}</small><p>{{ line.text }}</p></article>
                <p v-if="!originalLines.length" class="match-empty">当前报告没有保留原文，重新分析后可查看。</p>
              </section>
            </section>
            <aside class="match-evidence" aria-label="选中要求的证据与判断">
              <CareerMatchEvidence :selected="selected" :modern="modern" @source="showSource" @manage="manage" />
            </aside>
          </div>
        </div>
        <footer class="match-footer">{{ assessment?.disclaimer || '分析基于当前绑定的简历与 JD，不代表录用概率。' }}<span v-if="assessment?.as_of">评估基准日 {{ assessment.as_of }}</span></footer>
      </div>
    </dialog>
  </Teleport>
</template>

<style scoped>
.match-previous-report{flex:none;margin:0;border-bottom:1px solid var(--ui-line);padding:10px 16px;background:var(--ui-warning-soft,#fff8e8);color:var(--ui-warning,#946012);font-size:13px;line-height:1.6}
.match-retry-error{flex:none;margin:0;border-bottom:1px solid var(--ui-line);padding:12px 16px;background:var(--ui-danger-soft,#fff3f3);color:var(--ui-danger,#b54747);font-size:14px;line-height:1.7}
.career-match-dialog{width:min(1240px,calc(100vw - 64px));max-width:none;max-height:calc(100vh - 64px);padding:0;border:1px solid var(--ui-line);border-radius:18px;background:var(--ui-surface);color:var(--ui-text);box-shadow:0 24px 80px #10223e40;font:14px/1.6 var(--ui-font-body);overflow:hidden}.career-match-dialog::backdrop{background:#14243c80;backdrop-filter:blur(4px)}.career-match-dialog *{box-sizing:border-box}.match-window{display:flex;flex-direction:column;max-height:calc(100vh - 66px)}.match-header{display:flex;align-items:start;justify-content:space-between;gap:24px;padding:24px 28px 20px;border-bottom:1px solid var(--ui-line)}.match-header h2{margin:0;font:700 24px/1.4 var(--ui-font-display);letter-spacing:-.5px}.match-header p{margin:6px 0 0;font-size:12px;color:var(--ui-text-muted)}.match-header-actions{display:flex;align-items:center;gap:12px;flex:none}.match-header-actions button{border:1px solid var(--ui-line);border-radius:8px;background:var(--ui-surface);color:var(--ui-accent-ink);padding:8px 12px;font:inherit;cursor:pointer}.match-header-actions button:disabled{opacity:.6;cursor:wait}.match-header-actions .match-close{width:36px;height:36px;padding:0;font-size:24px;color:var(--ui-text-secondary)}.match-scroll{overflow:auto;overscroll-behavior:contain;min-height:0;padding:22px 28px}.match-notice{margin:0 0 18px;padding:12px 14px;border:1px solid var(--ui-line-strong);border-radius:8px;background:var(--ui-surface-soft);font-size:13px;color:var(--ui-text-secondary)}.match-summaries{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));border:1px solid var(--ui-line);border-radius:10px;margin-bottom:22px;padding:18px 0}.match-summaries button{border:0;background:transparent;text-align:left;padding:0 20px;color:var(--ui-text);cursor:pointer;min-width:0}.match-summaries button+button{border-left:1px solid var(--ui-line)}.match-summaries span,.match-summaries small{display:block;color:var(--ui-text-muted);font-size:12px}.match-summaries strong{display:block;font:650 26px/1.7 var(--ui-font-display);font-variant-numeric:tabular-nums;overflow-wrap:anywhere}.match-tabs{display:flex;gap:28px;border-bottom:1px solid var(--ui-line);margin-bottom:22px}.match-tabs button{border:0;border-bottom:2px solid transparent;background:transparent;padding:0 0 12px;color:var(--ui-text-muted);font:inherit;cursor:pointer}.match-tabs button[aria-pressed=true]{color:var(--ui-accent-ink);border-color:var(--ui-accent);font-weight:600}.match-content{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:26px;align-items:start}.match-primary{min-width:0}.match-dimensions{display:grid;gap:10px;margin-bottom:22px}.match-dimensions button{display:grid;grid-template-columns:minmax(90px,140px) minmax(0,1fr) 100px;align-items:center;gap:14px;border:0;background:transparent;color:var(--ui-text-secondary);font:inherit;text-align:left;padding:2px 0;cursor:pointer}.match-dimensions button[aria-pressed=true]{color:var(--ui-accent-ink)}.match-dimensions button>span:last-child{text-align:right;font-size:12px;font-variant-numeric:tabular-nums}.match-bar{display:block;position:relative;height:7px;background:var(--ui-line);border-radius:4px;overflow:hidden}.match-bar i{position:absolute;display:block;inset-block:0;background:var(--ui-accent)}.match-bar .match-upper{opacity:.25}.match-filters{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:12px}.match-filters>div{display:flex;gap:4px;flex-wrap:wrap}.match-filters button{border:0;border-radius:6px;background:transparent;color:var(--ui-text-muted);font:inherit;font-size:12px;padding:6px 9px;cursor:pointer}.match-filters button[aria-pressed=true]{color:var(--ui-accent-ink);background:var(--ui-surface-active)}.match-filters label{font-size:12px;color:var(--ui-text-muted)}.match-filters select{border:1px solid var(--ui-line);border-radius:6px;background:var(--ui-surface);color:var(--ui-text);font:inherit;padding:5px 8px}.match-dimension-filter{font-size:12px;color:var(--ui-text-muted)}.match-dimension-filter button,.match-text-button{border:0;background:transparent;color:var(--ui-accent-ink);padding:5px 0;font:inherit;font-size:12px;cursor:pointer;text-align:left}.match-table{width:100%;table-layout:fixed;border-collapse:collapse;font-size:13px}.match-table th{font-weight:400;color:var(--ui-text-muted);text-align:left;border-bottom:1px solid var(--ui-line);padding:10px 9px}.match-table th:first-child{width:35%}.match-table th:last-child{width:19%}.match-table td{padding:14px 9px;border-bottom:1px solid var(--ui-line);vertical-align:top;overflow-wrap:anywhere}.match-table .is-selected{background:var(--ui-surface-active)}.match-table button{padding:0;border:0;background:transparent;color:var(--ui-text);font:inherit;text-align:left;cursor:pointer}.match-table button:hover{text-decoration:underline;text-underline-offset:4px}.match-table small{display:block;margin-top:5px;font-size:11px;color:var(--ui-text-muted)}.match-table p{margin:0}.match-status{font-size:12px;color:var(--ui-text-muted)}.match-status.supported{color:var(--ui-success,#237657)}.match-status.partial,.match-status.needs_confirmation{color:var(--ui-warning,#946012)}.match-status.unsupported,.match-status.conflict{color:var(--ui-danger,#b53e48)}.match-count{font-size:12px;color:var(--ui-text-muted);margin:12px 0 0}.match-evidence{border-left:1px solid var(--ui-line);padding-left:22px;min-width:0;overflow-wrap:anywhere}.match-evidence-caption{display:flex;gap:10px;justify-content:space-between;color:var(--ui-text-muted);font-size:12px}.match-evidence h3{margin:7px 0 8px;font:650 17px/1.5 var(--ui-font-display)}.match-evidence h4{font-size:12px;font-weight:400;color:var(--ui-text-muted);margin:20px 0 7px}.match-evidence p{margin:5px 0;font-size:13px;white-space:pre-wrap}.match-evidence blockquote{margin:7px 0;border-left:2px solid var(--ui-line-strong);padding-left:10px;font-size:13px;white-space:pre-wrap}.match-leaf{display:flex;align-items:baseline;justify-content:space-between;gap:12px;padding:8px 0;border-bottom:1px solid var(--ui-line);font-size:12px}.match-leaf .match-status{flex:none}.match-evidence .match-calculation{color:var(--ui-text-muted);font-size:12px;margin-top:10px;font-variant-numeric:tabular-nums}.match-questions{margin-top:18px;background:var(--ui-surface-soft);padding:12px;border:1px solid var(--ui-line);border-radius:8px}.match-questions strong{font-size:13px;font-weight:500}.match-rules h3,.match-originals h3{margin:0 0 15px;font-size:18px;font-weight:650}.match-rules h4{margin:20px 0 6px;font-size:14px;font-weight:600}.match-rules p{margin:7px 0;color:var(--ui-text-secondary);font-size:13px}.match-formula{padding:12px 14px;margin:14px 0;border:1px solid var(--ui-line);border-radius:8px;background:var(--ui-surface-soft);font-size:13px;font-variant-numeric:tabular-nums}.match-original{padding:14px 10px;border-bottom:1px solid var(--ui-line);scroll-margin:20px}.match-original small{color:var(--ui-text-muted);font-size:11px}.match-original p{margin:5px 0 0;font-size:13px;white-space:pre-wrap;overflow-wrap:anywhere}.match-original.is-highlighted{background:var(--ui-surface-active);border-left:3px solid var(--ui-accent)}.match-empty{padding:20px 0;color:var(--ui-text-muted);font-size:13px}.match-footer{display:flex;justify-content:space-between;gap:16px;padding:12px 28px;border-top:1px solid var(--ui-line);background:var(--ui-surface-soft);font-size:11px;color:var(--ui-text-muted)}.match-footer span{flex:none}.career-match-dialog button:focus-visible,.career-match-dialog select:focus-visible{outline:2px solid var(--ui-accent);outline-offset:3px}
.match-mobile-evidence{display:none}
@media(max-width:640px){
  .career-match-dialog{inset:0;width:100%;height:100dvh;max-height:100dvh;margin:0;border:0;border-radius:0}
  .match-window{height:100%;max-height:100dvh}
  .match-header{gap:12px;padding:max(12px,env(safe-area-inset-top)) 16px 12px}
  .match-header h2{font-size:20px;line-height:1.5}
  .match-header p{font-size:13px;overflow-wrap:anywhere}
  .match-header-actions{gap:8px}
  .match-header-actions button{min-height:44px;padding:8px;font-size:13px;white-space:nowrap}
  .match-header-actions .match-close{width:44px;height:44px}
  .match-scroll{min-height:0;flex:1;padding:16px;overflow-y:auto;overflow-x:clip}
  .match-summaries{grid-template-columns:minmax(0,1fr);gap:0;padding:0}
  .match-summaries button{display:grid;grid-template-columns:88px minmax(0,1fr);align-items:center;gap:2px 10px;padding:10px 12px}
  .match-summaries button+button{border-left:0;border-top:1px solid var(--ui-line)}
  .match-summaries strong{font-size:20px;text-align:right;line-height:1.3}
  .match-summaries small{grid-column:1/-1;font-size:13px;line-height:1.5}
  .match-tabs{gap:0;justify-content:space-between;overflow-x:auto;margin-bottom:16px}
  .match-tabs button{min-height:44px;flex:none;padding:8px 6px;font-size:14px;white-space:nowrap}
  .match-content{display:block;min-width:0}
  .match-content>.match-evidence{display:none}
  .match-dimensions button{grid-template-columns:minmax(80px,1fr) minmax(32px,1fr) 86px;gap:8px;min-height:44px;font-size:14px}
  .match-filters{align-items:stretch;flex-direction:column}
  .match-filters button,.match-filters select{min-height:44px;font-size:14px}
  .match-table,.match-table tbody,.match-table tr,.match-table td{display:block;width:100%}
  .match-table thead{display:none}
  .match-table tr{margin:12px 0;border:1px solid var(--ui-line);border-radius:12px;overflow:hidden}
  .match-table td{border:0;padding:10px 14px;font-size:15px}
  .match-table td:first-child{font-weight:600;padding-bottom:0}
  .match-table td:first-child button{min-height:44px;width:100%}
  .match-table .match-evidence-cell{padding-bottom:0}
  .match-table .match-mobile-evidence{display:block;border-top:1px solid var(--ui-line);background:var(--ui-surface);padding:16px}
  .match-table small{font-size:13px}
  .match-footer{flex-direction:column;gap:4px;padding:10px 16px max(10px,env(safe-area-inset-bottom));font-size:12px}
  .match-footer span{flex:auto}
}

</style>
