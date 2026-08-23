<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { listMicrophones } from '../audio/capture'
import { beginLiveSession } from '../runtime'
import { resetSessionState, sessionState } from '../stores/session'
import { bridge, isElectronBridge } from '../bridge'

interface SetupOptions {
  candidate_profiles: Array<{ id: string; display_name: string; source_filename: string }>
  target_roles: Array<{ id: string; company_name: string; role_name: string }>
  asr_models: Array<{ id: string; display_name: string; readiness: string }>
  answer_models: Array<{ id: string; display_name: string; readiness: string }>
}

const router = useRouter()
const route = useRoute()
const launchApiBaseUrl = typeof route.query.apiBaseUrl === 'string' ? route.query.apiBaseUrl : ''
const apiBaseUrl = ref(launchApiBaseUrl || localStorage.getItem('liveInterviewApiBase') || 'http://127.0.0.1:8000')
const options = ref<SetupOptions | null>(null)
const microphones = ref<Array<{ deviceId: string; label: string }>>([])
const candidateId = ref('')
const targetId = ref('')
const asrModelId = ref('')
const answerModelId = ref('')
const microphoneId = ref('')
const interviewMaterials = ref<Array<{ id: string; label: string; company: string }>>([])
const selectedInterviewIds = ref<string[]>([])
const loading = ref(false)
const error = ref('')
const readyAsr = computed(() => options.value?.asr_models.filter((item) => item.readiness === 'ready') ?? [])
const readyAnswers = computed(() => options.value?.answer_models.filter((item) => item.readiness === 'ready') ?? [])

async function loadOptions() {
  loading.value = true
  error.value = ''
  try {
    options.value = await bridge.apiRequest<SetupOptions>(apiBaseUrl.value, '/api/career/live-interviews/setup-options')
    try {
      const library = await bridge.apiRequest<{ items: Array<{ label: string; children?: Array<{ id: string; label: string }> }> }>(apiBaseUrl.value, '/api/career/interview-library/tree')
      interviewMaterials.value = library.items.flatMap((company) =>
        (company.children ?? []).map((item) => ({ id: item.id, label: item.label, company: company.label })),
      ).slice(0, 12)
    } catch {
      // 面经是可选上下文，加载失败不应阻断核心面试流程。
      interviewMaterials.value = []
    }
    microphones.value = isElectronBridge
      ? await listMicrophones()
      : [{ deviceId: 'preview-microphone', label: '默认麦克风（预览）' }]
    candidateId.value ||= options.value.candidate_profiles[0]?.id ?? ''
    targetId.value ||= options.value.target_roles[0]?.id ?? ''
    asrModelId.value ||= readyAsr.value[0]?.id ?? ''
    answerModelId.value ||= readyAnswers.value[0]?.id ?? ''
    microphoneId.value ||= microphones.value[0]?.deviceId ?? ''
    localStorage.setItem('liveInterviewApiBase', apiBaseUrl.value)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法完成设备与服务预检'
  } finally {
    loading.value = false
  }
}

function openLogin() {
  void bridge.openLogin(apiBaseUrl.value)
}

async function start() {
  if (!candidateId.value && !targetId.value) {
    error.value = '至少选择一份简历或目标岗位。'
    return
  }
  loading.value = true
  error.value = ''
  try {
    resetSessionState()
    const response = await bridge.apiRequest<{ session: { id: string } }>(
      apiBaseUrl.value,
      '/api/career/live-interviews/sessions',
      {
        method: 'POST',
        body: {
          candidate_profile_id: candidateId.value || null,
          target_role_profile_id: targetId.value || null,
          asr_model_profile_id: asrModelId.value || null,
          answer_model_profile_id: answerModelId.value || null,
          interview_experience_ids: selectedInterviewIds.value,
        },
      },
    )
    await beginLiveSession(apiBaseUrl.value, response.session.id, microphoneId.value || undefined)
    await router.push(`/live-interview/session/${response.session.id}`)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法开始面试'
    sessionState.connection = 'error'
  } finally {
    loading.value = false
  }
}

onMounted(loadOptions)
</script>

<template>
  <main class="setup-shell">
    <section class="setup-intro">
      <div class="brand-mark" aria-hidden="true"><span></span><span></span><span></span><span></span></div>
      <p class="utility-label">Windows 10/11 · 双音轨实时辅助</p>
      <h1>让每个问题先被听清，<br />再给出可用的中文思路。</h1>
      <p class="intro-copy">系统声音固定识别为面试官，麦克风固定识别为应试者。原文支持中英混合，回答统一中文，专有名词保持原样。</p>
      <div class="privacy-note"><strong>采集边界</strong><span>点击“开始面试”后才采集；结束或断线立即停止；不保存原始音频。</span></div>
    </section>

    <section class="setup-panel" aria-labelledby="setup-title">
      <header><p class="step-label">开始准备</p><h2 id="setup-title">检查资料、模型与声音</h2></header>
      <label>服务地址<input v-model="apiBaseUrl" spellcheck="false" /></label>
      <div class="inline-actions"><button class="quiet-button" type="button" @click="openLogin">打开登录页</button><button class="quiet-button" type="button" :disabled="loading" @click="loadOptions">重新检查</button></div>
      <div class="field-grid">
        <label>当前简历<select v-model="candidateId"><option value="">暂不选择</option><option v-for="item in options?.candidate_profiles" :key="item.id" :value="item.id">{{ item.display_name }}</option></select></label>
        <label>目标岗位<select v-model="targetId"><option value="">暂不选择</option><option v-for="item in options?.target_roles" :key="item.id" :value="item.id">{{ item.company_name }} · {{ item.role_name }}</option></select></label>
        <label>实时转写<select v-model="asrModelId"><option value="">服务端 OpenAI 环境配置</option><option v-for="item in readyAsr" :key="item.id" :value="item.id">{{ item.display_name }}</option></select></label>
        <label>回答模型<select v-model="answerModelId"><option value="">免费模型自动选择</option><option v-for="item in readyAnswers" :key="item.id" :value="item.id">{{ item.display_name }}</option></select></label>
      </div>
      <label>麦克风<select v-model="microphoneId"><option v-for="item in microphones" :key="item.deviceId" :value="item.deviceId">{{ item.label || '麦克风' }}</option></select></label>
      <fieldset v-if="interviewMaterials.length" class="material-picker">
        <legend>面经资料（最多 5 份）</legend>
        <label v-for="item in interviewMaterials" :key="item.id" class="material-option"><input v-model="selectedInterviewIds" type="checkbox" :value="item.id" :disabled="!selectedInterviewIds.includes(item.id) && selectedInterviewIds.length >= 5" /><span><strong>{{ item.label }}</strong><small>{{ item.company }}</small></span></label>
      </fieldset>
      <div class="output-row"><span>系统输出</span><strong>Windows 当前默认播放设备</strong><small>开始时由系统授权窗口确认</small></div>
      <p v-if="error" class="error-message">{{ error }}</p>
      <button class="primary-button" type="button" :disabled="loading" @click="start">{{ loading ? '正在检查…' : '开始面试' }}</button>
    </section>
  </main>
</template>
