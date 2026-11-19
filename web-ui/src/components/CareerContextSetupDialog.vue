<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useDialogFocus } from '../use-dialog-focus.js'

import {
  nextResumeProcessingProgress,
  resumeUploadProgress
} from '../career-resume-progress.js'

const props = defineProps({
  open: { type: Boolean, default: false },
  candidateProfiles: { type: Array, default: () => [] },
  showResumeOwner: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
  initialContext: { type: Object, default: null },
  initialEntry: { type: String, default: '' },
  initialDraft: { type: Object, default: null }
})

const emit = defineEmits(['cancel', 'ready', 'profile-created', 'preserve-draft'])
useDialogFocus(() => props.open, '.context-dialog', cancel, () => !dialogBusy.value)

const step = ref(1)
const resumeMode = ref('existing')
const selectedCandidateId = ref('')
const selectedCandidate = ref(null)
const confirmedExisting = ref(false)
const resumeFile = ref(null)
const resumeInput = ref(null)
const resumePreview = ref(null)
const resumePreviewReviewed = ref(false)
const resumeDisplayName = ref('我的基准简历')
const parsingResume = ref(false)
const resumeProgress = ref(0)
const resumeProgressPhase = ref('idle')
const savingResume = ref(false)
const companyName = ref('')
const roleName = ref('')
const jobText = ref('')
const jobSourceLabel = ref('手动粘贴')
const jobFile = ref(null)
const jobInput = ref(null)
const parsingJob = ref(false)
const parsingJobUrl = ref(false)
const jobImporting = computed(() => parsingJob.value || parsingJobUrl.value)
const jobImportMode = ref('text')
const jobUrlText = ref('')
const jobSourceKind = ref('text')
const jobSourceUrl = ref('')
const jobFetchedAt = ref('')
const jobImportNotices = ref([])
const jobImportMessage = ref('')
let jobImportRevision = 0
let jobImportController = null
const savingTarget = ref(false)
const dialogBusy = computed(() => props.busy || parsingResume.value || jobImporting.value || savingResume.value || savingTarget.value)
const selectedTarget = ref(null)
const localError = ref('')
let activeResumeRequest = null
let resumeProgressTimer = null
const draftFields = { step, resumeMode, selectedCandidateId, selectedCandidate, confirmedExisting,
  resumeFile, resumePreview, resumePreviewReviewed, resumeDisplayName, companyName, roleName, jobText, jobSourceLabel,
  jobFile, selectedTarget, jobImportMode, jobUrlText, jobSourceKind, jobSourceUrl,
  jobFetchedAt, jobImportNotices, jobImportMessage }
function captureDraft() {
  return Object.fromEntries(Object.entries(draftFields).map(([key, state]) => [key, state.value]))
}
defineExpose({ captureDraft })

const selectedExistingCandidate = computed(() => (
  props.candidateProfiles.find((item) => item.id === selectedCandidateId.value)
  ?? props.initialContext?.candidate_profile
  ?? null
))
const resumeNotices = computed(() => resumePreview.value?.notices ?? [])
const resumeNeedsReview = computed(() => resumePreview.value?.parsing_status === 'needs_review' || resumeNotices.value.length > 0)
const canConfirmResume = computed(() => !parsingResume.value && (
  resumeMode.value === 'existing'
    ? Boolean(selectedExistingCandidate.value && confirmedExisting.value)
    : Boolean(resumePreview.value?.resume_outline?.trim() && resumeDisplayName.value.trim()
      && (!resumeNeedsReview.value || resumePreviewReviewed.value))
))
const canConfirmTarget = computed(() => (
  companyName.value.trim() && roleName.value.trim() && jobText.value.trim().length >= 20
))
const showResumeProgress = computed(() => resumeProgressPhase.value !== 'idle')
const resumeProgressCopy = computed(() => {
  const copy = {
    uploading: ['正在上传简历', '文件上传完成后会自动开始提取内容'],
    processing: ['正在提取简历内容', '正在识别文字与简历结构，请稍候'],
    complete: ['简历文本已提取', '已生成可编辑预览，请对照原简历核对完整性'],
    needs_review: ['简历内容待核对', '检测到识别不完整的风险，请查看下方具体提示并补全'],
    failed: ['简历解析未完成', '请根据下方提示检查文件后重试']
  }
  return copy[resumeProgressPhase.value] ?? ['', '']
})

watch(() => props.open, (open) => {
  if (!open) return
  resetDialog()
})

function resetDialog() {
  cancelJobImport()
  stopResumeProgress()
  activeResumeRequest?.abort()
  activeResumeRequest = null
  parsingResume.value = false
  localError.value = ''
  selectedTarget.value = props.initialContext?.target_role ?? null
  jobText.value = props.initialContext?.target_role?.job_text ?? ''
  jobFile.value = null
  jobImportMode.value = 'text'
  jobUrlText.value = ''
  jobSourceKind.value = props.initialContext?.target_role?.source_kind || 'text'
  jobSourceUrl.value = ''
  jobFetchedAt.value = ''
  jobImportNotices.value = []
  jobImportMessage.value = ''
  companyName.value = props.initialContext?.target_role?.company_name ?? ''
  roleName.value = props.initialContext?.target_role?.role_name ?? ''
  jobSourceLabel.value = props.initialContext?.target_role?.source_label || (props.initialContext ? '对话中更新' : '手动粘贴')
  resumePreview.value = null
  resumePreviewReviewed.value = false
  resumeFile.value = null
  resumeProgress.value = 0
  resumeProgressPhase.value = 'idle'
  confirmedExisting.value = Boolean(props.initialContext?.candidate_profile)
  selectedCandidate.value = props.initialContext?.candidate_profile ?? null
  selectedCandidateId.value = selectedCandidate.value?.id ?? props.candidateProfiles[0]?.id ?? ''
  resumeMode.value = selectedCandidateId.value ? 'existing' : 'upload'
  step.value = 1
  if (resumeInput.value) resumeInput.value.value = ''
  if (jobInput.value) jobInput.value.value = ''
  if (props.initialDraft) {
    for (const [key, state] of Object.entries(draftFields)) {
      if (Object.hasOwn(props.initialDraft, key)) state.value = props.initialDraft[key]
    }
  }
  if (props.initialEntry === 'target') step.value = 2
  if (props.initialEntry === 'resume') step.value = 1
}

function skipResume() {
  selectedCandidate.value = props.initialContext?.candidate_profile ?? null
  localError.value = ''
  step.value = 2
}

function skipTarget() {
  selectedTarget.value = props.initialContext?.target_role ?? null
  localError.value = ''
  step.value = 3
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options)
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload.detail ?? `请求失败：${response.status}`)
  return payload
}

function chooseResumeMode(mode) {
  if (parsingResume.value) return
  resumeMode.value = mode
  confirmedExisting.value = false
  localError.value = ''
}

function handleResumeFile(event) {
  resumeFile.value = event.target.files?.[0] ?? null
  resumePreview.value = null
  resumePreviewReviewed.value = false
  stopResumeProgress()
  resumeProgress.value = 0
  resumeProgressPhase.value = 'idle'
  if (resumeFile.value) {
    resumeDisplayName.value = resumeFile.value.name.replace(/\.[^.]+$/, '') || '我的基准简历'
  }
}

function stopResumeProgress() {
  if (resumeProgressTimer !== null) {
    window.clearInterval(resumeProgressTimer)
    resumeProgressTimer = null
  }
}

function startResumeProcessing() {
  resumeProgressPhase.value = 'processing'
  resumeProgress.value = Math.max(32, resumeProgress.value)
  if (resumeProgressTimer !== null) return
  resumeProgressTimer = window.setInterval(() => {
    resumeProgress.value = nextResumeProcessingProgress(resumeProgress.value)
  }, 480)
}

function parseResumeRequest(formData) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    activeResumeRequest = xhr
    xhr.open('POST', '/api/career/candidate-profiles/parse')
    xhr.responseType = 'json'
    xhr.upload.addEventListener('progress', (event) => {
      if (!event.lengthComputable || resumeProgressPhase.value !== 'uploading') return
      resumeProgress.value = Math.max(
        resumeProgress.value,
        resumeUploadProgress(event.loaded, event.total)
      )
    })
    xhr.upload.addEventListener('load', startResumeProcessing)
    xhr.addEventListener('load', () => {
      activeResumeRequest = null
      const payload = xhr.response && typeof xhr.response === 'object' ? xhr.response : {}
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(payload)
        return
      }
      reject(new Error(payload.detail ?? `请求失败：${xhr.status}`))
    })
    xhr.addEventListener('error', () => {
      activeResumeRequest = null
      reject(new Error('网络连接异常，简历上传失败'))
    })
    xhr.addEventListener('abort', () => {
      activeResumeRequest = null
      reject(new Error('简历解析已取消'))
    })
    xhr.send(formData)
  })
}

async function parseResume() {
  if (!resumeFile.value || parsingResume.value) return
  parsingResume.value = true
  localError.value = ''
  resumePreview.value = null
  resumePreviewReviewed.value = false
  stopResumeProgress()
  resumeProgress.value = 4
  resumeProgressPhase.value = 'uploading'
  try {
    const formData = new FormData()
    formData.append('resume_file', resumeFile.value)
    resumePreview.value = await parseResumeRequest(formData)
    stopResumeProgress()
    resumeProgress.value = 100
    resumeProgressPhase.value = resumeNeedsReview.value ? 'needs_review' : 'complete'
  } catch (error) {
    stopResumeProgress()
    resumeProgressPhase.value = 'failed'
    localError.value = error instanceof Error ? error.message : '简历解析失败'
  } finally {
    parsingResume.value = false
  }
}

onBeforeUnmount(() => {
  cancelJobImport()
  stopResumeProgress()
  activeResumeRequest?.abort()
})

async function confirmResume() {
  if (!canConfirmResume.value) return
  localError.value = ''
  if (resumeMode.value === 'existing') {
    selectedCandidate.value = selectedExistingCandidate.value
    step.value = 2
    return
  }
  savingResume.value = true
  try {
    selectedCandidate.value = await requestJson('/api/career/candidate-profiles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        display_name: resumeDisplayName.value.trim(),
        source_filename: resumePreview.value.source_filename,
        resume_outline: resumePreview.value.resume_outline
      })
    })
    emit('profile-created', selectedCandidate.value)
    step.value = 2
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '基准简历保存失败'
  } finally {
    savingResume.value = false
  }
}

function handleJobFile(event) {
  jobFile.value = event.target.files?.[0] ?? null
  if (jobFile.value) void parseJobFile()
}

function cancelJobImport() {
  jobImportRevision += 1
  jobImportController?.abort()
  jobImportController = null
  parsingJob.value = false
  parsingJobUrl.value = false
}

function chooseJobImportMode(mode) {
  if (mode === jobImportMode.value) return
  cancelJobImport()
  jobImportMode.value = mode
  jobImportMessage.value = ''
  localError.value = ''
}

async function importJob(url, options, kind) {
  if (jobImporting.value) return
  if ((jobText.value.trim() || companyName.value.trim() || roleName.value.trim())
    && !window.confirm('导入会替换当前职位正文和可提取的公司、岗位信息，是否继续？')) {
    jobImportMessage.value = '已取消导入，当前正文未改变。'
    return
  }
  cancelJobImport()
  const revision = jobImportRevision
  const before = { text: jobText.value, company: companyName.value, role: roleName.value }
  jobImportController = new AbortController()
  parsingJob.value = kind === 'file'
  parsingJobUrl.value = kind === 'url'
  localError.value = ''
  jobImportMessage.value = ''
  try {
    const payload = await requestJson(url, { ...options, signal: jobImportController.signal })
    if (revision !== jobImportRevision || !props.open) return
    if (jobText.value !== before.text) {
      jobImportMessage.value = '解析已完成，但正文已有新编辑，未覆盖当前内容。请核对后重新导入。'
      return
    }
    if (!payload.job_text?.trim()) throw new Error('未读取到职位正文，请上传截图或粘贴完整描述。')
    jobText.value = payload.job_text
    if (companyName.value === before.company && Object.hasOwn(payload, 'company_name')) companyName.value = payload.company_name || ''
    if (roleName.value === before.role && Object.hasOwn(payload, 'role_name')) roleName.value = payload.role_name || ''
    jobSourceKind.value = kind
    jobSourceLabel.value = payload.source_label || payload.source_filename || (kind === 'file' ? jobFile.value?.name : '') || '公开职位链接'
    jobSourceUrl.value = payload.source_url || ''
    jobFetchedAt.value = payload.fetched_at || ''
    jobImportNotices.value = payload.notices || []
    jobImportMessage.value = '导入完成。请核对公司、岗位和正文，确认后才会保存。'
    selectedTarget.value = null
  } catch (error) {
    if (revision !== jobImportRevision || error?.name === 'AbortError') return
    localError.value = error instanceof Error ? error.message : '导入未完成，请改用截图或粘贴职位正文。'
  } finally {
    if (revision === jobImportRevision) {
      jobImportController = null
      parsingJob.value = false
      parsingJobUrl.value = false
    }
  }
}

async function parseJobFile() {
  if (!jobFile.value || jobImporting.value) return
  const formData = new FormData()
  formData.append('job_file', jobFile.value)
  await importJob('/api/career/target-role-profiles/parse-file', { method: 'POST', body: formData }, 'file')
}

async function parseJobUrl() {
  if (!jobUrlText.value.trim() || jobImporting.value) return
  await importJob('/api/career/target-role-profiles/parse-url', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url: jobUrlText.value.trim() })
  }, 'url')
}

async function confirmTarget() {
  if (!canConfirmTarget.value || savingTarget.value || jobImporting.value) return
  savingTarget.value = true
  localError.value = ''
  try {
    selectedTarget.value = await requestJson('/api/career/target-role-profiles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        company_name: companyName.value.trim(),
        role_name: roleName.value.trim(),
        source_kind: jobSourceKind.value,
        source_label: jobSourceLabel.value,
        job_text: jobText.value.trim()
      })
    })
    step.value = 3
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '目标岗位保存失败'
  } finally {
    savingTarget.value = false
  }
}

function finishSetup() {
  if (props.busy) return
  emit('ready', {
    candidateProfile: selectedCandidate.value,
    targetRole: selectedTarget.value
  })
}

function cancel() {
  if (!dialogBusy.value) {
    emit('preserve-draft', captureDraft())
    emit('cancel')
  }
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="context-backdrop" @mousedown.self="cancel">
      <section class="context-dialog" role="dialog" aria-modal="true" aria-label="建立求职上下文">
        <header class="context-dialog-header">
          <div>
            <h2>补充求职资料</h2>
            <p>简历和目标岗位均为可选项；保存后只影响后续回答，历史消息保持不变。</p>
          </div>
          <button type="button" aria-label="关闭" :disabled="dialogBusy" @click="cancel">×</button>
        </header>

        <nav class="context-steps" aria-label="配置进度">
          <span :class="{ active: step === 1, done: step > 1 }"><i>1</i>可选简历</span>
          <b></b>
          <span :class="{ active: step === 2, done: step > 2 }"><i>2</i>可选岗位</span>
          <b></b>
          <span :class="{ active: step === 3 }"><i>3</i>确认资料</span>
        </nav>

        <main class="context-dialog-body">
          <section v-if="step === 1" class="context-step-panel">
            <div class="step-heading">
              <h3>选择或上传简历</h3>
              <span>可选</span>
            </div>
            <div class="mode-switch">
              <button type="button" :class="{ active: resumeMode === 'existing' }" :disabled="!candidateProfiles.length || parsingResume" @click="chooseResumeMode('existing')">使用已有简历</button>
              <button type="button" :class="{ active: resumeMode === 'upload' }" :disabled="parsingResume" @click="chooseResumeMode('upload')">上传新简历</button>
              <button type="button" :class="{ active: resumeMode === 'image' }" :disabled="parsingResume" @click="chooseResumeMode('image')">图片识别</button>
            </div>

            <div v-if="resumeMode === 'existing'" class="existing-resumes">
              <label v-for="profile in candidateProfiles" :key="profile.id" :class="{ selected: selectedCandidateId === profile.id }">
                <input v-model="selectedCandidateId" type="radio" :value="profile.id" @change="confirmedExisting = false" />
                <span>
                  <strong>{{ profile.display_name }}</strong>
                  <small>{{ profile.source_filename }} · 简历版本 {{ profile.version }}</small>
                  <small v-if="showResumeOwner" class="resume-owner">所属账户：{{ profile.owner_display_name || '未知用户' }}</small>
                </span>
                <em>已解析</em>
              </label>
              <label class="confirmation-check">
                <input v-model="confirmedExisting" type="checkbox" />
                <span>我确认使用这份简历作为本次对话的个人事实依据</span>
              </label>
            </div>

            <div v-else class="upload-workbench">
              <input ref="resumeInput" class="hidden-input" type="file" :accept="resumeMode === 'image' ? 'image/jpeg,image/png,image/webp' : 'application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,image/jpeg,image/png,image/webp'" @change="handleResumeFile" />
              <button class="file-drop" type="button" :disabled="parsingResume" @click="resumeInput?.click()">
                <span>↑</span><strong>{{ resumeFile?.name || (resumeMode === 'image' ? '从相册选择简历截图' : '选择一份基准简历') }}</strong><small>{{ resumeMode === 'image' ? '单张清晰的 JPG、PNG 或 WebP 图片，最大 10MB' : 'PDF、Word 或清晰图片，最大 10MB' }}</small>
              </button>
              <button v-if="resumeFile" class="parse-button" :class="{ 'is-loading': parsingResume }" type="button" :disabled="parsingResume" :aria-busy="parsingResume" @click="parseResume">{{ parsingResume ? '正在解析简历…' : resumePreview ? '重新解析' : '解析简历' }}</button>
              <div v-if="showResumeProgress" class="resume-parse-progress" :class="`is-${resumeProgressPhase}`" role="status" aria-live="polite">
                <header>
                  <span aria-hidden="true"></span>
                  <strong>{{ resumeProgressCopy[0] }}</strong>
                  <em>{{ resumeProgress }}%</em>
                </header>
                <div class="resume-progress-track" role="progressbar" :aria-label="resumeProgressCopy[0]" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="resumeProgress">
                  <i :style="{ width: `${resumeProgress}%` }"></i>
                </div>
                <small>{{ resumeProgressCopy[1] }}</small>
              </div>
              <div v-if="resumePreview" class="parse-preview">
                <div><label>简历名称</label><input v-model="resumeDisplayName" maxlength="120" /></div>
                <header><strong>解析预览</strong><small>{{ resumePreview.source_character_count }} 字 · {{ resumePreview.sections?.length || 0 }} 个区块</small></header>
                <ul v-if="resumeNotices.length" class="resume-parse-notices" role="alert"><li v-for="notice in resumeNotices" :key="notice">{{ notice }}</li></ul>
                <textarea v-model="resumePreview.resume_outline" rows="10" aria-label="基准简历解析预览" @input="resumePreviewReviewed = false"></textarea>
                <p>请对照原简历核对内容是否完整、准确。确认保存后，模型将以这份内容作为个人事实依据。</p>
                <label v-if="resumeNeedsReview" class="confirmation-check">
                  <input v-model="resumePreviewReviewed" type="checkbox" />
                  <span>我已对照原简历核对并补全缺失内容</span>
                </label>
              </div>
            </div>
          </section>

          <section v-else-if="step === 2" class="context-step-panel target-step">
            <div class="step-heading">
              <h3>添加目标岗位</h3>
              <span>可选</span>
            </div>
            <div class="mode-switch job-import-tabs" aria-label="职位导入方式">
              <button type="button" :class="{ active: jobImportMode === 'text' }" @click="chooseJobImportMode('text')">粘贴正文</button>
              <button type="button" :class="{ active: jobImportMode === 'file' }" @click="chooseJobImportMode('file')">截图或文件</button>
              <button type="button" :class="{ active: jobImportMode === 'url' }" @click="chooseJobImportMode('url')">职位链接</button>
            </div>
            <p v-if="jobImportMode === 'text'" class="import-hint">将完整职位描述粘贴到下方，保留任职要求和加分项。</p>
            <div v-if="jobImportMode === 'file'" class="job-file-row">
              <input ref="jobInput" class="hidden-input" type="file" accept="application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,image/jpeg,image/png,image/webp" @change="handleJobFile" />
              <button type="button" :disabled="jobImporting" @click="jobInput?.click()">{{ parsingJob ? '正在解析岗位文件…' : '上传岗位截图或文件' }}</button>
              <span v-if="jobFile">{{ jobFile.name }}</span>
              <small>支持 PDF、Word 或岗位截图，解析后可修改正文。</small>
            </div>
            <div v-if="jobImportMode === 'url'" class="job-url-import">
              <label for="context-job-url">职位详情链接</label>
              <div><input id="context-job-url" v-model="jobUrlText" type="url" inputmode="url" maxlength="2000" placeholder="粘贴公开的 HTTPS 职位详情链接" /><button type="button" :disabled="jobImporting || !jobUrlText.trim()" @click="parseJobUrl">{{ parsingJobUrl ? '正在读取…' : '解析链接' }}</button></div>
              <p class="import-hint">仅读取公开页面。遇到登录或验证限制，请切换到截图／文件或粘贴正文。</p>
            </div>
            <div v-if="jobImportMessage || jobImporting" class="job-import-feedback" role="status">{{ jobImporting ? '正在读取职位内容，完成后请在下方校对。' : jobImportMessage }}</div>
            <div v-if="jobSourceUrl" class="job-import-source"><a :href="jobSourceUrl" target="_blank" rel="noopener noreferrer">查看来源页面</a><span v-if="jobFetchedAt">获取时间：{{ new Date(jobFetchedAt).toLocaleString('zh-CN') }}</span><span>是否仍在招：待核实</span></div>
            <ul v-if="jobImportNotices.length" class="job-import-notices"><li v-for="notice in jobImportNotices" :key="notice">{{ notice }}</li></ul>
            <div class="target-meta-grid">
              <label><span>公司</span><input v-model="companyName" maxlength="120" placeholder="例如：字节跳动" /></label>
              <label><span>岗位</span><input v-model="roleName" maxlength="160" placeholder="例如：后端开发工程师" /></label>
            </div>
            <label class="jd-editor">
              <span>职位信息</span>
              <textarea v-model="jobText" aria-label="职位信息正文" maxlength="30000" rows="13" placeholder="粘贴完整职位描述、任职要求和加分项。系统只根据这里确认的内容计算匹配画像。"></textarea>
              <small>{{ jobText.length }} / 30000</small>
            </label>
          </section>

          <section v-else class="context-step-panel context-review">
            <div class="review-mark">✓</div>
            <h3>{{ selectedCandidate || selectedTarget ? '求职资料已准备好' : '不添加资料也可以继续' }}</h3>
            <p>{{ selectedCandidate || selectedTarget ? '保存后，后续回答会读取下方已有资料。' : '你可以先直接对话，之后再随时回来补充简历或 JD。' }}</p>
            <div v-if="selectedCandidate || selectedTarget" class="review-pair" :class="{ single: !selectedCandidate || !selectedTarget }">
              <article v-if="selectedCandidate"><small>基准简历</small><strong>{{ selectedCandidate.display_name }}</strong><span>{{ selectedCandidate.source_filename }} · 简历版本 {{ selectedCandidate.version }}</span></article>
              <i v-if="selectedCandidate && selectedTarget">×</i>
              <article v-if="selectedTarget"><small>目标岗位</small><strong>{{ selectedTarget.company_name }} · {{ selectedTarget.role_name }}</strong><span>{{ selectedTarget.requirements.length }} 条可复核要求 · 岗位版本 {{ selectedTarget.version }}</span></article>
            </div>
            <div v-if="selectedTarget" class="requirement-preview">
              <span v-for="item in selectedTarget.requirements.slice(0, 6)" :key="item.id">{{ item.text }}</span>
              <em v-if="selectedTarget.requirements.length > 6">+{{ selectedTarget.requirements.length - 6 }}</em>
            </div>
            <p v-if="selectedCandidate && selectedTarget" class="review-note">两份资料齐全后才会计算匹配画像，且不会把它解释为录用概率。</p>
          </section>

          <p v-if="localError" class="context-error" role="alert">{{ localError }}</p>
        </main>

        <footer class="context-dialog-footer">
          <button v-if="step > 1" type="button" class="secondary" :disabled="dialogBusy" @click="step -= 1">上一步</button>
          <span v-else></span>
          <div class="footer-actions">
            <button v-if="step === 1" type="button" class="secondary" :disabled="busy || parsingResume" @click="skipResume">{{ initialContext?.candidate_profile ? '保留当前简历' : '暂不添加简历' }}</button>
            <button v-if="step === 1" type="button" class="primary" :disabled="!canConfirmResume || savingResume" @click="confirmResume">{{ savingResume ? '正在保存…' : '确认简历并继续' }}</button>
            <button v-else-if="step === 2" type="button" class="secondary" :disabled="busy || jobImporting" @click="skipTarget">{{ initialContext?.target_role ? '保留当前岗位' : '暂不添加岗位' }}</button>
            <button v-if="step === 2" type="button" class="primary" :disabled="!canConfirmTarget || savingTarget || jobImporting" @click="confirmTarget">{{ savingTarget ? '正在保存…' : '确认岗位并继续' }}</button>
            <button v-if="step === 3" type="button" class="primary start" :disabled="busy" @click="finishSetup">{{ busy ? '正在更新资料…' : '保存并返回对话' }}</button>
          </div>
        </footer>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.resume-parse-notices{margin:0;border:1px solid #e9cb8d;border-radius:10px;background:#fff9ec;padding:10px 12px 10px 30px;color:#875e1c;font-size:12px;line-height:1.7}
.resume-parse-progress.is-needs_review{border-color:#e9cb8d;background:#fff9ec}
.resume-parse-progress.is-needs_review strong,.resume-parse-progress.is-needs_review em{color:#875e1c}
.resume-parse-progress.is-needs_review header>span::after,.resume-parse-progress.is-needs_review .resume-progress-track i{background:#bb852d;animation:none;box-shadow:none}
.import-hint{margin:0;color:#647695;font-size:13px;line-height:1.6}.job-url-import{display:grid;gap:8px;min-width:0}.job-url-import>label{font-size:13px;font-weight:800;color:#40536f}.job-url-import>div{display:flex;gap:8px}.job-url-import input{min-width:0;flex:1;border:1px solid #ccd9e9;border-radius:10px;padding:10px 12px;font:inherit}.job-url-import button{flex:none;min-height:44px;border:1px solid #bcd1eb;border-radius:10px;background:#f5f9ff;color:#145baf;padding:8px 14px;font-weight:800;cursor:pointer}.job-url-import button:disabled{opacity:.5;cursor:not-allowed}.job-import-feedback{border-radius:10px;background:#edf6ff;padding:10px 12px;color:#245786;font-size:13px;line-height:1.6}.job-import-source{display:flex;flex-wrap:wrap;gap:8px 14px;color:#647695;font-size:12px;overflow-wrap:anywhere}.job-import-source a{color:#145baf}.job-import-notices{margin:0;padding-left:18px;color:#647695;font-size:12px;line-height:1.6}
.context-backdrop{position:fixed;z-index:1200;inset:0;display:grid;place-items:center;background:rgba(5,25,58,.52);padding:28px;backdrop-filter:blur(8px)}
.context-dialog{display:flex;width:min(920px,calc(100vw - 56px));max-height:calc(100dvh - 56px);flex-direction:column;overflow:hidden;border:1px solid #c9d9ef;border-radius:24px;background:#fff;box-shadow:0 28px 90px rgba(5,30,70,.24);color:#102343}
.context-dialog-header{display:flex;align-items:flex-start;justify-content:space-between;padding:24px 28px 18px;border-bottom:1px solid #e4edf8;background:linear-gradient(130deg,#f7faff 0%,#eef5ff 100%)}
.context-dialog-header h2{margin:3px 0 7px;font-size:26px;letter-spacing:-.04em}.context-dialog-header p{margin:0;color:#647695}.context-kicker{color:#0870df!important;font:800 11px/1.2 ui-monospace,SFMono-Regular,Consolas,monospace;letter-spacing:.14em}.context-dialog-header>button{display:grid;width:36px;height:36px;place-items:center;border:1px solid #d4e0f0;border-radius:50%;background:#fff;color:#657792;font-size:23px;cursor:pointer}.context-dialog-header>button:disabled{cursor:not-allowed;opacity:.45}
.context-steps{display:grid;grid-template-columns:auto 1fr auto 1fr auto;align-items:center;gap:12px;padding:16px 28px;border-bottom:1px solid #edf2f8;background:#fbfdff}.context-steps span{display:flex;align-items:center;gap:7px;color:#8a98ad;font-size:13px;font-weight:800}.context-steps i{display:grid;width:25px;height:25px;place-items:center;border:1px solid #cfd9e7;border-radius:50%;background:#fff;font-style:normal}.context-steps span.active{color:#075ec7}.context-steps span.active i{border-color:#0a67db;background:#0a67db;color:#fff}.context-steps span.done{color:#28734c}.context-steps span.done i{border-color:#9bc7ad;background:#eaf7ef;color:#28734c}.context-steps b{height:1px;background:#dce5f1}
.context-dialog-body{min-height:0;overflow:auto;padding:25px 28px}.context-step-panel{display:grid;gap:18px}.step-heading{display:flex;align-items:flex-start;justify-content:space-between}.step-heading h3{margin:3px 0 0;font-size:20px;letter-spacing:-.025em}.step-heading small{color:#7485a0;font:750 11px ui-monospace,monospace;letter-spacing:.1em}.step-heading>span{border-radius:999px;background:#eaf3ff;color:#0862c6;padding:5px 9px;font-size:11px;font-weight:800}
.mode-switch{box-sizing:border-box;display:inline-flex;min-width:0;max-width:100%;width:max-content;border:1px solid #d6e1ef;border-radius:11px;background:#f3f7fc;padding:3px}.mode-switch button{min-width:0;border:0;border-radius:8px;background:transparent;padding:9px 15px;color:#71819a;font-weight:800;cursor:pointer}.mode-switch button.active{background:#fff;color:#075fc9;box-shadow:0 2px 9px rgba(30,69,116,.09)}.mode-switch button:disabled{opacity:.42;cursor:not-allowed}
.existing-resumes{display:grid;gap:9px}.existing-resumes>label:not(.confirmation-check){display:flex;align-items:center;gap:12px;border:1px solid #dce5f0;border-radius:14px;padding:13px 15px;cursor:pointer}.existing-resumes>label.selected{border-color:#8ab8ef;background:#f3f8ff}.existing-resumes input{accent-color:#0870df}.existing-resumes span{display:grid;gap:3px}.existing-resumes small{color:#7b8ba2}.existing-resumes .resume-owner{color:#3c6696;font-weight:750}.existing-resumes em{margin-left:auto;border-radius:999px;background:#ebf7ef;color:#28764c;padding:4px 8px;font-size:11px;font-style:normal;font-weight:800}.confirmation-check{display:flex;align-items:center;gap:8px;margin-top:5px;color:#51647f;font-size:13px}
.upload-workbench{display:grid;gap:12px}.hidden-input{display:none}.file-drop{display:grid;min-height:130px;place-items:center;align-content:center;gap:5px;border:1px dashed #90b8e7;border-radius:16px;background:#f7fbff;color:#183d6b;cursor:pointer}.file-drop>span{display:grid;width:34px;height:34px;place-items:center;border-radius:50%;background:#e1efff;color:#0768d7;font-size:20px}.file-drop small{color:#7b8ca3}.file-drop:disabled{cursor:not-allowed;filter:saturate(.72);opacity:.72}.parse-button{display:inline-flex;align-items:center;gap:8px;justify-self:start;border:0;border-radius:10px;background:#0a67db;color:#fff;padding:10px 16px;font-weight:800;box-shadow:0 7px 16px rgba(10,103,219,.17);cursor:pointer}.parse-button.is-loading::before{width:13px;height:13px;border:2px solid rgba(255,255,255,.45);border-top-color:#fff;border-radius:50%;content:'';animation:resume-button-spin .8s linear infinite}.parse-button:disabled{cursor:not-allowed;opacity:.82}.resume-parse-progress{display:grid;gap:9px;border:1px solid #cfe0f5;border-radius:14px;background:linear-gradient(135deg,#f8fbff 0%,#f1f7ff 100%);padding:13px 14px;box-shadow:inset 0 1px 0 rgba(255,255,255,.8)}.resume-parse-progress header{display:grid;grid-template-columns:24px minmax(0,1fr) auto;align-items:center;gap:9px}.resume-parse-progress header>span{display:grid;width:24px;height:24px;place-items:center;border-radius:8px;background:#e3efff}.resume-parse-progress header>span::after{width:7px;height:7px;border-radius:50%;background:#0a67db;box-shadow:0 0 0 5px rgba(10,103,219,.11);content:'';animation:resume-progress-pulse 1.35s ease-in-out infinite}.resume-parse-progress strong{color:#173c68;font-size:13px}.resume-parse-progress em{color:#176acb;font:800 12px/1 ui-monospace,SFMono-Regular,Consolas,monospace;font-style:normal;font-variant-numeric:tabular-nums}.resume-parse-progress>small{color:#7187a3;font-size:11px;line-height:1.5}.resume-progress-track{height:8px;overflow:hidden;border-radius:999px;background:#dce8f6;box-shadow:inset 0 1px 2px rgba(39,82,132,.11)}.resume-progress-track i{position:relative;display:block;height:100%;overflow:hidden;border-radius:inherit;background:linear-gradient(90deg,#0966d8 0%,#3c91ec 68%,#6eb6ff 100%);box-shadow:0 0 10px rgba(28,126,227,.25);transition:width .42s cubic-bezier(.22,.61,.36,1)}.resume-progress-track i::after{position:absolute;inset:0;width:42%;background:linear-gradient(90deg,transparent,rgba(255,255,255,.55),transparent);content:'';transform:translateX(-130%);animation:resume-progress-scan 1.8s ease-in-out infinite}.resume-parse-progress.is-complete{border-color:#bfe2cd;background:linear-gradient(135deg,#f8fffb 0%,#effaf3 100%)}.resume-parse-progress.is-complete header>span{background:#dff3e7}.resume-parse-progress.is-complete header>span::after{background:#2d8a57;box-shadow:none;animation:none}.resume-parse-progress.is-complete strong,.resume-parse-progress.is-complete em{color:#28734c}.resume-parse-progress.is-complete .resume-progress-track{background:#dcefe3}.resume-parse-progress.is-complete .resume-progress-track i{background:#39a369;box-shadow:none}.resume-parse-progress.is-failed{border-color:#efcaca;background:#fff8f8}.resume-parse-progress.is-failed header>span{background:#fde7e7}.resume-parse-progress.is-failed header>span::after{background:#c34d4d;box-shadow:none;animation:none}.resume-parse-progress.is-failed strong,.resume-parse-progress.is-failed em{color:#a63e3e}.resume-parse-progress.is-failed .resume-progress-track i{background:#d36666;box-shadow:none}.parse-preview{display:grid;gap:9px;border:1px solid #d9e4f2;border-radius:15px;background:#fbfdff;padding:15px}.parse-preview>div{display:grid;grid-template-columns:100px 1fr;align-items:center;gap:10px}.parse-preview input,.target-meta-grid input,.jd-editor textarea,.parse-preview textarea{width:100%;box-sizing:border-box;border:1px solid #ccd9e9;border-radius:10px;background:#fff;padding:10px 12px;color:#142b4c;font:inherit}.parse-preview header{display:flex;justify-content:space-between}.parse-preview header small,.parse-preview>p{color:#7b8ba2;font-size:12px}.parse-preview textarea{resize:vertical;font:12px/1.65 ui-monospace,SFMono-Regular,Consolas,monospace}
.target-meta-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.target-meta-grid label,.jd-editor{display:grid;gap:7px;color:#40536f;font-size:13px;font-weight:800}.jd-editor{position:relative}.jd-editor textarea{min-height:250px;resize:vertical;line-height:1.65;font-weight:500}.jd-editor>small{position:absolute;right:11px;bottom:9px;color:#93a0b2;font-size:11px}.job-file-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.job-file-row button{border:1px solid #bcd1eb;border-radius:9px;background:#f5f9ff;color:#145baf;padding:9px 12px;font-weight:800;cursor:pointer}.job-file-row span{color:#274a73;font-size:13px}.job-file-row small{color:#8997aa;font-size:12px}
.context-review{justify-items:center;padding:10px 0 5px;text-align:center}.review-mark{display:grid;width:54px;height:54px;place-items:center;border-radius:18px;background:#e9f7ef;color:#28764c;font-size:25px;font-weight:900}.context-review h3{margin:0;font-size:24px}.context-review>p{margin:0;color:#71809a}.review-pair{display:grid;width:100%;grid-template-columns:1fr auto 1fr;align-items:center;gap:16px}.review-pair article{display:grid;min-height:104px;align-content:center;gap:5px;border:1px solid #dbe6f3;border-radius:15px;background:#f8fbff;padding:15px;text-align:left}.review-pair small{color:#75859e;font-size:11px;font-weight:800}.review-pair strong{font-size:16px}.review-pair span{color:#75859e;font-size:12px}.review-pair>i{color:#93a2b8;font-style:normal}.requirement-preview{display:flex;justify-content:center;gap:7px;flex-wrap:wrap}.requirement-preview span,.requirement-preview em{max-width:220px;overflow:hidden;border-radius:999px;background:#edf4fd;color:#31577f;padding:6px 9px;font-size:11px;font-style:normal;text-overflow:ellipsis;white-space:nowrap}.review-note{border-left:3px solid #e0a13c;background:#fff8e9;padding:9px 12px;color:#74521d!important;font-size:12px;text-align:left}
.review-pair.single{max-width:520px;grid-template-columns:minmax(0,1fr)}
.context-error{margin:0;border-radius:10px;background:#fff0f0;color:#a63e3e;padding:10px 12px;font-size:13px}.context-dialog-footer{display:flex;align-items:center;justify-content:space-between;border-top:1px solid #e4ebf4;background:#fbfdff;padding:16px 28px}.context-dialog-footer button{min-width:120px;border-radius:10px;padding:11px 17px;font-weight:850;cursor:pointer}.context-dialog-footer .secondary{border:1px solid #cad8e9;background:#fff;color:#536986}.context-dialog-footer .primary{border:1px solid #0865d5;background:#0865d5;color:#fff}.context-dialog-footer .start{min-width:170px}.context-dialog-footer button:disabled{opacity:.45;cursor:not-allowed}
.footer-actions{display:flex;align-items:center;justify-content:flex-end;gap:9px}
button:focus-visible,input:focus-visible,textarea:focus-visible{outline:3px solid rgba(9,105,219,.2);outline-offset:2px}
@keyframes resume-button-spin{to{transform:rotate(360deg)}}
@keyframes resume-progress-pulse{50%{transform:scale(.72);opacity:.62}}
@keyframes resume-progress-scan{55%,100%{transform:translateX(340%)}}
@media (prefers-reduced-motion:reduce){.parse-button.is-loading::before,.resume-parse-progress header>span::after,.resume-progress-track i::after{animation:none}.resume-progress-track i{transition:none}}
@media (max-width:760px){.context-backdrop{align-items:end;padding:0}.context-dialog{width:100%;max-height:94dvh;border-radius:22px 22px 0 0}.context-dialog-header,.context-dialog-body,.context-dialog-footer{padding-right:18px;padding-left:18px}.context-steps{padding-right:18px;padding-left:18px}.context-steps span{font-size:0}.context-steps span i{font-size:12px}.target-meta-grid,.review-pair{grid-template-columns:1fr}.review-pair>i{display:none}}
@media(max-width:760px){
  .context-dialog{width:100%;height:auto;max-height:min(94dvh,var(--dialog-visible-height,var(--career-visible-height,100dvh)));border:0;border-radius:20px 20px 0 0}
  .context-dialog-header{flex:none;padding:max(12px,env(safe-area-inset-top)) 16px 12px}
  .context-dialog-header h2{font-size:20px}
  .context-dialog-header p{font-size:14px;line-height:1.6}
  .context-dialog-header>button{width:44px;height:44px;flex:none}
  .context-steps{flex:none;padding:10px 16px}
  .context-dialog-body{min-height:0;flex:0 1 auto;padding:16px}
  .context-step-panel{gap:14px}
  .step-heading{gap:12px}
  .step-heading h3{font-size:18px;line-height:1.5}
  .step-heading>span{flex:none}
  .jd-editor textarea{box-sizing:border-box;height:clamp(140px,20dvh,200px);min-height:140px;max-height:200px;line-height:1.65}
  .file-drop strong,.job-file-row>span{max-width:100%;overflow-wrap:anywhere}
  .context-dialog-body input:not([type=checkbox]):not([type=radio]),.context-dialog-body select,.context-dialog-body textarea{font-size:16px;min-width:0;max-width:100%}
  .context-dialog-footer{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);flex:none;gap:8px;padding:10px 16px max(10px,env(safe-area-inset-bottom))}
  .context-dialog-footer>span{display:none}
  .context-dialog-footer .footer-actions{display:contents}
  .context-dialog-footer button{min-width:0;min-height:48px;padding:8px 12px;font-size:14px}
  .context-dialog-footer .primary,.context-dialog-footer:has(>span) .secondary,.context-dialog-footer:not(:has(.footer-actions .secondary))>.secondary{grid-column:1/-1}
  .context-step-panel .mode-switch{width:100%;display:flex}
  .mode-switch button{flex:1;min-height:44px}
}
</style>
