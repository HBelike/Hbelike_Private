<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import JobSearchWorkspace from './JobSearchWorkspace.vue'
import { canAutoSearchCard } from '../job-search-card.js'

const props = defineProps({
  message: { type: Object, required: true },
  active: { type: Boolean, default: true },
  fresh: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
  error: { type: String, default: '' },
  searchSession: { type: Object, default: null }
})
const emit = defineEmits(['confirm', 'state-change'])
const payload = computed(() => props.message.ui_payload || {})
const selectedJob = ref(null)
const savedBatches = ref([])
const savedJobs = ref([])
const viewedBatch = ref(null)
const archiveError = ref('')
const loadingArchive = ref(false)
const sessionRevision = ref(0)
const retryingAnalysis = ref('')
const analysisEntries = computed(() => {
  sessionRevision.value
  return savedBatches.value.map((batch) => props.searchSession?.state(batch.id)?.batch || batch).filter((batch) => batch.analysis_state)
})
function analysisLabel(batch) {
    return { queued: '正在等待岗位推荐', running: '正在分析岗位', succeeded: '岗位推荐已完成', failed: '岗位推荐未完成', cancelled: '岗位推荐已取消', cancel_requested: '正在取消岗位推荐' }[batch.analysis_state] || ''
}
async function retryRecommendation(batch) {
  if (retryingAnalysis.value) return
  retryingAnalysis.value = batch.id
  try { await props.searchSession.retryAnalysis(batch.id) } catch (error) { archiveError.value = error.message }
  finally { retryingAnalysis.value = '' }
}
let unsubscribeSession = () => {}
watch(() => props.searchSession, (session) => {
  unsubscribeSession()
  unsubscribeSession = session?.subscribe?.(() => { sessionRevision.value++ }) || (() => {})
}, { immediate: true })
onBeforeUnmount(() => unsubscribeSession())
const hasSaveError = computed(() => {
  sessionRevision.value
  return Boolean(props.error || savedBatches.value.some((batch) => props.searchSession?.state(batch.id)?.failed?.some((operation) => operation.action !== 'analysis/retry')))
})
const isVersioned = computed(() => payload.value.version === 2 && props.searchSession)
function batchChanged(batch) {
  if (!batch) return
  savedBatches.value = [...savedBatches.value.filter((item) => item.id !== batch.id), batch]
}
async function viewBatch(batch) {
  loadingArchive.value = true; archiveError.value = ''
  try {
    viewedBatch.value = batch
    savedJobs.value = await props.searchSession.jobs(batch.id, batch.revision)
    props.searchSession.activate(batch.id)
    if (payload.value.focused_job_ref) {
      const focused = savedJobs.value.find((job) => job.job_ref === payload.value.focused_job_ref)
      if (focused) await previewSaved(focused)
    }
    expanded.value = true
  } catch (error) { archiveError.value = error.message }
  finally { loadingArchive.value = false }
}
async function previewSaved(job) {
  selectedJob.value = job
  try { await props.searchSession.focus(viewedBatch.value.id, job) } catch (error) { archiveError.value = error.message }
}
async function restoreBatches() {
  if (!isVersioned.value) return
  try {
    const all = await props.searchSession.restore(payload.value.view_batch_id ? null : props.message.id)
    savedBatches.value = payload.value.view_batch_id ? all.filter((item) => item.id === payload.value.view_batch_id) : all
    if (payload.value.view_batch_id && savedBatches.value[0]) await viewBatch(savedBatches.value[0])
  } catch (error) { archiveError.value = error.message }
}
async function retrySaving() {
  try {
    for (const batch of savedBatches.value) await props.searchSession.retry(batch.id)
    const latest = savedBatches.value.at(-1)
    if (latest) {
      const restored = props.searchSession.state(latest.id)?.batch || latest
      await viewBatch(restored)
      if (payload.value.auto_analyze && !restored.analysis_turn_id) await props.searchSession.complete(restored.id)
    }
    archiveError.value = ''
  } catch (error) { archiveError.value = error.message }
}
watch(() => props.message.id, restoreBatches, { immediate: true })
const expanded = ref(false)
const searchable = computed(() => props.active && !['selected', 'dismissed'].includes(payload.value.status))
const showWorkspace = computed(() => !viewedBatch.value && searchable.value && (isVersioned.value ? (props.fresh || expanded.value) : (payload.value.status === 'editing' || props.fresh || expanded.value)))
const autoSearch = computed(() => canAutoSearchCard({ fresh: props.fresh, active: props.active, payload: payload.value }))
const displayConditions = computed(() => viewedBatch.value?.conditions || savedBatches.value.at(-1)?.conditions || payload.value.conditions)
const conditionsText = computed(() => [displayConditions.value?.city, displayConditions.value?.keyword].filter(Boolean).join(' · ') || '待填写搜索条件')
const displayStatus = computed(() => {
  const batch = viewedBatch.value || savedBatches.value.at(-1)
  return isVersioned.value && batch ? (batch.search_status === 'cancelled' ? 'dismissed' : (batch.loaded_count > 0 || batch.search_status === 'empty' ? 'searched' : payload.value.status)) : payload.value.status
})
const statusText = computed(() => ({ editing: '填写搜索条件', searched: '搜索已完成', selected: '目标岗位已确认', dismissed: '已收起' }[displayStatus.value] || '找工作'))
const canConfirm = computed(() => Boolean(selectedJob.value?.title && (selectedJob.value?.company || selectedJob.value?.companyShort) && selectedJob.value?.description && !props.busy))

watch(() => props.message.id, () => { selectedJob.value = null; expanded.value = false })
watch(() => props.active, (active) => { if (!active) selectedJob.value = null })

function confirm() {
  if (canConfirm.value) emit('confirm', selectedJob.value)
}

async function dismiss() {
  if (props.busy) return
  try {
    if (isVersioned.value) for (const batch of savedBatches.value) await props.searchSession.cancel(batch.id)
    emit('state-change', { status: 'dismissed' })
  } catch (error) { archiveError.value = error.message }
}
</script>

<template>
  <section class="career-job-search-card" aria-label="找工作">
    <header class="job-chat-heading">
      <div><h3>找工作</h3><p>{{ statusText }}<span> · {{ conditionsText }}</span></p></div>
      <button v-if="searchable" type="button" :disabled="busy" @click="dismiss">收起</button>
    </header>

    <JobSearchWorkspace
      v-if="showWorkspace"
      chat-mode
      selection-mode
      :initial-query="payload.conditions?.keyword || ''"
      :initial-city-name="payload.conditions?.city || ''"
      :initial-city-source="payload.conditions?.city_source || ''"
      :auto-search="autoSearch"
      :search-session="isVersioned ? searchSession : null"
      :source-message-id="message.id"
      :analysis-requested="Boolean(payload.auto_analyze)"
      @batch-change="batchChanged"
      @selection-change="selectedJob = $event"
      @search-state="emit('state-change', $event)"
    />

    <div v-else class="job-chat-summary">
      <template v-if="isVersioned && savedBatches.length">
        <button v-for="batch in savedBatches" :key="batch.id" type="button" @click="viewBatch(batch)">第 {{ batch.batch_no }} 批 · {{ batch.conditions?.keyword }} · {{ batch.conditions?.city }}（{{ batch.loaded_count }} 个）</button>
        <p v-if="loadingArchive">正在读取已保存岗位…</p>
        <div v-if="viewedBatch" class="job-archive-grid"><ul class="job-archive-list" aria-label="已保存岗位"><li v-for="job in savedJobs" :key="job.job_ref"><button type="button" :class="{ 'is-current': selectedJob?.job_ref === job.job_ref }" @click="previewSaved(job)">{{ job.job_ref }} · {{ job.title }} · {{ job.company }}</button></li></ul>
        <section v-if="selectedJob" class="job-archive-detail"><h4>{{ selectedJob.title }}</h4><p>{{ selectedJob.description || selectedJob.description_excerpt || '尚未取得完整岗位要求，可基于摘要初筛。' }}</p></section></div>
      </template>
      <p v-if="payload.selected_job"><strong>{{ payload.selected_job.title }}</strong><span> · {{ payload.selected_job.company }}</span></p>
      <template v-else-if="payload.result_summary">
        <p>已找到 {{ payload.result_summary.count || 0 }} 个岗位</p>
        <ul v-if="payload.result_summary.jobs?.length">
          <li v-for="(job, index) in payload.result_summary.jobs.slice(0, 3)" :key="job.id || index">
            <strong>{{ job.title }}</strong><span>{{ job.company }}</span><em>{{ job.salary }}</em>
          </li>
        </ul>
      </template>
      <p v-else-if="!isVersioned || !savedBatches.length">搜索条件已保留。</p>
      <button v-if="searchable && !viewedBatch" type="button" @click="expanded = true">展开搜索</button>
    </div>

    <div v-if="isVersioned" class="job-analysis-status" aria-live="polite">
      <div v-for="batch in analysisEntries" :key="batch.id">
        <span>第 {{ batch.batch_no }} 批 · {{ analysisLabel(batch) }}</span>
        <p v-if="batch.analysis_error" class="job-chat-error">{{ batch.analysis_error }}</p>
        <button v-if="['failed', 'cancelled'].includes(batch.analysis_state)" class="job-chat-retry" type="button" :disabled="retryingAnalysis === batch.id" @click="retryRecommendation(batch)">重试推荐</button>
      </div>
    </div>
    <p v-if="archiveError" class="job-chat-error" role="alert">{{ archiveError }}</p>
    <button v-if="isVersioned && hasSaveError" class="job-chat-retry" type="button" @click="retrySaving">重试保存</button>
    <p v-if="error" class="job-chat-error" role="alert">{{ error }}</p>
    <footer v-if="showWorkspace || (viewedBatch && selectedJob)" class="job-chat-confirmation">
      <p v-if="selectedJob"><strong>{{ selectedJob.title }}</strong><span> · {{ selectedJob.company || selectedJob.companyShort }}</span></p>
      <p v-else>查看岗位详情后，可将它设为本次对话的目标岗位。</p>
      <button type="button" :disabled="!canConfirm" @click="confirm">{{ busy ? '正在设置…' : '设为目标岗位' }}</button>
    </footer>
  </section>
</template>

<style scoped>
.career-job-search-card{box-sizing:border-box;min-width:0;width:100%;margin-top:12px;border:1px solid var(--ui-line-strong,#b9cfed);border-radius:14px;padding:14px;background:var(--ui-surface,#fff);color:var(--ui-text,#14213d)}
.job-chat-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}.job-chat-heading h3{margin:0;font-size:16px;line-height:1.5}.job-chat-heading p{margin:3px 0 0;font-size:12px;color:var(--ui-text-secondary,#526078)}.job-chat-heading p span{color:var(--ui-text-muted,#7b879c)}
.job-chat-heading button,.job-chat-summary>button{flex:none;border:1px solid var(--ui-line,#d7e4f6);border-radius:8px;padding:7px 11px;background:var(--ui-surface,#fff);color:var(--ui-text-secondary,#526078);cursor:pointer}
.job-chat-summary{font-size:12px;line-height:1.7}.job-chat-summary p{margin:5px 0}.job-chat-summary ul{list-style:none;margin:10px 0 0;padding:0}.job-chat-summary li{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr) auto;gap:10px;padding:8px 0;border-top:1px solid var(--ui-line,#d7e4f6)}.job-chat-summary li strong,.job-chat-summary li span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.job-chat-summary li span{color:var(--ui-text-secondary,#526078)}.job-chat-summary li em{font-style:normal;color:var(--ui-accent-ink,#004aa8)}
.job-chat-confirmation{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-top:12px;padding-top:12px;border-top:1px solid var(--ui-line,#d7e4f6)}.job-chat-confirmation p{min-width:0;margin:0;font-size:12px;line-height:1.6;color:var(--ui-text-secondary,#526078)}.job-chat-confirmation button{flex:none;min-height:38px;border:0;border-radius:9px;padding:8px 14px;background:var(--ui-accent,#0869d8);color:#fff;font-weight:700;cursor:pointer}.job-chat-confirmation button:disabled{opacity:.45;cursor:not-allowed}.job-chat-error{margin:10px 0 0;padding:9px;border-radius:8px;background:#fff4f4;color:#a64141;font-size:12px}
.job-analysis-status{margin-top:12px;font-size:12px;line-height:1.7;color:var(--ui-text-secondary,#526078)}
.job-archive-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr));gap:14px;margin-top:12px;min-width:0}
.job-chat-summary .job-archive-list{margin:0;max-height:360px;overflow:auto;border:1px solid var(--ui-line,#d7e4f6);border-radius:9px;padding:4px 10px}
.job-chat-summary .job-archive-list li{display:block;padding:4px 0}.job-archive-list button{box-sizing:border-box;width:100%;text-align:left;border:0;border-radius:6px;background:var(--ui-surface,#fff);padding:9px 7px;color:var(--ui-text,#14213d);font:inherit;line-height:1.6;white-space:normal;overflow-wrap:anywhere;cursor:pointer}.job-archive-list button.is-current,.job-archive-list button:hover,.job-archive-list button:focus-visible{background:var(--ui-accent-soft,#edf5ff);outline:2px solid var(--ui-line-strong,#b9cfed)}
.job-archive-detail{min-width:0;max-height:360px;overflow:auto;padding:12px;border:1px solid var(--ui-line,#d7e4f6);border-radius:9px;background:var(--ui-surface-subtle,#f7faff)}.job-archive-detail h4{font-size:15px;margin:0 0 9px;overflow-wrap:anywhere}.job-archive-detail p{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.85}.job-chat-retry{margin-top:10px;border:1px solid var(--ui-line,#d7e4f6);border-radius:7px;padding:6px 10px;color:var(--ui-text-secondary,#526078);background:var(--ui-surface,#fff);cursor:pointer}
@media(max-width:600px){.career-job-search-card{padding:10px}.job-chat-heading p span{display:block}.job-chat-confirmation{align-items:stretch;flex-direction:column}.job-chat-summary li{grid-template-columns:minmax(0,1fr) auto}.job-chat-summary li span{grid-row:2}.job-chat-summary li em{grid-column:2;grid-row:1}}
</style>
