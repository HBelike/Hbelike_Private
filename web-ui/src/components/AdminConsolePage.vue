<script setup>
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'

const loading = ref(true)
const saving = ref(false)
const runningPipeline = ref(false)
const errorMessage = ref('')
const successMessage = ref('')
const version = ref(null)
const pipelineRuns = ref([])
let pipelinePollTimer = null

const form = reactive({
  top_n: 5,
  github_keywords: 'agent, AI, LLM, RAG',
  summary_prompt: '',
  image_prompt: '',
  video_prompt: ''
})

onMounted(async () => {
  await Promise.all([loadConfig(), loadPipelineRuns()])
})

onBeforeUnmount(() => stopPipelinePolling())

async function loadConfig() {
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await fetch('/api/admin/pipeline-config', { credentials: 'include', cache: 'no-store' })
    if (!response.ok) throw new Error(await responseError(response, '无法读取工作流配置'))
    const item = (await response.json()).item
    if (item) {
      version.value = item.version
      Object.assign(form, {
        top_n: item.config?.top_n ?? 5,
        github_keywords: Array.isArray(item.config?.github_keywords)
          ? item.config.github_keywords.join(', ')
          : (item.config?.github_keywords ?? form.github_keywords),
        summary_prompt: item.config?.summary_prompt ?? '',
        image_prompt: item.config?.image_prompt ?? '',
        video_prompt: item.config?.video_prompt ?? ''
      })
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '无法读取工作流配置'
  } finally {
    loading.value = false
  }
}

async function saveConfig() {
  if (saving.value) return
  errorMessage.value = ''
  successMessage.value = ''
  const topN = Number(form.top_n)
  if (!Number.isInteger(topN) || topN < 1 || topN > 12) {
    errorMessage.value = '热门项目数量需要是 1 到 12 之间的整数。'
    return
  }

  saving.value = true
  try {
    const response = await fetch('/api/admin/pipeline-config', {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        config: {
          top_n: topN,
          github_keywords: form.github_keywords.split(/[，,]/).map((item) => item.trim()).filter(Boolean),
          summary_prompt: form.summary_prompt.trim(),
          image_prompt: form.image_prompt.trim(),
          video_prompt: form.video_prompt.trim()
        }
      })
    })
    if (!response.ok) throw new Error(await responseError(response, '保存失败'))
    const item = (await response.json()).item
    version.value = item.version
    successMessage.value = `已保存配置版本 v${item.version}。后续手动或定时运行将读取该版本快照。`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '保存失败'
  } finally {
    saving.value = false
  }
}

async function loadPipelineRuns() {
  try {
    const response = await fetch('/api/admin/pipeline-runs', { credentials: 'include', cache: 'no-store' })
    if (!response.ok) throw new Error(await responseError(response, '无法读取运行历史'))
    pipelineRuns.value = (await response.json()).items ?? []
    const hasActiveRun = pipelineRuns.value.some((item) => ['queued', 'running'].includes(item.status))
    runningPipeline.value = hasActiveRun
    if (hasActiveRun) startPipelinePolling()
    else stopPipelinePolling()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '无法读取运行历史'
    stopPipelinePolling()
  }
}

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
    successMessage.value = `已提交完整流水线 ${item?.id ? `#${String(item.id).slice(0, 8)}` : ''}，可继续浏览；任务会在后台依次执行。`
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
  <section class="admin-console-page">
    <header class="admin-console-heading">
      <div>
        <p class="eyebrow">RUNTIME CONFIGURATION</p>
        <h2>内容工作流管理台</h2>
        <p>配置 GitHub 选题范围、热门项目数量与文案提示词。每次保存生成独立版本，运行中的任务不会读取半成品配置。</p>
      </div>
      <div class="admin-config-version">{{ version ? `当前 v${version}` : '尚未保存版本' }}</div>
    </header>

    <section v-if="loading" class="admin-console-state">正在读取工作流配置…</section>
    <template v-else>
      <section v-if="errorMessage" class="admin-console-alert danger">{{ errorMessage }}</section>
      <section v-if="successMessage" class="admin-console-alert success">{{ successMessage }}</section>

      <form class="admin-config-form" @submit.prevent="saveConfig">
        <section class="admin-config-card">
          <div class="admin-card-heading">
            <div><p class="eyebrow">DISCOVERY</p><h3>GitHub 热门项目筛选</h3></div>
          </div>
          <div class="admin-field-grid">
            <label>
              <span>本期项目数量</span>
              <input v-model.number="form.top_n" type="number" min="1" max="12" />
              <small>用于搜索、总结、图片和视频分镜的项目数量。</small>
            </label>
            <label>
              <span>主题关键词</span>
              <input v-model="form.github_keywords" type="text" placeholder="agent, AI, LLM, RAG" />
              <small>用逗号分隔。为空时使用 GitHub 通用热门排行。</small>
            </label>
          </div>
        </section>

        <section class="admin-config-card">
          <div class="admin-card-heading">
            <div><p class="eyebrow">PROMPT TEMPLATES</p><h3>生成策略</h3></div>
            <span>可留空以使用系统默认模板</span>
          </div>
          <div class="admin-prompt-grid">
            <label>
              <span>文章总结提示词</span>
              <textarea v-model="form.summary_prompt" rows="7" placeholder="覆盖系统默认文章总结提示词"></textarea>
            </label>
            <label>
              <span>生图提示词</span>
              <textarea v-model="form.image_prompt" rows="7" placeholder="覆盖系统默认教学风插图提示词"></textarea>
            </label>
            <label>
              <span>视频分镜提示词</span>
              <textarea v-model="form.video_prompt" rows="7" placeholder="覆盖系统默认 Seedance 分镜提示词"></textarea>
            </label>
          </div>
        </section>

        <footer class="admin-config-footer">
          <p>保存仅更新后续运行的配置快照；历史文章和正在执行的任务不会被改写。</p>
          <button class="refresh-button" type="submit" :disabled="saving">
            {{ saving ? '正在保存…' : '保存为新版本' }}
          </button>
        </footer>
      </form>

      <section class="admin-pipeline-card">
        <div class="admin-card-heading">
          <div>
            <p class="eyebrow">MANUAL EXECUTION</p>
            <h3>手动执行完整流水线</h3>
          </div>
          <span>按当前已保存版本执行</span>
        </div>
        <p class="admin-pipeline-copy">按顺序执行自检、GitHub 搜索、内容总结、图像与视频蓝图、音视频任务、排版与草稿箱投递；缺少外部资源的任务会记录为可追踪状态。</p>
        <div class="admin-pipeline-actions">
          <button class="refresh-button" type="button" :disabled="runningPipeline" @click="startPipeline">
            {{ runningPipeline ? '流水线运行中…' : '执行本次完整流水线' }}
          </button>
          <button class="secondary-button" type="button" @click="loadPipelineRuns">刷新运行记录</button>
        </div>
        <div v-if="pipelineRuns.length" class="admin-pipeline-runs" aria-live="polite">
          <article v-for="item in pipelineRuns" :key="item.id" class="admin-pipeline-run">
            <div>
              <strong>#{{ String(item.id).slice(0, 8) }}</strong>
              <small>{{ pipelineTime(item.started_at || item.created_at) }}</small>
            </div>
            <span class="admin-run-status" :class="pipelineStatusClass(item.status)">{{ pipelineStatusText(item.status) }}</span>
            <p v-if="item.error_message">{{ item.error_message }}</p>
            <p v-else-if="item.metadata?.tasks?.length">已记录 {{ item.metadata.tasks.length }} 个任务结果</p>
            <p v-else>任务已登记，等待执行器更新。</p>
          </article>
        </div>
        <p v-else class="admin-pipeline-empty">暂无手动运行记录。</p>
      </section>
    </template>
  </section>
</template>
