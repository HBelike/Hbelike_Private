<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'

const runningPipeline = ref(false)
const loadingRuns = ref(true)
const errorMessage = ref('')
const successMessage = ref('')
const pipelineRuns = ref([])
let pipelinePollTimer = null

onMounted(loadPipelineRuns)
onBeforeUnmount(stopPipelinePolling)

/**
 * 读取管理员手动运行记录，并依据是否存在活跃任务决定是否继续轮询。
 * 该组件只会在首页被管理员挂载；后端仍会二次校验管理员身份。
 */
async function loadPipelineRuns() {
  loadingRuns.value = true
  try {
    const response = await fetch('/api/admin/pipeline-runs', {
      credentials: 'include',
      cache: 'no-store'
    })
    if (!response.ok) throw new Error(await responseError(response, '无法读取手动运行记录'))

    pipelineRuns.value = (await response.json()).items ?? []
    const hasActiveRun = pipelineRuns.value.some((item) => ['queued', 'running'].includes(item.status))
    runningPipeline.value = hasActiveRun
    if (hasActiveRun) startPipelinePolling()
    else stopPipelinePolling()
  } catch (error) {
    runningPipeline.value = false
    errorMessage.value = error instanceof Error ? error.message : '无法读取手动运行记录'
    stopPipelinePolling()
  } finally {
    loadingRuns.value = false
  }
}

/**
 * 使用既有管理员流水线 API 提交一次完整任务链。
 * client_request_id 让重复点击或网络重试不会创建两次运行记录。
 */
async function startPipeline() {
  if (runningPipeline.value) return

  errorMessage.value = ''
  successMessage.value = ''
  runningPipeline.value = true

  try {
    const response = await fetch('/api/admin/pipeline-runs', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        client_request_id: typeof crypto?.randomUUID === 'function'
          ? crypto.randomUUID()
          : `manual-${Date.now()}-${Math.random().toString(16).slice(2)}`
      })
    })
    if (!response.ok) throw new Error(await responseError(response, '无法提交完整流水线'))

    const item = (await response.json()).item
    successMessage.value = `已提交完整流水线${item?.id ? ` #${String(item.id).slice(0, 8)}` : ''}，任务将在后台依次执行。`
    await loadPipelineRuns()
    startPipelinePolling()
  } catch (error) {
    runningPipeline.value = false
    errorMessage.value = error instanceof Error ? error.message : '无法提交完整流水线'
  }
}

function startPipelinePolling() {
  if (pipelinePollTimer) return
  pipelinePollTimer = window.setInterval(loadPipelineRuns, 4000)
}

function stopPipelinePolling() {
  if (!pipelinePollTimer) return
  window.clearInterval(pipelinePollTimer)
  pipelinePollTimer = null
}

function pipelineStatusText(status) {
  return ({ queued: '排队中', running: '运行中', succeeded: '已完成', failed: '失败' })[status] ?? status
}

function pipelineStatusClass(status) {
  return `is-${status ?? 'queued'}`
}

function pipelineTime(value) {
  if (!value) return '等待开始'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString('zh-CN', { hour12: false })
}

async function responseError(response, fallback) {
  try {
    const payload = await response.json()
    return payload?.detail || fallback
  } catch {
    return fallback
  }
}
</script>

<template>
  <section class="manual-pipeline-card" aria-labelledby="manual-pipeline-title">
    <div class="manual-pipeline-heading">
      <div>
        <p class="eyebrow">Manual run</p>
        <h2 id="manual-pipeline-title">手动运行本周工作流</h2>
        <p>按当前已保存的配置依次执行选题、总结、图片、视频蓝图、排版和草稿创建。</p>
      </div>
      <span class="manual-pipeline-badge" :class="{ running: runningPipeline }">
        {{ runningPipeline ? '任务运行中' : '管理员操作' }}
      </span>
    </div>

    <div v-if="errorMessage" class="manual-pipeline-alert danger" role="alert">{{ errorMessage }}</div>
    <div v-if="successMessage" class="manual-pipeline-alert success" role="status">{{ successMessage }}</div>

    <div class="manual-pipeline-actions">
      <button class="refresh-button" type="button" :disabled="runningPipeline" @click="startPipeline">
        {{ runningPipeline ? '完整流程运行中…' : '运行完整流程' }}
      </button>
      <button class="secondary-button" type="button" :disabled="loadingRuns" @click="loadPipelineRuns">
        {{ loadingRuns ? '读取状态中…' : '刷新运行状态' }}
      </button>
    </div>

    <div v-if="loadingRuns && !pipelineRuns.length" class="manual-pipeline-empty">正在读取最近运行记录…</div>
    <div v-else-if="pipelineRuns.length" class="manual-pipeline-runs" aria-live="polite">
      <article v-for="item in pipelineRuns" :key="item.id" class="manual-pipeline-run">
        <div>
          <strong>#{{ String(item.id).slice(0, 8) }}</strong>
          <small>{{ pipelineTime(item.started_at || item.created_at) }}</small>
        </div>
        <span class="manual-run-status" :class="pipelineStatusClass(item.status)">{{ pipelineStatusText(item.status) }}</span>
        <p v-if="item.error_message">{{ item.error_message }}</p>
        <p v-else-if="item.metadata?.tasks?.length">已记录 {{ item.metadata.tasks.length }} 个任务结果</p>
        <p v-else>任务已登记，等待执行器更新。</p>
      </article>
    </div>
    <p v-else class="manual-pipeline-empty">暂无手动运行记录。</p>
  </section>
</template>
