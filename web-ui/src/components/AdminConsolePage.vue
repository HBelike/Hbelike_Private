<script setup>
import { onMounted, reactive, ref } from 'vue'

const loading = ref(true)
const saving = ref(false)
const errorMessage = ref('')
const successMessage = ref('')
const version = ref(null)

const form = reactive({
  top_n: 5,
  github_keywords: 'agent, AI, LLM, RAG',
  summary_prompt: '',
  image_prompt: '',
  video_prompt: ''
})

onMounted(loadConfig)

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
    </template>
  </section>
</template>
