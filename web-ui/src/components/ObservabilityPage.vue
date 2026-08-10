<script setup>
import { computed, onMounted, ref } from 'vue'

const loading = ref(true)
const status = ref(null)
const errorMessage = ref('')
const frameLoaded = ref(false)

const consoleUrl = computed(() => status.value?.ui_url || 'https://smith.langchain.com')

onMounted(loadStatus)

async function loadStatus() {
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await fetch('/api/observability/status', { credentials: 'include', cache: 'no-store' })
    if (!response.ok) throw new Error(await responseError(response, '无法读取 LangSmith 配置'))
    status.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '无法读取 LangSmith 配置'
  } finally {
    loading.value = false
  }
}

function openConsole() {
  window.open(consoleUrl.value, '_blank', 'noopener,noreferrer')
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
  <section class="observability-page">
    <header class="observability-heading">
      <div>
        <p class="eyebrow">LANGSMITH OBSERVABILITY</p>
        <h2>模型链路监控</h2>
        <p>查看 LangChain 与手工 Trace 记录的模型调用、Token 消耗、耗时和失败原因。</p>
      </div>
      <div class="observability-actions">
        <button class="secondary-button" type="button" @click="loadStatus">刷新状态</button>
        <button class="refresh-button" type="button" @click="openConsole">在新窗口打开</button>
      </div>
    </header>

    <section v-if="loading" class="observability-state">正在连接 LangSmith…</section>
    <section v-else-if="errorMessage" class="observability-state danger">
      <strong>无法加载监控页面</strong>
      <span>{{ errorMessage }}</span>
    </section>
    <template v-else>
      <section class="observability-status-grid">
        <article>
          <span>追踪状态</span>
          <strong :class="{ ready: status.enabled }">{{ status.enabled ? '已启用' : '待配置' }}</strong>
          <small>{{ status.enabled ? '服务端已检测到 LangSmith 凭证。' : '配置 LANGSMITH_API_KEY 后自动启用。' }}</small>
        </article>
        <article>
          <span>项目</span>
          <strong>{{ status.project }}</strong>
          <small>运行记录按项目维度归档。</small>
        </article>
        <article>
          <span>隐私边界</span>
          <strong>仅元数据</strong>
          <small>不上传账号、Cookie、原始简历或附件。</small>
        </article>
      </section>

      <section class="observability-browser">
        <div class="observability-browser-bar">
          <span class="observability-browser-dots"><i></i><i></i><i></i></span>
          <span>{{ consoleUrl }}</span>
          <button type="button" @click="openConsole">↗ 打开</button>
        </div>
        <div class="observability-frame-wrap">
          <div v-if="!frameLoaded" class="observability-frame-loading">正在载入 LangSmith 控制台…</div>
          <iframe
            :src="consoleUrl"
            title="LangSmith 监控控制台"
            @load="frameLoaded = true"
          ></iframe>
        </div>
        <p class="observability-note">
          若 LangSmith 因登录策略、第三方 Cookie 或页面安全策略拒绝嵌入，请使用“在新窗口打开”。监控采集不受页面嵌入限制。
        </p>
      </section>
    </template>
  </section>
</template>
