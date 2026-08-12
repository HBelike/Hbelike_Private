<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const conversations = ref([])
const selectedConversation = ref(null)
const messages = ref([])
const modelProfiles = ref([])
const freeModelCatalog = ref([])
const loading = ref(false)
const sending = ref(false)
const creating = ref(false)
const savingModel = ref(false)
const testingConnection = ref(false)
const showJobUrl = ref(false)
const showModelDialog = ref(false)
const modelDialogMode = ref('list')
const testedConnectionFingerprint = ref('')
const connectionTestMessage = ref('')
const connectionTestError = ref('')
const connectionSaveError = ref('')
const connectionConfigRef = ref(null)
const messageText = ref('')
const interviewMentionQuery = ref('')
const interviewMentionResults = ref([])
const selectedInterviewReferences = ref([])
const jobUrl = ref('')
const resumeFile = ref(null)
const resumeInput = ref(null)
const selectionMode = ref('free_quota_first')
const selectedProfileId = ref('')
const feedback = ref('')
const errorMessage = ref('')
const lastTurn = ref(null)
const streamedAssistantText = ref('')
const streamStatus = ref('')
const streamProgress = ref([])
const activeTemporaryMessageId = ref('')
const modelForm = ref(emptyModelForm())

let errorToastTimer = null
let conversationSelectionRequestId = 0
let temporaryMessageSequence = 0
let turnRecoveryPollTimer = null
let interviewMentionDebounceTimer = null
let interviewMentionRequestId = 0

function dismissError() {
  errorMessage.value = ''
  if (errorToastTimer) {
    clearTimeout(errorToastTimer)
    errorToastTimer = null
  }
}

watch(errorMessage, (message) => {
  if (errorToastTimer) clearTimeout(errorToastTimer)
  errorToastTimer = null
  if (!message) return
  errorToastTimer = setTimeout(() => {
    errorMessage.value = ''
    errorToastTimer = null
  }, 3000)
})

onBeforeUnmount(() => {
  if (errorToastTimer) clearTimeout(errorToastTimer)
  if (interviewMentionDebounceTimer) clearTimeout(interviewMentionDebounceTimer)
  stopTurnRecoveryPolling()
})

function clearInterviewMentions() {
  interviewMentionQuery.value = ''
  interviewMentionResults.value = []
  selectedInterviewReferences.value = []
}

function activeInterviewMention(text) {
  const match = text.match(/(?:^|\s)@([^\s@]{1,80})$/)
  return match ? match[1].trim() : ''
}

async function loadInterviewMentionResults(query) {
  const requestId = ++interviewMentionRequestId
  try {
    const payload = await requestJson(`/api/career/interview-library/mentions?query=${encodeURIComponent(query)}`)
    if (requestId !== interviewMentionRequestId || interviewMentionQuery.value !== query) return
    interviewMentionResults.value = payload.items ?? []
  } catch {
    if (requestId === interviewMentionRequestId) interviewMentionResults.value = []
  }
}

function scheduleInterviewMentionSearch(text) {
  const query = activeInterviewMention(text)
  interviewMentionQuery.value = query
  interviewMentionResults.value = []
  if (interviewMentionDebounceTimer) clearTimeout(interviewMentionDebounceTimer)
  interviewMentionDebounceTimer = null
  if (!query) return
  interviewMentionDebounceTimer = setTimeout(() => {
    void loadInterviewMentionResults(query)
  }, 180)
}

function selectInterviewMention(experience) {
  if (selectedInterviewReferences.value.some((item) => item.id === experience.id)) {
    interviewMentionResults.value = []
    return
  }
  if (selectedInterviewReferences.value.length >= 5) {
    errorMessage.value = '单轮最多引用 5 份面经资料。'
    return
  }
  selectedInterviewReferences.value = [...selectedInterviewReferences.value, experience]
  messageText.value = messageText.value.replace(/(?:^|\s)@[^\s@]{1,80}$/, ' ').trimEnd()
  messageText.value = `${messageText.value}${messageText.value ? ' ' : ''}@${experience.company_name}·${experience.role_name} `
  interviewMentionQuery.value = ''
  interviewMentionResults.value = []
}

function removeInterviewMention(experienceId) {
  selectedInterviewReferences.value = selectedInterviewReferences.value.filter((item) => item.id !== experienceId)
}

watch(messageText, (text) => {
  scheduleInterviewMentionSearch(text)
})

const providerOptions = [
  { key: 'deepseek', label: 'DeepSeek', short: 'DS', detail: '中文技术与推理模型', websiteUrl: 'https://platform.deepseek.com', apiBaseUrl: 'https://api.deepseek.com', defaultModelId: 'deepseek-v4-pro', modelHint: '例如：deepseek-v4-pro', vision: false },
  { key: 'groq', label: 'Groq', short: 'G', detail: '低延迟开源模型推理服务', websiteUrl: 'https://console.groq.com', apiBaseUrl: 'https://api.groq.com/openai/v1', defaultModelId: 'openai/gpt-oss-20b', modelHint: '例如：openai/gpt-oss-20b', vision: false },
  { key: 'openrouter', label: 'OpenRouter', short: 'OR', detail: '可路由到当前免费模型', websiteUrl: 'https://openrouter.ai', apiBaseUrl: 'https://openrouter.ai/api/v1', defaultModelId: 'openrouter/free', modelHint: '例如：openrouter/free', vision: false },
  { key: 'gemini', label: 'Google Gemini', short: 'GM', detail: '免费层支持文字与图片理解', websiteUrl: 'https://aistudio.google.com', apiBaseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai', defaultModelId: 'gemini-3.5-flash-lite', modelHint: '例如：gemini-3.5-flash-lite', vision: true },
  { key: 'qwen', label: '阿里云百炼 Qwen', short: 'QW', detail: 'DashScope OpenAI-compatible 接口', websiteUrl: 'https://bailian.console.aliyun.com', apiBaseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1', modelHint: '例如：qwen-plus', vision: true },
  { key: 'siliconflow', label: 'SiliconFlow', short: 'SF', detail: '开源模型聚合服务（部分模型免费）', websiteUrl: 'https://cloud.siliconflow.cn', apiBaseUrl: 'https://api.siliconflow.cn/v1', defaultModelId: 'Qwen/Qwen2.5-7B-Instruct', modelHint: '例如：Qwen/Qwen2.5-7B-Instruct', vision: true },
  { key: 'modelscope', label: 'ModelScope', short: 'MS', detail: '魔搭社区模型服务', websiteUrl: 'https://www.modelscope.cn', apiBaseUrl: 'https://api-inference.modelscope.cn/v1', modelHint: '例如：Qwen/Qwen2.5-7B-Instruct', vision: true },
  { key: 'nvidia', label: 'NVIDIA NIM', short: 'NV', detail: 'NVIDIA 推理服务', websiteUrl: 'https://build.nvidia.com', apiBaseUrl: 'https://integrate.api.nvidia.com/v1', modelHint: '例如：meta/llama-3.3-70b-instruct', vision: true },
  { key: 'tokenhub', label: '腾讯云 TokenHub', short: 'TH', detail: '多模型统一网关', websiteUrl: 'https://console.cloud.tencent.com/tokenhub/', apiBaseUrl: 'https://tokenhub.tencentmaas.com/v1', defaultModelId: 'deepseek-v4-flash', modelHint: '例如：deepseek-v4-flash', vision: true },
  { key: 'baidu-qianfan', label: '百度千帆', short: 'BQ', detail: 'ERNIE 与开源模型 OpenAI-compatible 接口', websiteUrl: 'https://console.bce.baidu.com/qianfan', apiBaseUrl: 'https://qianfan.baidubce.com/v2', defaultModelId: 'ernie-3.5-8k', modelHint: '例如：ernie-3.5-8k', vision: true },
  { key: 'hunyuan', label: '腾讯混元', short: 'HY', detail: '混元 OpenAI-compatible 接口', websiteUrl: 'https://console.cloud.tencent.com/hunyuan', apiBaseUrl: 'https://api.hunyuan.cloud.tencent.com/v1', defaultModelId: 'hunyuan-lite', modelHint: '例如：hunyuan-lite', vision: true },
  { key: 'zhipu', label: '智谱 AI', short: 'ZP', detail: 'GLM 系列 OpenAI-compatible 接口', websiteUrl: 'https://open.bigmodel.cn', apiBaseUrl: 'https://open.bigmodel.cn/api/paas/v4', modelHint: '例如：glm-4-flash', vision: true },
  { key: 'minimax', label: 'MiniMax', short: 'MM', detail: 'MiniMax 通用模型服务', websiteUrl: 'https://platform.minimaxi.com', apiBaseUrl: 'https://api.minimaxi.com/v1', modelHint: '例如：MiniMax-Text-01', vision: false },
  { key: 'volcengine', label: '火山方舟', short: 'ARK', detail: '方舟兼容 Chat Completions 接口', websiteUrl: 'https://console.volcengine.com/ark', apiBaseUrl: 'https://ark.cn-beijing.volces.com/api/v3', modelHint: '填写已创建推理接入点 ID', vision: true },
  { key: 'custom', label: '自定义兼容服务商', short: '+', detail: '手动填写 OpenAI-compatible 地址', websiteUrl: '', apiBaseUrl: '', modelHint: '例如：provider/model-name', vision: false }
]

const selectedProfile = computed(() =>
  modelProfiles.value.find((item) => item.profile.id === selectedProfileId.value) ?? null
)
const modelLabel = computed(() =>
  selectionMode.value === 'free_quota_first'
    ? '免费模型自动选择'
    : selectedProfile.value?.profile.display_name ?? '请选择模型'
)
const configuredFreeModels = computed(() =>
  freeModelCatalog.value.flatMap((offer) =>
    (offer.configured_profiles ?? []).map((profile) => ({
      ...profile,
      providerName: offer.display_name
    }))
  )
)
const configuredFreeProfileIds = computed(() =>
  new Set(configuredFreeModels.value.map((item) => item.id))
)
const pendingFreeModels = computed(() =>
  freeModelCatalog.value.flatMap((offer) =>
    offer.platform_ready
      ? []
      : (offer.models ?? []).map((model) => ({
          ...model,
          providerName: offer.display_name,
          providerKey: offer.provider_key
        }))
  )
)
const otherModelProfiles = computed(() =>
  modelProfiles.value.filter((item) => !configuredFreeProfileIds.value.has(item.profile.id))
)

function emptyModelForm() {
  return {
    profileKey: '', displayName: '', providerKey: 'deepseek', modelId: '', apiKey: '',
    apiBaseUrl: 'https://api.deepseek.com', websiteUrl: 'https://platform.deepseek.com',
    costTier: 'free_quota', priority: 100, text: true, vision: false
  }
}

const activeProviderOption = computed(() =>
  providerOptions.find((item) => item.key === modelForm.value.providerKey) ?? providerOptions[0]
)
const usesPresetEndpoint = computed(() => activeProviderOption.value.key !== 'custom')

function openModelDialog() {
  modelDialogMode.value = 'list'
  showModelDialog.value = true
}

function closeModelDialog() {
  showModelDialog.value = false
  connectionTestMessage.value = ''
  connectionTestError.value = ''
  connectionSaveError.value = ''
  testedConnectionFingerprint.value = ''
}

function createModelConnection() {
  modelForm.value = emptyModelForm()
  connectionTestMessage.value = ''
  connectionTestError.value = ''
  connectionSaveError.value = ''
  testedConnectionFingerprint.value = ''
  modelDialogMode.value = 'setup'
}

function chooseProvider(provider) {
  modelForm.value = {
    ...emptyModelForm(),
    providerKey: provider.key,
    profileKey: `${provider.key}-${Date.now()}`,
    displayName: `${provider.label} 模型连接`,
    modelId: provider.defaultModelId ?? '',
    apiBaseUrl: provider.apiBaseUrl,
    websiteUrl: provider.websiteUrl,
    vision: provider.vision
  }
  testedConnectionFingerprint.value = ''
  connectionTestMessage.value = ''
  connectionTestError.value = ''
  connectionSaveError.value = ''
  nextTick(() => connectionConfigRef.value?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
}

function editModelConnection(item) {
  const profile = item.profile
  modelForm.value = {
    profileKey: profile.profile_key,
    displayName: profile.display_name,
    providerKey: profile.provider_key,
    modelId: profile.model_id,
    apiKey: '',
    apiBaseUrl: providerApiBaseUrl(profile.provider_key, profile.api_base_url),
    websiteUrl: providerWebsiteUrl(profile.provider_key, profile.provider_website_url),
    costTier: profile.cost_tier,
    priority: profile.priority,
    text: profile.capabilities.includes('text'),
    vision: profile.capabilities.includes('vision')
  }
  testedConnectionFingerprint.value = ''
  connectionTestMessage.value = '为了保护密钥，修改连接后请重新填写 API Key 并测试。'
  connectionTestError.value = ''
  connectionSaveError.value = ''
  modelDialogMode.value = 'setup'
}

function openResumePicker() {
  resumeInput.value?.click()
}

function handleResumeFile(event) {
  const selectedFile = event.target.files?.[0] ?? null
  if (!selectedFile) return
  const supportedTypes = [
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'image/jpeg',
    'image/png',
    'image/webp',
    'image/bmp',
    'image/x-ms-bmp',
    'image/tiff'
  ]
  if (!supportedTypes.includes(selectedFile.type) || selectedFile.size > 10 * 1024 * 1024) {
    errorMessage.value = '仅支持 10MB 以内的 PDF、Word、Excel、JPG、PNG、WebP、BMP 或 TIFF 文件。'
    event.target.value = ''
    return
  }
  resumeFile.value = selectedFile
  feedback.value = `已选择 ${selectedFile.name}，文件只会临时解析，任务结束后自动删除。`
}

function clearResumeFile() {
  resumeFile.value = null
  if (resumeInput.value) resumeInput.value.value = ''
}

function resetConversationDraft() {
  // 清理只能服务当前浏览器会话的临时输入，避免跨会话串材料。

  messageText.value = ''
  jobUrl.value = ''
  showJobUrl.value = false
  clearResumeFile()
  clearInterviewMentions()
  streamedAssistantText.value = ''
  streamStatus.value = ''
  streamProgress.value = []
  activeTemporaryMessageId.value = ''
  lastTurn.value = null
}

function isActiveTurn(turn) {
  return turn?.status === 'queued' || turn?.status === 'running'
}

function turnStatusText(turn) {
  if (!turn) return ''
  return {
    queued: '任务已排队，正在等待处理',
    running: '任务仍在后台处理中，页面可安全刷新',
    succeeded: '本轮任务已完成',
    failed: '本轮任务未完成，请查看对话中的说明',
    cancelled: '本轮任务已取消'
  }[turn.status] ?? `任务状态：${turn.status}`
}

function stopTurnRecoveryPolling() {
  if (!turnRecoveryPollTimer) return
  clearTimeout(turnRecoveryPollTimer)
  turnRecoveryPollTimer = null
}

function scheduleTurnRecoveryPolling() {
  stopTurnRecoveryPolling()
  if (sending.value || !selectedConversation.value || !isActiveTurn(lastTurn.value)) return
  turnRecoveryPollTimer = setTimeout(() => {
    void refreshActiveConversationTurn()
  }, 3000)
}

async function refreshActiveConversationTurn() {
  const conversationId = selectedConversation.value?.id
  if (!conversationId || sending.value) return
  try {
    const payload = await requestJson(`/api/career/conversations/${conversationId}`)
    if (selectedConversation.value?.id !== conversationId) return
    messages.value = payload.messages ?? []
    restoreConversationModelSelection(payload.last_model_selection)
    lastTurn.value = payload.latest_turn ?? null
  } catch (error) {
    // 后台任务仍在运行时，短暂网络波动不应以弹窗打断用户；下一次轮询会继续尝试。
    if (!isActiveTurn(lastTurn.value)) return
  } finally {
    if (selectedConversation.value?.id === conversationId) scheduleTurnRecoveryPolling()
  }
}

function useFreeQuotaFirstSelection() {
  selectionMode.value = 'free_quota_first'
  selectedProfileId.value = ''
}

function restoreConversationModelSelection(selection) {
  const profileId = selection?.profile_id ?? ''
  const profileExists = modelProfiles.value.some((item) => item.profile.id === profileId)
  if (selection?.mode === 'specific_profile' && profileId && profileExists) {
    selectionMode.value = 'specific_profile'
    selectedProfileId.value = profileId
    return
  }
  useFreeQuotaFirstSelection()
}

function formatDate(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date)
}

function readinessText(value) {
  return {
    ready: '可调用', credential_required: '待配置额度 Key',
    policy_blocked: '策略已拦截', disabled: '已停用'
  }[value] ?? value
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options)
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload.detail ?? `请求失败：${response.status}`)
  return payload
}

async function requestSse(url, options, handlers) {
  const response = await fetch(url, options)
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    throw new Error(payload.detail ?? `请求失败：${response.status}`)
  }
  if (!response.body) throw new Error('浏览器未获得模型流式响应。')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  const consumeFrame = (frame) => {
    const lines = frame.split(/\r?\n/)
    const eventName = lines.find((line) => line.startsWith('event:'))?.slice(6).trim() ?? 'message'
    const data = lines.filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trim()).join('\n')
    if (!data) return
    let payload
    try {
      payload = JSON.parse(data)
    } catch {
      throw new Error('模型流式响应格式异常。')
    }
    handlers[eventName]?.(payload)
  }

  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done })
    let boundary = buffer.indexOf('\n\n')
    while (boundary >= 0) {
      consumeFrame(buffer.slice(0, boundary))
      buffer = buffer.slice(boundary + 2)
      boundary = buffer.indexOf('\n\n')
    }
    if (done) break
  }
}

async function refreshData() {
  loading.value = true
  errorMessage.value = ''
  try {
    const [history, profiles, catalog] = await Promise.all([
      requestJson('/api/career/conversations'),
      requestJson('/api/career/model-profiles'),
      requestJson('/api/career/free-model-catalog')
    ])
    conversations.value = history.items ?? []
    modelProfiles.value = profiles.items ?? []
    freeModelCatalog.value = catalog.items ?? []
    if (selectedConversation.value) {
      const current = conversations.value.find((item) => item.id === selectedConversation.value.id)
      if (current) await selectConversation(current.id, false)
      else {
        selectedConversation.value = null
        messages.value = []
      }
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '求职助手数据加载失败'
  } finally {
    loading.value = false
  }
}

async function selectConversation(conversationId, clearFeedback = true) {
  const requestId = ++conversationSelectionRequestId
  loading.value = true
  errorMessage.value = ''
  if (clearFeedback) feedback.value = ''
  stopTurnRecoveryPolling()
  resetConversationDraft()
  useFreeQuotaFirstSelection()
  try {
    const payload = await requestJson(`/api/career/conversations/${conversationId}`)
    if (requestId !== conversationSelectionRequestId) return
    selectedConversation.value = payload.conversation
    messages.value = payload.messages ?? []
    restoreConversationModelSelection(payload.last_model_selection)
    lastTurn.value = payload.latest_turn ?? null
    scheduleTurnRecoveryPolling()
  } catch (error) {
    if (requestId !== conversationSelectionRequestId) return
    errorMessage.value = error instanceof Error ? error.message : '会话读取失败'
  } finally {
    if (requestId === conversationSelectionRequestId) loading.value = false
  }
}

function conversationTitleFromText(text) {
  const title = text.trim().replace(/\s+/g, ' ')
  return title ? `${title.slice(0, 24)}${title.length > 24 ? '…' : ''}` : '新的求职咨询'
}

function conversationTitle() {
  return conversationTitleFromText(messageText.value)
}

async function createConversation({ preserveDraft = false, title = conversationTitle() } = {}) {
  creating.value = true
  errorMessage.value = ''
  try {
    const conversation = await requestJson('/api/career/conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title })
    })
    conversations.value = [conversation, ...conversations.value]
    selectedConversation.value = conversation
    messages.value = []
    lastTurn.value = null
    if (!preserveDraft) {
      resetConversationDraft()
      useFreeQuotaFirstSelection()
    }
    return conversation
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '新建会话失败'
    return null
  } finally {
    creating.value = false
  }
}

function createMaterialsRequest(input) {
  const formData = new FormData()
  formData.append('text', input.text)
  formData.append('job_url', input.jobUrl || '')
  formData.append('selection_mode', input.selectionMode)
  if (input.selectionMode === 'specific_profile' && input.selectedProfileId) {
    formData.append('model_profile_id', input.selectedProfileId)
  }
  for (const experienceId of input.interviewExperienceIds) {
    formData.append('interview_experience_ids', experienceId)
  }
  formData.append('resume_file', input.resumeFile)
  return { method: 'POST', body: formData }
}

function createTemporaryMessage(input) {
  temporaryMessageSequence += 1
  const id = `local-${Date.now()}-${temporaryMessageSequence}`
  return {
    id,
    role: 'user',
    content: input.text || input.jobUrl || `已提交附件：${input.resumeFile?.name ?? '简历材料'}`,
    created_at: new Date().toISOString(),
    local_state: 'sending'
  }
}

function replaceOrAppendMessage(message, temporaryMessageId = '') {
  if (!message?.id) return
  const existingIndex = messages.value.findIndex((item) => item.id === message.id)
  const temporaryIndex = temporaryMessageId
    ? messages.value.findIndex((item) => item.id === temporaryMessageId)
    : -1
  const replacementIndex = existingIndex >= 0 ? existingIndex : temporaryIndex
  if (replacementIndex >= 0) {
    messages.value.splice(replacementIndex, 1, message)
  } else {
    messages.value.push(message)
  }
}

function markTemporaryMessageFailed(temporaryMessageId) {
  const index = messages.value.findIndex((item) => item.id === temporaryMessageId)
  if (index < 0) return
  messages.value.splice(index, 1, {
    ...messages.value[index],
    local_state: 'failed'
  })
}

function updateStreamProgress(event) {
  const key = event?.key
  if (!key) return
  const index = streamProgress.value.findIndex((item) => item.key === key)
  const progress = {
    key,
    label: event.label || '正在处理…',
    state: event.state || 'running'
  }
  if (index >= 0) streamProgress.value.splice(index, 1, progress)
  else streamProgress.value.push(progress)
}

function finalizeStreamProgress() {
  streamProgress.value = streamProgress.value.map((item) => (
    item.state === 'running' ? { ...item, state: 'completed' } : item
  ))
}

function clearComposerAfterSubmit() {
  messageText.value = ''
  jobUrl.value = ''
  showJobUrl.value = false
  clearResumeFile()
  clearInterviewMentions()
}

function restoreComposerAfterPreflightFailure(input) {
  // 仅在请求尚未开始前恢复草稿。真正已经提交到服务端的请求失败时，
  // 保留失败消息，避免用户不知情地重复提交同一份材料。
  messageText.value = input.text
  jobUrl.value = input.jobUrl
  showJobUrl.value = Boolean(input.jobUrl)
  resumeFile.value = input.resumeFile
  selectedInterviewReferences.value = input.interviewReferences
}

async function sendMessage() {
  if (!messageText.value.trim() && !jobUrl.value.trim() && !resumeFile.value) {
    errorMessage.value = '请输入咨询内容或粘贴职位链接。'
    return
  }
  sending.value = true
  errorMessage.value = ''
  feedback.value = ''
  const input = {
    text: messageText.value.trim(),
    jobUrl: jobUrl.value.trim(),
    resumeFile: resumeFile.value,
    selectionMode: selectionMode.value,
    selectedProfileId: selectedProfileId.value,
    interviewExperienceIds: selectedInterviewReferences.value.map((item) => item.id),
    interviewReferences: [...selectedInterviewReferences.value]
  }
  const temporaryMessage = createTemporaryMessage(input)
  activeTemporaryMessageId.value = temporaryMessage.id
  clearComposerAfterSubmit()
  let temporaryMessageMounted = false
  try {
    const conversation = selectedConversation.value ?? await createConversation({
      preserveDraft: true,
      title: conversationTitleFromText(input.text)
    })
    if (!conversation) {
      restoreComposerAfterPreflightFailure(input)
      return
    }
    messages.value.push(temporaryMessage)
    temporaryMessageMounted = true
    const hasMaterial = Boolean(input.resumeFile)
    const endpoint = hasMaterial
      ? `/api/career/conversations/${conversation.id}/intake-with-materials-stream`
      : `/api/career/conversations/${conversation.id}/intake-stream`
    const requestOptions = hasMaterial
      ? createMaterialsRequest(input)
      : {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            text: input.text,
            job_url: input.jobUrl || null,
            selection_mode: input.selectionMode,
            model_profile_id: input.selectionMode === 'specific_profile' ? input.selectedProfileId || null : null,
            interview_experience_ids: input.interviewExperienceIds
          })
        }
    let payload = null
    let streamFailure = ''
    streamStatus.value = '正在思考…'
    streamedAssistantText.value = ''
    streamProgress.value = [{
      key: 'intake_started',
      label: '已收到提问，正在建立本轮任务',
      state: 'running'
    }]
    await requestSse(endpoint, requestOptions, {
      status: (event) => { streamStatus.value = event.message ?? '正在处理…' },
      progress: (event) => {
        updateStreamProgress(event)
        streamStatus.value = event.label ?? streamStatus.value
      },
      accepted: (event) => {
        replaceOrAppendMessage(event.message, activeTemporaryMessageId.value)
        activeTemporaryMessageId.value = ''
        if (event.turn) lastTurn.value = event.turn
      },
      delta: (event) => {
        streamStatus.value = '正在生成回复…'
        streamedAssistantText.value += (event.content ?? '')
      },
      done: (event) => { payload = event },
      error: (event) => {
        if (event.assistant_message) payload = event
        else streamFailure = event.detail ?? '模型流式响应未完成。'
      }
    })
    if (!payload) throw new Error(streamFailure || '模型流式响应未返回最终结果。')
    replaceOrAppendMessage(payload.message, activeTemporaryMessageId.value)
    activeTemporaryMessageId.value = ''
    replaceOrAppendMessage(payload.assistant_message)
    finalizeStreamProgress()
    lastTurn.value = payload.turn
    if (hasMaterial) {
      const processing = payload.attachment_processing ?? {}
      const extractedCharacters = Number(processing.text_characters ?? 0)
      const notices = Array.isArray(processing.notices) ? processing.notices : []
      const processingItems = Array.isArray(processing.items) ? processing.items : []
      const doclingItem = processingItems.find((item) => item?.parser_name === 'docling-serve')
      const cloudVisionItem = processingItems.find((item) => item?.processing_route === 'cloud_vision')
      const parserDescription = cloudVisionItem
        ? '云端图片理解已自动完成'
        : doclingItem
        ? `Docling OCR 已${doclingItem.parser_status === 'success' ? '完成' : '部分完成'}`
        : ''
      feedback.value = notices.length > 0
        ? `已收到附件；${notices[0]} 原文件已自动清理。`
        : extractedCharacters > 0
        ? `已收到附件${parserDescription ? `，${parserDescription}` : ''}，并提取 ${extractedCharacters} 个字符供本轮模型分析；原文件已自动清理。`
        : '已收到附件，但未提取到可复制文本；若是扫描版 PDF，请上传图片简历或可复制文本的 PDF。'
    } else {
      feedback.value = payload.turn?.status === 'succeeded'
        ? '已完成本轮模型回复。'
        : '本轮内容已保存，但模型调用未完成；具体原因已显示在对话内。'
    }
    const jobSource = payload.job_source ?? {}
    if (jobSource.status === 'unavailable' || jobSource.status === 'not_configured') {
      const jobSourceMessage = jobSource.message || '职位链接暂时无法读取，请直接粘贴职位描述。'
      feedback.value = `${feedback.value} ${jobSourceMessage}`.trim()
    }
    const conversationIndex = conversations.value.findIndex((item) => item.id === conversation.id)
    if (conversationIndex >= 0) {
      const updatedConversation = {
        ...conversations.value[conversationIndex],
        updated_at: payload.assistant_message?.created_at ?? payload.message?.created_at ?? new Date().toISOString()
      }
      conversations.value = [
        updatedConversation,
        ...conversations.value.filter((item) => item.id !== conversation.id)
      ]
      selectedConversation.value = updatedConversation
    }
  } catch (error) {
    if (temporaryMessageMounted) {
      markTemporaryMessageFailed(activeTemporaryMessageId.value)
    } else {
      restoreComposerAfterPreflightFailure(input)
    }
    errorMessage.value = error instanceof Error ? error.message : '消息提交失败'
  } finally {
    activeTemporaryMessageId.value = ''
    streamedAssistantText.value = ''
    streamStatus.value = ''
    streamProgress.value = []
    sending.value = false
    scheduleTurnRecoveryPolling()
  }
}

async function archiveConversation() {
  if (!selectedConversation.value) return
  try {
    await requestJson(`/api/career/conversations/${selectedConversation.value.id}/archive`, { method: 'POST' })
    conversations.value = conversations.value.filter((item) => item.id !== selectedConversation.value.id)
    selectedConversation.value = null
    messages.value = []
    resetConversationDraft()
    useFreeQuotaFirstSelection()
    feedback.value = '会话已归档，历史会被保留但不能继续写入。'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '会话归档失败'
  }
}

function chooseModel(event) {
  const value = event.target.value
  selectionMode.value = value === 'free_quota_first' ? 'free_quota_first' : 'specific_profile'
  selectedProfileId.value = value === 'free_quota_first' ? '' : value
}

async function saveModelProfile() {
  const form = modelForm.value
  const validationMessage = connectionValidationMessage()
  if (validationMessage) {
    connectionSaveError.value = validationMessage
    return
  }
  if (testedConnectionFingerprint.value !== connectionFingerprint()) {
    connectionTestError.value = '请先对当前配置执行“测试连接”，确认可用后再保存。'
    return
  }
  savingModel.value = true
  connectionSaveError.value = ''
  try {
    const payload = await requestJson(`/api/career/model-connections/${encodeURIComponent(resolvedProfileKey())}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(modelConnectionPayload())
    })
    const index = modelProfiles.value.findIndex((item) => item.profile.id === payload.profile.id)
    if (index >= 0) modelProfiles.value.splice(index, 1, payload)
    else modelProfiles.value = [...modelProfiles.value, payload]
    selectionMode.value = 'specific_profile'
    selectedProfileId.value = payload.profile.id
    modelForm.value = emptyModelForm()
    modelDialogMode.value = 'list'
    connectionTestMessage.value = ''
    testedConnectionFingerprint.value = ''
    feedback.value = '模型连接已通过测试并保存。API Key 已保存到本机 PostgreSQL 模型连接表。'
  } catch (error) {
    connectionSaveError.value = connectionErrorText(error, '保存失败，请检查配置后重试。')
  } finally {
    savingModel.value = false
  }
}

function modelConnectionPayload() {
  const form = modelForm.value
  const capabilities = []
  if (form.text) capabilities.push('text')
  if (form.vision) capabilities.push('vision')
  const apiBaseUrl = providerApiBaseUrl(form.providerKey, form.apiBaseUrl)
  const websiteUrl = providerWebsiteUrl(form.providerKey, form.websiteUrl)
  return {
    display_name: resolvedDisplayName(),
    provider_key: form.providerKey.trim(),
    model_id: form.modelId.trim(),
    api_key: form.apiKey.trim(),
    capabilities,
    cost_tier: form.costTier,
    priority: Number(form.priority),
    enabled: true,
    api_base_url: apiBaseUrl || null,
    provider_website_url: websiteUrl || null
  }
}

function resolvedDisplayName() {
  const configuredName = modelForm.value.displayName.trim()
  if (configuredName) return configuredName
  return `${activeProviderOption.value.label} 模型连接`
}

function resolvedProfileKey() {
  const configuredKey = modelForm.value.profileKey.trim()
  if (configuredKey) return configuredKey
  const providerKey = activeProviderOption.value.key || 'model'
  const modelKey = modelForm.value.modelId
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48)
  return `${providerKey}-${modelKey || 'connection'}`.slice(0, 64)
}

function providerApiBaseUrl(providerKey, configuredUrl) {
  const provider = providerOptions.find((item) => item.key === providerKey)
  if (provider && provider.key !== 'custom') return provider.apiBaseUrl
  return String(configuredUrl ?? '').trim()
}

function providerWebsiteUrl(providerKey, configuredUrl) {
  const provider = providerOptions.find((item) => item.key === providerKey)
  if (provider && provider.key !== 'custom') return provider.websiteUrl
  return String(configuredUrl ?? '').trim()
}

function connectionFingerprint() {
  const payload = modelConnectionPayload()
  return JSON.stringify(payload)
}

function invalidateConnectionTest() {
  testedConnectionFingerprint.value = ''
  connectionTestMessage.value = ''
  connectionTestError.value = ''
  connectionSaveError.value = ''
}

function connectionErrorText(error, fallback) {
  const message = error instanceof Error ? error.message.trim() : ''
  if (!message) return fallback
  if (/failed to fetch|networkerror|load failed/i.test(message)) {
    return '无法连接到本地求职助手服务。请确认后台服务正在运行，然后重试。'
  }
  return message.replace(/^模型连接测试未通过：\s*/, '')
}

function apiBaseUrlValidationMessage() {
  const apiBaseUrl = providerApiBaseUrl(modelForm.value.providerKey, modelForm.value.apiBaseUrl)
  try {
    const url = new URL(apiBaseUrl)
    if (url.protocol !== 'https:') {
      return 'API Base URL 必须以 https:// 开头。'
    }
  } catch {
    return 'API Base URL 格式不正确，请填写完整的 HTTPS 地址。'
  }
  return ''
}

function connectionValidationMessage() {
  const form = modelForm.value
  if (!form.modelId.trim()) return '请填写模型名称（Model ID）。'
  if (!form.apiKey.trim()) return '请粘贴该服务商的 API Key。'
  if (!form.text && !form.vision) return '请至少选择一种模型能力。'
  if (!usesPresetEndpoint.value && !form.apiBaseUrl.trim()) {
    return '自定义兼容服务商需要填写请求地址（API Base URL）。'
  }
  return apiBaseUrlValidationMessage()
}

async function testModelConnection() {
  const validationMessage = connectionValidationMessage()
  if (validationMessage) {
    connectionTestError.value = validationMessage
    return
  }
  testingConnection.value = true
  connectionTestMessage.value = ''
  connectionTestError.value = ''
  try {
    const result = await requestJson('/api/career/model-connections/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(modelConnectionPayload())
    })
    testedConnectionFingerprint.value = connectionFingerprint()
    const responsePreview = typeof result.response_preview === 'string' ? result.response_preview.trim() : ''
    connectionTestMessage.value = responsePreview
      ? `连接测试通过：模型已回复“${responsePreview}”。`
      : '连接测试通过：地址、模型 ID 与 API Key 均可用。'
    connectionSaveError.value = ''
  } catch (error) {
    testedConnectionFingerprint.value = ''
    connectionTestError.value = connectionErrorText(error, '模型连接测试失败，请检查配置后重试。')
  } finally {
    testingConnection.value = false
  }
}

onMounted(refreshData)
</script>

<template>
  <section class="career-workspace" aria-label="求职助手">
    <aside class="career-history-panel">
      <button class="primary-button" type="button" :disabled="creating || sending" @click="createConversation">
        ＋ {{ creating ? '正在创建...' : '开启新对话' }}
      </button>
      <div class="history-title"><span>会话历史</span><small>{{ conversations.length }}</small></div>
      <p v-if="loading && !conversations.length" class="list-empty">正在加载会话...</p>
      <p v-else-if="!conversations.length" class="list-empty">还没有求职会话</p>
      <div v-else class="conversation-list">
        <button
          v-for="conversation in conversations"
          :key="conversation.id"
          type="button"
          class="conversation-item"
          :disabled="sending"
          :class="{ active: selectedConversation?.id === conversation.id }"
          @click="selectConversation(conversation.id)"
        >
          <strong>{{ conversation.title }}</strong>
          <small>{{ formatDate(conversation.updated_at) }}</small>
        </button>
      </div>
      <div class="privacy-card"><strong>隐私边界</strong><p>简历原文件不入库；历史保存对话文本，是否脱敏由个人部署配置决定。</p></div>
    </aside>

    <section class="career-chat-panel">
      <header class="chat-header">
        <div><p class="eyebrow">CAREER AGENT</p><h2>{{ selectedConversation?.title || '求职助手' }}</h2></div>
      </header>

      <section v-if="feedback" class="notice success">{{ feedback }}</section>

      <section v-if="!selectedConversation" class="empty-state">
        <div class="empty-mark">✦</div><p class="eyebrow">CAREER MATCHING</p><h2>请上传简历和职位信息</h2>
        <p>可粘贴职位链接，或先描述目标岗位与求职困惑。系统会先建立安全的对话上下文。</p>
        <div class="tag-row"><span>简历优缺点</span><span>岗位匹配度</span><span>面试准备</span></div>
      </section>

      <section v-else class="message-list" aria-live="polite">
        <article v-if="!messages.length" class="agent-message"><strong>求职助手</strong><p>你好，我可以先回答求职、职业发展和面试准备问题；需要简历诊断或岗位匹配时，再上传材料即可。</p></article>
        <article v-for="message in messages" :key="message.id" class="message" :class="[message.role === 'user' ? 'from-user' : 'from-agent', message.local_state ? `message-${message.local_state}` : '']"><span>{{ message.role === 'user' ? '你' : '求职助手' }}</span><p>{{ message.content }}</p><small>{{ formatDate(message.created_at) }}<template v-if="message.local_state === 'sending'"> · 正在发送</template><template v-else-if="message.local_state === 'failed'"> · 发送失败</template></small></article>
        <article v-if="sending" class="agent-message agent-pending">
          <div class="stream-pending-heading"><strong>求职助手</strong><span>正在思考</span></div>
          <ol v-if="streamProgress.length" class="stream-progress-list" aria-label="处理进展">
            <li v-for="progress in streamProgress" :key="progress.key" class="stream-progress-item" :class="progress.state"><i></i><span>{{ progress.label }}</span></li>
          </ol>
          <p v-if="streamedAssistantText" class="streamed-answer">{{ streamedAssistantText }}</p>
          <p v-else class="stream-status">{{ streamStatus || '正在思考…' }}</p>
        </article>
        <div v-if="lastTurn" class="turn-status" :class="{ active: isActiveTurn(lastTurn) }">{{ turnStatusText(lastTurn) }}</div>
      </section>

      <footer class="composer">
        <div class="composer-toolbar" aria-label="会话与材料工具">
          <div class="input-tools">
            <button class="chip-button" :class="{ active: showJobUrl }" type="button" @click="showJobUrl = !showJobUrl">职位链接</button>
            <input ref="resumeInput" class="resume-input" type="file" accept="application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,image/jpeg,image/png,image/webp,image/bmp,image/x-ms-bmp,image/tiff" @change="handleResumeFile" />
            <button class="chip-button" :class="{ active: resumeFile }" type="button" @click="openResumePicker">{{ resumeFile ? `材料：${resumeFile.name}` : '上传材料' }}</button>
            <button v-if="resumeFile" class="chip-button file-clear-button" type="button" @click="clearResumeFile">移除</button>
            <select class="model-select" :value="selectionMode === 'free_quota_first' ? 'free_quota_first' : selectedProfileId" aria-label="本轮模型选择" @change="chooseModel">
              <option value="free_quota_first">【免费】自动选择可用模型</option>
              <optgroup v-if="configuredFreeModels.length" label="已接入的免费模型">
                <option v-for="model in configuredFreeModels" :key="model.id" :value="model.id">【免费】{{ model.providerName }} · {{ model.display_name }}</option>
              </optgroup>
              <optgroup v-if="pendingFreeModels.length" label="免费模型（待管理员接入）">
                <option v-for="model in pendingFreeModels" :key="`${model.providerKey}-${model.model_id}`" disabled>【免费·待接入】{{ model.providerName }} · {{ model.display_name }}</option>
              </optgroup>
              <optgroup v-if="otherModelProfiles.length" label="已配置的其他模型">
                <option v-for="item in otherModelProfiles" :key="item.profile.id" :value="item.profile.id">{{ item.profile.display_name }} · {{ readinessText(item.readiness) }}</option>
              </optgroup>
            </select>
          </div>
          <div class="session-tools">
            <button class="quiet-button model-manager-button" type="button" @click="openModelDialog">模型与连接</button>
            <button v-if="selectedConversation" class="quiet-button danger" type="button" @click="archiveConversation">归档会话</button>
          </div>
        </div>
        <div v-if="showJobUrl" class="job-url-row"><label for="job-url">职位链接</label><input id="job-url" v-model="jobUrl" placeholder="粘贴 BOSS、脉脉等公开职位链接" /></div>
        <textarea v-model="messageText" aria-label="求职咨询内容" placeholder="描述目标岗位、经验背景，或粘贴职位要求..." rows="3" @keydown.enter.exact.prevent="sendMessage" />
        <div v-if="selectedInterviewReferences.length" class="interview-reference-row" aria-label="已引用面经">
          <span v-for="experience in selectedInterviewReferences" :key="experience.id" class="interview-reference-chip">
            <span>@{{ experience.company_name }} · {{ experience.role_name }}</span>
            <button type="button" :aria-label="`移除 ${experience.job_name}`" @click="removeInterviewMention(experience.id)">×</button>
          </span>
        </div>
        <div v-if="interviewMentionResults.length" class="interview-mention-menu" role="listbox" aria-label="面经库检索结果">
          <button v-for="experience in interviewMentionResults" :key="experience.id" type="button" role="option" @mousedown.prevent="selectInterviewMention(experience)">
            <strong>{{ experience.company_name }} · {{ experience.role_name }}</strong>
            <small>{{ experience.job_name }}<template v-if="experience.interview_date"> · {{ experience.interview_date }}</template></small>
          </button>
        </div>
        <div class="composer-footer"><small>当前模型：{{ modelLabel }}。输入 @ 可检索面经库；回车发送，Shift + Enter 换行。</small><button class="send-button" type="button" :disabled="sending" @click="sendMessage">{{ sending ? '正在思考…' : '发送' }}</button></div>
      </footer>
    </section>
  </section>

  <Teleport to="body">
    <section v-if="errorMessage" class="career-error-toast" role="alert" aria-live="assertive">
      <div><strong>操作未完成</strong><p>{{ errorMessage }}</p></div>
      <button class="toast-close-button" type="button" aria-label="关闭错误提示" @click="dismissError">×</button>
    </section>
  </Teleport>

  <Teleport to="body">
    <div v-if="showModelDialog" class="model-dialog-backdrop" @mousedown.self="closeModelDialog">
      <section class="model-dialog" role="dialog" aria-modal="true" aria-label="模型与连接管理">
        <header class="model-dialog-header">
          <div>
            <h2>{{ modelDialogMode === 'list' ? '模型与连接' : '添加或编辑模型连接' }}</h2>
          </div>
          <button class="dialog-close-button" type="button" aria-label="关闭模型连接管理" @click="closeModelDialog">×</button>
        </header>

        <main v-if="modelDialogMode === 'list'" class="model-dialog-body">
          <div class="connection-toolbar">
            <div><strong>模型连接</strong><small>平台已托管的免费模型可直接供访客使用，不会向浏览器暴露 API Key。</small></div>
            <button class="dialog-primary-button" type="button" @click="createModelConnection">＋ 添加模型</button>
          </div>
          <div v-if="modelProfiles.length" class="connection-card-list">
            <button v-for="item in modelProfiles" :key="item.profile.id" class="connection-card" type="button" @click="editModelConnection(item)">
              <span class="connection-drag">⠿</span>
              <span class="provider-avatar">{{ item.profile.provider_key.slice(0, 2).toUpperCase() }}</span>
              <span class="connection-card-copy"><strong>{{ item.profile.display_name }}</strong><small>{{ item.profile.provider_key }} · {{ item.profile.model_id }}</small></span>
              <span class="connection-meta"><span :class="`readiness ${item.readiness}`">{{ readinessText(item.readiness) }}</span><small>顺序 {{ item.profile.priority }}</small></span>
            </button>
          </div>
          <div v-else class="connection-empty-state"><span>⌁</span><strong>还没有可用模型</strong><p>添加一个带免费额度的服务商连接后，即可开始求职分析。</p></div>
        </main>

        <main v-else class="model-dialog-body connection-setup-body">
          <button class="back-button" type="button" @click="modelDialogMode = 'list'">← 返回已配置模型</button>
          <section class="provider-catalog-section" aria-labelledby="provider-catalog-title">
            <div><h3 id="provider-catalog-title">1. 选择服务商</h3></div>
            <div class="provider-picker-grid provider-picker-grid-expanded">
              <button v-for="provider in providerOptions" :key="provider.key" class="provider-picker-card" :class="{ selected: activeProviderOption.key === provider.key }" type="button" @click="chooseProvider(provider)">
                <span class="provider-avatar large">{{ provider.short }}</span><span><strong>{{ provider.label }}</strong><small>{{ provider.detail }}</small></span><em v-if="provider.key !== 'custom'">预置地址</em><em v-else>手动配置</em>
              </button>
            </div>
          </section>

          <section ref="connectionConfigRef" class="connection-config-section" aria-labelledby="connection-config-title">
            <div class="connection-section-heading"><div><h3 id="connection-config-title">2. 填写连接配置</h3></div></div>
            <div class="connection-form-grid">
              <label v-if="!usesPresetEndpoint">连接名称<span>只在本平台展示，例如“自定义求职分析”</span><input v-model="modelForm.displayName" placeholder="例如：自定义求职分析" @input="invalidateConnectionTest" /></label>
              <label>模型名称（Model ID）<span>从服务商控制台复制模型或接入点标识</span><input v-model="modelForm.modelId" :placeholder="activeProviderOption.modelHint" @input="invalidateConnectionTest" /></label>
              <label v-if="!usesPresetEndpoint">官网地址<span>用于打开服务商控制台，不参与模型调用</span><input v-model="modelForm.websiteUrl" placeholder="https://platform.example.com" @input="invalidateConnectionTest" /></label>
              <label v-if="!usesPresetEndpoint">免费模型候选顺序<span>数值越小，自动选择时越靠前</span><input v-model.number="modelForm.priority" min="0" max="10000" type="number" @input="invalidateConnectionTest" /></label>
              <label class="full-width">API Key<span>显式填写；测试和保存后均不会回显。生产环境请通过 HTTPS 访问本平台。</span><input v-model="modelForm.apiKey" type="password" autocomplete="new-password" spellcheck="false" placeholder="粘贴该服务商的 API Key" @input="invalidateConnectionTest" /></label>
              <label v-if="!usesPresetEndpoint" class="full-width">请求地址（API Base URL）<span>填写到 API Base URL 层级，不要附加 /chat/completions。</span><input v-model="modelForm.apiBaseUrl" placeholder="https://your-api-endpoint/v1" @input="invalidateConnectionTest" /></label>
            </div>
            <fieldset class="capability-fieldset"><legend>模型能力</legend><label class="capability-option"><input v-model="modelForm.text" type="checkbox" @change="invalidateConnectionTest" /> <span><strong>文字与 PDF 文本</strong><small>用于职位匹配、简历建议和面试准备</small></span></label><label class="capability-option"><input v-model="modelForm.vision" type="checkbox" @change="invalidateConnectionTest" /> <span><strong>图片简历</strong><small>仅在当前模型确实支持视觉输入时勾选</small></span></label></fieldset>
            <p v-if="connectionTestMessage" class="connection-test-success">{{ connectionTestMessage }}</p>
            <p v-if="connectionTestError" class="connection-test-error" role="alert"><strong>连接测试未通过</strong>{{ connectionTestError }}</p>
            <p v-if="connectionSaveError" class="connection-test-error" role="alert"><strong>保存模型连接失败</strong>{{ connectionSaveError }}</p>
          </section>
        </main>

        <footer v-if="modelDialogMode !== 'list'" class="model-dialog-footer">
          <button class="dialog-secondary-button" type="button" @click="modelDialogMode = 'list'">取消</button>
          <button v-if="modelDialogMode === 'setup'" class="dialog-secondary-button test-connection-button" type="button" :disabled="testingConnection || savingModel" @click="testModelConnection">{{ testingConnection ? '正在测试连接…' : '测试连接' }}</button>
          <button v-if="modelDialogMode === 'setup'" class="dialog-primary-button" type="button" :disabled="savingModel || testingConnection || testedConnectionFingerprint !== connectionFingerprint()" @click="saveModelProfile">{{ savingModel ? '正在保存并复测…' : '保存模型连接' }}</button>
        </footer>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.career-workspace { display:grid; height:100%; min-height:0; flex:1; grid-template-columns:252px minmax(0,1fr); gap:14px; overflow:hidden; }
.career-history-panel,.career-chat-panel { border:1px solid #e0e6d8; border-radius:20px; background:#fff; box-shadow:none; }
.career-history-panel { display:flex; height:100%; min-height:0; flex-direction:column; gap:12px; overflow:hidden; padding:14px; }
.primary-button,.send-button { border:0; border-radius:13px; background:#89a93e; color:#fff; font-weight:800; }
.primary-button { padding:12px 14px; }.primary-button:disabled,.send-button:disabled { cursor:wait; opacity:.6; }
.history-title,.conversation-item,.chat-header,.composer-toolbar,.composer-footer,.session-tools,.model-profile-list article { display:flex; align-items:center; justify-content:space-between; gap:10px; }
.history-title { color:#7e8a6d; font-size:12px; font-weight:800; }.history-title small { display:grid; min-width:25px; height:25px; place-items:center; border-radius:999px; background:#eff5e3; color:#668325; }
.conversation-list { display:grid; flex:1; align-content:start; gap:7px; overflow-y:auto; }.list-empty { color:#95a08b; font-size:13px; text-align:center; }
.conversation-item { width:100%; border:1px solid transparent; border-radius:12px; background:#fafbf8; color:#344132; padding:11px; text-align:left; }.conversation-item strong { overflow:hidden; max-width:174px; text-overflow:ellipsis; white-space:nowrap; }.conversation-item small { color:#99a18f; font-size:11px; }.conversation-item.active { border-color:#d7e5ba; background:#f1f6e7; color:#50741a; }
.privacy-card { border:1px solid #e6ecd9; border-radius:16px; background:#fbfcf8; padding:13px; }.privacy-card strong,.eyebrow { color:#8ba257; font-size:11px; font-weight:900; letter-spacing:.1em; }.privacy-card p { margin:7px 0 0; color:#747f6c; font-size:12px; line-height:1.6; }
.career-chat-panel { display:flex; height:100%; min-height:0; flex-direction:column; overflow:hidden; }.chat-header { min-height:68px; border-bottom:1px solid #edf0e7; padding:14px 20px; }.chat-header h2,.model-settings h3,.empty-state h2 { margin:3px 0 0; color:#283427; }.eyebrow { margin:0; }
.quiet-button,.chip-button { border:1px solid #dfe8d0; border-radius:10px; background:#f8fbf1; color:#61764b; padding:8px 11px; font-size:12px; font-weight:800; }.quiet-button.danger { border-color:#f0d5d5; background:#fff8f8; color:#ad5a5a; }
.notice { margin:14px 22px 0; border-radius:12px; padding:10px 13px; font-size:13px; }.notice.success { border:1px solid #d7eabe; background:#f5faec; color:#5c7a2c; }
.model-settings { display:grid; grid-template-columns:minmax(220px,.75fr) minmax(360px,1.25fr); gap:18px; border-bottom:1px solid #edf0e7; background:#fbfcf8; padding:18px 22px; }.model-settings p:not(.eyebrow) { color:#7b8574; font-size:13px; line-height:1.6; }
.model-form { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }.model-form label { display:grid; gap:5px; color:#6e7b63; font-size:12px; font-weight:700; }.model-form input,.model-form select,.job-url-row input,.composer textarea,.model-select { width:100%; box-sizing:border-box; border:1px solid #dfe7d4; border-radius:10px; background:#fff; color:#324032; font:inherit; outline:none; }.model-form input,.model-form select { padding:8px 9px; }.check-label { display:flex !important; align-items:center; gap:7px; }.check-label input { width:auto; }.save-model { grid-column:1 / -1; padding:10px; }
.model-profile-list { grid-column:1 / -1; display:grid; gap:7px; }.model-profile-list article { border:1px solid #e8ede0; border-radius:11px; background:#fff; padding:9px 11px; }.model-profile-list strong,.model-profile-list small { display:block; }.model-profile-list strong { color:#3a4837; font-size:13px; }.model-profile-list small { margin-top:3px; color:#8a9582; font-size:11px; }.readiness { border-radius:999px; padding:5px 8px; font-size:11px; font-weight:800; white-space:nowrap; }.readiness.ready { background:#edf7df; color:#5c7d27; }.readiness.credential_required { background:#fff4d9; color:#a16d12; }.readiness.policy_blocked { background:#fff0f0; color:#a65a5a; }.readiness.disabled { background:#f0f2ee; color:#7d8779; }
.empty-state { display:grid; flex:1; place-content:center; justify-items:center; padding:50px 28px; text-align:center; }.empty-mark { display:grid; width:44px; height:44px; place-items:center; border-radius:14px; background:#89a93e; color:#fff; font-size:21px; }.empty-state > p:not(.eyebrow) { max-width:530px; color:#78836f; line-height:1.7; }.tag-row { display:flex; flex-wrap:wrap; justify-content:center; gap:8px; }.tag-row span { border-radius:999px; background:#f1f7e5; color:#668329; padding:7px 10px; font-size:12px; font-weight:800; }
.message-list { display:flex; flex:1; flex-direction:column; gap:12px; overflow-y:auto; overscroll-behavior:contain; padding:18px 20px; }.message,.agent-message { max-width:min(760px,78%); border-radius:15px; padding:12px 14px; }.agent-message,.message.from-agent { align-self:flex-start; border:1px solid #e5ebdc; background:#fbfcf9; }.message.from-user { align-self:flex-end; background:#edf5dc; }.message span,.agent-message strong { color:#687c50; font-size:11px; font-weight:900; }.message p,.agent-message p { margin:6px 0; color:#344033; line-height:1.7; white-space:pre-wrap; }.message small { color:#99a38f; font-size:10px; }.turn-status { align-self:center; border-radius:999px; background:#f7f8f3; color:#64715a; padding:7px 11px; font-size:12px; }.turn-status.active { background:#fff5df; color:#a16e1b; }
.message-sending { opacity:.76; }.message-failed { border:1px solid #edcaca; background:#fff8f8 !important; }.message-failed small { color:#b35454; font-weight:800; }.agent-pending { width:min(520px,78%); color:#607852; }.stream-pending-heading { display:flex; align-items:center; gap:8px; color:#607852; }.stream-pending-heading::before { width:13px; height:13px; flex:0 0 auto; border:2px solid #d7e5bc; border-top-color:#89a93e; border-radius:50%; content:''; animation:career-thinking-spin .8s linear infinite; }.stream-pending-heading span { color:#596d4d; font-size:12px; font-weight:850; }.stream-progress-list { display:grid; gap:7px; margin:11px 0 0; padding:0; list-style:none; }.stream-progress-item { display:flex; align-items:center; gap:8px; color:#75846d; font-size:12px; line-height:1.45; }.stream-progress-item i { width:7px; height:7px; flex:0 0 auto; border-radius:50%; background:#d2dcc5; }.stream-progress-item.running { color:#557525; font-weight:750; }.stream-progress-item.running i { background:#89a93e; animation:career-progress-pulse 1s ease-in-out infinite; }.stream-progress-item.completed i { background:#96b95a; }.stream-status,.streamed-answer { margin:10px 0 0 !important; }.streamed-answer { border-top:1px solid #e8eee0; padding-top:10px; } @keyframes career-progress-pulse { 50% { transform:scale(.72); opacity:.55; } }
@keyframes career-thinking-spin { to { transform:rotate(360deg); } }
.composer { border-top:1px solid #edf0e7; background:#fff; padding:12px 16px 14px; }.composer-toolbar { min-height:36px; flex-wrap:wrap; border-bottom:1px solid #edf0e7; padding-bottom:9px; }.input-tools,.session-tools { display:flex; min-width:0; flex-wrap:wrap; align-items:center; gap:7px; }.session-tools { margin-left:auto; justify-content:flex-end; }.job-url-row { display:grid; grid-template-columns:auto 1fr; align-items:center; gap:8px; margin:10px 0 9px; color:#738068; font-size:12px; font-weight:800; }.job-url-row input,.composer textarea { padding:10px 11px; }.composer textarea { display:block; min-height:68px; margin-top:10px; resize:vertical; }.composer-footer { margin-top:9px; }.input-tools { justify-content:flex-start; }.resume-input { display:none; }.chip-button.active { max-width:240px; overflow:hidden; background:#eaf4d4; color:#5a7d21; text-overflow:ellipsis; white-space:nowrap; }.file-clear-button { border-color:#f0d5d5; background:#fff8f8; color:#a65a5a; }.model-select { width:auto; max-width:210px; padding:7px 9px; color:#65775a; font-size:12px; font-weight:700; }.send-button { min-width:88px; padding:9px 13px; }.composer-footer > small { color:#98a18e; font-size:11px; }
.interview-reference-row { display:flex; flex-wrap:wrap; gap:7px; margin-top:9px; }
.interview-reference-chip { display:inline-flex; align-items:center; max-width:100%; gap:6px; border:1px solid #cfe1a9; border-radius:999px; background:#f1f8e4; color:#5d7e2a; padding:5px 7px 5px 10px; font-size:12px; font-weight:800; }
.interview-reference-chip > span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.interview-reference-chip button { display:grid; width:18px; height:18px; place-items:center; border:0; border-radius:50%; background:transparent; color:#76954b; font:inherit; font-size:16px; line-height:1; cursor:pointer; }
.interview-reference-chip button:hover { background:#dcecc0; color:#416217; }
.interview-mention-menu { display:grid; max-height:206px; margin-top:8px; overflow:auto; border:1px solid #dce6ce; border-radius:12px; background:#fff; box-shadow:0 12px 28px rgba(68,84,44,.12); }
.interview-mention-menu button { display:grid; gap:3px; border:0; border-bottom:1px solid #edf2e7; background:#fff; color:#34472a; padding:10px 12px; text-align:left; cursor:pointer; }
.interview-mention-menu button:last-child { border-bottom:0; }.interview-mention-menu button:hover { background:#f3f8e9; }.interview-mention-menu strong { font-size:13px; }.interview-mention-menu small { color:#87977b; font-size:12px; }
.career-error-toast { position:fixed; z-index:1400; top:50%; left:50%; display:flex; width:min(620px,calc(100vw - 40px)); box-sizing:border-box; align-items:flex-start; justify-content:space-between; gap:18px; transform:translate(-50%,-50%); border:1px solid #efbcbc; border-radius:18px; background:#fff8f8; box-shadow:0 22px 70px rgba(114,42,42,.24); color:#943f3f; padding:19px 18px 19px 21px; animation:career-toast-in .18s ease-out; }.career-error-toast strong { display:block; font-size:17px; line-height:1.35; }.career-error-toast p { margin:5px 0 0; font-size:16px; font-weight:650; line-height:1.6; }.toast-close-button { display:grid; width:30px; height:30px; flex:0 0 auto; place-items:center; border:1px solid #edcaca; border-radius:9px; background:#fff; color:#a95050; font-size:22px; line-height:1; }.toast-close-button:hover { background:#fff0f0; }@keyframes career-toast-in { from { opacity:0; transform:translate(-50%,-46%); } to { opacity:1; transform:translate(-50%,-50%); } }
.model-manager-button { border-color:#cfdcb7; background:#f1f7e5; color:#5c7a28; }.model-dialog-backdrop { position:fixed; z-index:1200; inset:0; display:grid; place-items:center; box-sizing:border-box; padding:28px; background:rgba(29,40,25,.42); backdrop-filter:blur(5px); }.model-dialog { display:flex; width:min(960px,100%); max-height:min(780px,calc(100vh - 56px)); flex-direction:column; overflow:hidden; border:1px solid #dce6cf; border-radius:24px; background:#fff; box-shadow:0 28px 80px rgba(23,37,20,.28); }.model-dialog-header { display:flex; align-items:flex-start; justify-content:space-between; gap:20px; border-bottom:1px solid #ecf0e5; padding:24px 28px 20px; }.model-dialog-header h2 { margin:4px 0 0; color:#243323; font-size:23px; }.dialog-close-button { display:grid; width:34px; height:34px; flex:0 0 auto; place-items:center; border:1px solid #e1e8d7; border-radius:10px; background:#fafcf7; color:#728067; font-size:23px; line-height:1; }.model-dialog-body { min-height:240px; overflow-y:auto; padding:22px 28px 26px; }.connection-toolbar { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:16px; }.connection-toolbar strong,.connection-toolbar small { display:block; }.connection-toolbar strong { color:#354333; font-size:16px; }.connection-toolbar small,.dialog-helper { margin-top:4px; color:#889281; font-size:12px; }.dialog-primary-button,.dialog-secondary-button { border:0; border-radius:11px; padding:10px 15px; font-size:13px; font-weight:850; }.dialog-primary-button { background:#89a93e; color:#fff; box-shadow:0 8px 18px rgba(112,144,51,.2); }.dialog-primary-button:disabled { cursor:wait; opacity:.6; }.dialog-secondary-button,.back-button { border:1px solid #e1e8d7; background:#fff; color:#64735a; }.connection-card-list { display:grid; gap:10px; }.connection-card { display:grid; width:100%; grid-template-columns:18px 42px minmax(0,1fr) auto; align-items:center; gap:13px; border:1px solid #e3ead9; border-radius:15px; background:#fff; color:#31402f; padding:13px 15px; text-align:left; transition:border-color .16s ease,background .16s ease,transform .16s ease; }.connection-card:hover { border-color:#a7c66d; background:#fbfdf7; transform:translateY(-1px); }.connection-drag { color:#bcc7b4; font-size:20px; letter-spacing:-3px; }.provider-avatar { display:grid; width:38px; height:38px; place-items:center; border:1px solid #dfe8d0; border-radius:12px; background:#f3f8e9; color:#6d8c33; font-size:11px; font-weight:900; }.provider-avatar.large { width:44px; height:44px; border-radius:14px; }.connection-card-copy { min-width:0; }.connection-card-copy strong,.connection-card-copy small { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }.connection-card-copy strong { font-size:14px; }.connection-card-copy small { margin-top:3px; color:#899481; font-size:12px; }.connection-meta { display:grid; justify-items:end; gap:5px; }.connection-meta small { color:#98a18f; font-size:11px; }.connection-empty-state { display:grid; min-height:250px; place-content:center; justify-items:center; border:1px dashed #d9e4cb; border-radius:16px; background:#fbfdf8; color:#7a8870; text-align:center; }.connection-empty-state > span { color:#90b04c; font-size:30px; }.connection-empty-state strong { margin-top:8px; color:#526449; }.connection-empty-state p { max-width:320px; margin:7px 0 0; font-size:13px; line-height:1.6; }.back-button { border-radius:9px; padding:7px 10px; color:#66765b; font-size:12px; font-weight:800; }.model-dialog-body h3 { margin:20px 0 4px; color:#31402e; font-size:18px; }.provider-picker-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin-top:18px; }.provider-picker-card { display:grid; grid-template-columns:44px minmax(0,1fr); align-items:center; gap:11px; border:1px solid #e2ead8; border-radius:16px; background:#fbfcf9; color:#334131; padding:15px; text-align:left; transition:border-color .16s ease,box-shadow .16s ease; }.provider-picker-card:hover { border-color:#97ba57; box-shadow:0 10px 25px rgba(91,120,42,.1); }.provider-picker-card strong,.provider-picker-card small { display:block; }.provider-picker-card small { margin-top:4px; color:#84907c; font-size:11px; line-height:1.5; }.provider-picker-card em { grid-column:1 / -1; justify-self:start; border-radius:999px; background:#eef6df; color:#66842e; padding:4px 7px; font-size:10px; font-style:normal; font-weight:850; }.connection-form-body { padding-bottom:22px; }.selected-provider-banner { display:flex; align-items:center; gap:11px; margin-top:17px; border:1px solid #dce9c8; border-radius:15px; background:#f6faef; padding:12px; }.selected-provider-banner div { min-width:0; flex:1; }.selected-provider-banner strong,.selected-provider-banner small { display:block; }.selected-provider-banner small { margin-top:3px; color:#7d8973; font-size:12px; }.selected-provider-banner > span:last-child { border-radius:999px; background:#e7f1d4; color:#63822b; padding:5px 8px; font-size:11px; font-weight:850; }.connection-form-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; margin-top:18px; }.connection-form-grid label { display:grid; gap:5px; color:#4e5c49; font-size:13px; font-weight:850; }.connection-form-grid label > span { color:#8a9583; font-size:11px; font-weight:500; }.connection-form-grid input,.connection-form-grid select { width:100%; box-sizing:border-box; border:1px solid #dfe7d4; border-radius:10px; background:#fff; color:#354334; padding:10px 11px; font:inherit; outline:none; }.connection-form-grid input:focus,.connection-form-grid select:focus { border-color:#91b44b; box-shadow:0 0 0 3px rgba(137,169,62,.12); }.full-width { grid-column:1 / -1; }.capability-fieldset { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; margin:18px 0 0; border:0; padding:0; }.capability-fieldset legend { margin-bottom:8px; color:#4d5d48; font-size:13px; font-weight:850; }.capability-option { display:flex; align-items:flex-start; gap:8px; border:1px solid #e3ead9; border-radius:13px; background:#fbfcf9; padding:11px; }.capability-option input { margin-top:3px; accent-color:#89a93e; }.capability-option strong,.capability-option small { display:block; }.capability-option strong { color:#485846; font-size:12px; }.capability-option small { margin-top:3px; color:#899481; font-size:11px; line-height:1.45; }.connection-test-success,.connection-test-error { display:block; margin:14px 0 0; border-radius:11px; padding:10px 12px; font-size:12px; font-weight:750; line-height:1.6; }.connection-test-success { border:1px solid #cce5aa; background:#f4faea; color:#587b27; }.connection-test-error { border:1px solid #efcaca; background:#fff6f6; color:#9c4848; }.connection-test-error strong { display:block; margin-bottom:2px; }.model-dialog-footer { display:flex; justify-content:flex-end; gap:10px; border-top:1px solid #ecf0e5; background:#fbfcf9; padding:15px 28px; }
.model-dialog { width:min(1080px,100%); max-height:min(840px,calc(100vh - 44px)); }
.connection-setup-body { scroll-behavior:smooth; }
.provider-catalog-section { padding-bottom:22px; border-bottom:1px solid #edf0e7; }
.provider-picker-grid-expanded { grid-template-columns:repeat(4,minmax(0,1fr)); }
.provider-picker-card.selected { border-color:#89a93e; background:#f2f8e7; box-shadow:0 0 0 3px rgba(137,169,62,.12); }
.connection-config-section { scroll-margin-top:14px; padding-top:20px; }
.connection-section-heading { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; }
.connection-section-heading h3 { margin-top:0; }
.test-connection-button { color:#50761b; }
.test-connection-button:disabled { cursor:wait; opacity:.6; }
@media (max-width:960px) { .career-workspace { grid-template-columns:1fr; grid-template-rows:minmax(160px,.35fr) minmax(0,1fr); }.career-history-panel { max-height:none; }.conversation-list { grid-template-columns:repeat(2,minmax(0,1fr)); overflow:auto; }.model-settings { grid-template-columns:1fr; }.provider-picker-grid,.provider-picker-grid-expanded { grid-template-columns:1fr; } }
@media (max-width:640px) { .career-workspace { gap:10px; }.career-chat-panel { min-height:0; }.chat-header { min-height:60px; padding:12px 14px; }.message-list { padding:14px; }.composer { padding:10px 12px max(12px, env(safe-area-inset-bottom)); }.composer-toolbar,.composer-footer { align-items:flex-start; flex-direction:column; }.session-tools { margin-left:0; }.career-history-panel { max-height:none; }.conversation-list { grid-template-columns:1fr; max-height:200px; }.model-form { grid-template-columns:1fr; }.message,.agent-message { max-width:94%; }.model-select { max-width:100%; }.send-button { align-self:stretch; min-height:44px; }.career-error-toast { width:calc(100vw - 28px); gap:12px; padding:17px; }.career-error-toast strong { font-size:16px; }.career-error-toast p { font-size:15px; }.model-dialog-backdrop { align-items:end; padding:0; }.model-dialog { max-height:90dvh; border-radius:22px 22px 0 0; }.model-dialog-header,.model-dialog-body,.model-dialog-footer { padding-right:18px; padding-left:18px; }.connection-toolbar,.connection-card { align-items:flex-start; }.connection-toolbar { flex-direction:column; }.connection-card { grid-template-columns:18px 38px minmax(0,1fr); }.connection-meta { grid-column:3; justify-items:start; }.connection-form-grid,.capability-fieldset { grid-template-columns:1fr; }.dialog-primary-button,.dialog-secondary-button { min-height:44px; } }
@media (max-width:960px) and (max-height:680px) { .career-workspace { height:auto; min-height:calc(100dvh - 76px); grid-template-rows:auto minmax(520px,1fr); overflow:visible; }.career-history-panel { height:auto; overflow:visible; }.conversation-list { max-height:196px; flex:none; }.career-chat-panel { min-height:520px; overflow:visible; }.message-list { min-height:220px; }.composer { position:sticky; bottom:0; z-index:2; } }

/* 手机端只保留一个页面级滚动面，避免会话、消息和主容器互相抢滚动。 */
@media (max-width:960px) {
  .career-workspace {
    height:auto;
    min-height:calc(100dvh - 92px);
    grid-template-rows:auto auto;
    align-content:start;
    overflow:visible;
  }

  .career-history-panel {
    height:auto;
    max-height:none;
    overflow:visible;
  }

  .conversation-list {
    flex:none;
    max-height:228px;
  }

  .career-chat-panel {
    height:auto;
    min-height:520px;
    overflow:visible;
  }

  .message-list {
    min-height:270px;
    flex:none;
    overflow:visible;
  }

  .composer {
    position:sticky;
    z-index:5;
    bottom:0;
    box-shadow:0 -10px 22px rgba(47,62,37,.08);
  }
}

@media (max-width:640px) {
  .career-history-panel {
    padding:12px;
  }

  .career-chat-panel {
    min-height:440px;
  }

  .message-list {
    min-height:228px;
  }

  .model-dialog-backdrop {
    align-items:end;
    padding:0;
  }

  .model-dialog {
    width:100%;
    max-height:calc(100dvh - env(safe-area-inset-top));
    border-radius:22px 22px 0 0;
  }

  .model-dialog-body {
    overscroll-behavior:contain;
    padding-bottom:22px;
  }

  .model-dialog-footer {
    flex-wrap:wrap;
    padding:12px 18px max(12px, env(safe-area-inset-bottom));
    box-shadow:0 -8px 20px rgba(47,62,37,.07);
  }

  .model-dialog-footer .dialog-primary-button,
  .model-dialog-footer .dialog-secondary-button {
    min-height:44px;
    flex:1 1 126px;
  }

  .selected-provider-banner {
    align-items:flex-start;
  }

  .selected-provider-banner > span:last-child {
    white-space:nowrap;
  }
}

@media (max-width:960px) and (max-height:760px) {
  .career-workspace {
    min-height:0;
  }

  .career-chat-panel {
    min-height:400px;
  }

  .conversation-list {
    max-height:156px;
  }

  .message-list {
    min-height:176px;
  }

  .composer textarea {
    min-height:56px;
  }
}
</style>
