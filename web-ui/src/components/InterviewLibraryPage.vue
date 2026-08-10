<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const treeItems = ref([])
const treeLoading = ref(false)
const treeError = ref('')
const query = ref('')
const selectedExperience = ref(null)
const selectedExperienceId = ref('')
const detailLoading = ref(false)
const detailError = ref('')
const editMode = ref(false)
const saving = ref(false)
const editorMarkdown = ref('')
const editorSummary = ref('')
const editorTags = ref('')
const showImportModal = ref(false)
const showCollectionModal = ref(false)
const importMode = ref('text')
const importing = ref(false)
const importProgress = ref(null)
const importError = ref('')
const importNotice = ref('')
const importFiles = ref([])
const fileInput = ref(null)
const fileParseResult = ref(null)
const parsedFileImports = ref([])
const fileImportStrategy = ref('merge')
const collectionMode = ref('keyword')
const collectionPlatforms = ref([])
const collectionLoading = ref(false)
const collectionSubmitting = ref(false)
const collectionError = ref('')
const collectionNotice = ref('')
const collectionJob = ref(null)
const collectionCandidates = ref([])
const collectionDraft = ref(createCollectionDraft())
let queryTimer = null

const textDraft = ref(createTextDraft())
const fileDraft = ref(createFileDraft())

const emptyState = computed(() => !detailLoading.value && !selectedExperience.value)
const selectedTags = computed(() => selectedExperience.value?.tags ?? [])
const renderedMarkdownBlocks = computed(() => parseMarkdownBlocks(selectedExperience.value?.markdown_content ?? ''))
const importFileCount = computed(() => importFiles.value.length)
const fileStrategyDescription = computed(() => fileImportStrategy.value === 'merge'
  ? `将 ${importFileCount.value || '所选'} 份材料归并为一份完整面经，适合同一场或同一岗位的多页资料。`
  : `将每份材料独立入库为一份面经，适合同一次导入多个不同岗位或公司的资料。`)

function displayMarkdownText(value) {
  return value
    .replace(/!\[(.*?)\]\([^)]*\)/g, '$1')
    .replace(/\[(.*?)\]\([^)]*\)/g, '$1')
    .replace(/\*\*(.*?)\*\*/g, '$1')
    .replace(/__(.*?)__/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/~~(.*?)~~/g, '$1')
    .trim()
}

function parseMarkdownBlocks(markdown) {
  const blocks = []
  let paragraph = []
  let list = null

  const flushParagraph = () => {
    const text = displayMarkdownText(paragraph.join(' ').trim())
    if (text) blocks.push({ type: 'paragraph', text })
    paragraph = []
  }
  const flushList = () => {
    if (list?.items.length) blocks.push(list)
    list = null
  }

  for (const rawLine of markdown.replace(/\r\n/g, '\n').split('\n')) {
    const line = rawLine.trim()
    if (!line) {
      flushParagraph()
      flushList()
      continue
    }
    if (/^(---|\*\*\*|___)$/.test(line)) {
      flushParagraph()
      flushList()
      blocks.push({ type: 'divider' })
      continue
    }
    const heading = line.match(/^(#{1,4})\s+(.+)$/)
    if (heading) {
      flushParagraph()
      flushList()
      blocks.push({ type: 'heading', level: heading[1].length, text: displayMarkdownText(heading[2]) })
      continue
    }
    const ordered = line.match(/^\d+[.)]\s+(.+)$/)
    const unordered = line.match(/^[-*+]\s+(.+)$/)
    if (ordered || unordered) {
      flushParagraph()
      const nextOrdered = Boolean(ordered)
      if (!list || list.ordered !== nextOrdered) {
        flushList()
        list = { type: 'list', ordered: nextOrdered, items: [] }
      }
      list.items.push(displayMarkdownText((ordered ?? unordered)[1]))
      continue
    }
    flushList()
    paragraph.push(line.replace(/^>\s?/, ''))
  }
  flushParagraph()
  flushList()
  return blocks.length ? blocks : [{ type: 'paragraph', text: '暂无可展示的面经正文。' }]
}

function createTextDraft() {
  return {
    companyName: '',
    roleName: '',
    interviewDate: '',
    sourcePlatform: '',
    sourceUrl: '',
    tags: '',
    summary: '',
    markdown: ''
  }
}

function createFileDraft() {
  return {
    companyName: '',
    roleName: '',
    interviewDate: '',
    sourcePlatform: '',
    sourceUrl: '',
    tags: '',
    summary: '',
    markdown: ''
  }
}

function normalizeParsedFileDraft(payload, manualDraft = createFileDraft()) {
  const parsedDraft = payload?.draft ?? {}
  return {
    companyName: manualDraft.companyName.trim() || parsedDraft.company_name || '',
    roleName: manualDraft.roleName.trim() || parsedDraft.role_name || '',
    interviewDate: manualDraft.interviewDate || parsedDraft.interview_date || '',
    sourcePlatform: manualDraft.sourcePlatform.trim() || parsedDraft.source_platform || '',
    sourceUrl: manualDraft.sourceUrl.trim() || parsedDraft.source_url || '',
    tags: manualDraft.tags.trim() || (parsedDraft.tags ?? []).join('，'),
    summary: manualDraft.summary.trim() || parsedDraft.summary_text || '',
    markdown: payload?.markdown_content ?? ''
  }
}

function buildFileExperiencePayload(draft) {
  return {
    company_name: draft.companyName.trim() || '待归档公司',
    role_name: draft.roleName.trim() || '未识别岗位',
    interview_date: draft.interviewDate || null,
    markdown_content: draft.markdown,
    source_type: 'manual_upload',
    source_platform: draft.sourcePlatform.trim() || null,
    source_url: draft.sourceUrl.trim() || null,
    summary_text: draft.summary.trim() || null,
    tags: parseTags(draft.tags)
  }
}

function mergeParsedFileImports(items) {
  const firstDraft = items[0]?.draft ?? createFileDraft()
  const tags = [...new Set(items.flatMap((item) => parseTags(item.draft.tags)))]
  const sections = items.map((item, index) => {
    const title = item.fileName || `材料 ${index + 1}`
    return `## 材料 ${index + 1}：${title}\n\n${item.draft.markdown.trim()}`
  })
  return {
    ...firstDraft,
    tags: tags.join('，'),
    summary: firstDraft.summary || `由 ${items.length} 份材料归并生成，建议结合各材料中的面试轮次与考题复盘。`,
    markdown: `# 合并面经材料\n\n${sections.join('\n\n---\n\n')}`
  }
}

function createCollectionDraft() {
  return {
    platformKey: 'xiaohongshu',
    keyword: '',
    requestedLimit: 10,
    sourceUrl: '',
    companyName: '',
    roleName: '',
    interviewDate: '',
    summary: '',
    tags: ''
  }
}

function parseTags(value) {
  return value
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function formatDate(value) {
  if (!value) return '日期待补充'
  return value
}

function formatUpdatedAt(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  }).format(date)
}

function statusText(value) {
  const labels = {
    queued: '等待处理',
    parsing: '解析中',
    parsed: '已解析',
    indexing: '建立索引中',
    indexed: '可检索',
    failed: '处理失败'
  }
  return labels[value] ?? value
}

function sourceText(value) {
  const labels = {
    manual_upload: '文件导入',
    manual_text: '手动录入',
    public_url: '公开链接',
    authenticated_session: '授权会话',
    official_api: '平台接口'
  }
  return labels[value] ?? value ?? '手动录入'
}

async function requestJson(path, options = {}) {
  let response
  try {
    response = await fetch(path, {
      ...options,
      headers: {
        ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
        ...(options.headers ?? {})
      }
    })
  } catch (error) {
    // Vite 代理无法连接后端时，浏览器只会抛出没有上下文的 Failed to fetch。
    // 这里转成可操作的提示，避免用户误以为是上传文件本身损坏。
    if (error instanceof TypeError && /failed to fetch/i.test(error.message)) {
      throw new Error('无法连接开发后端（18080）。请运行 scripts\\start_dev_backend.ps1，并访问 http://127.0.0.1:18080/api/health 检查服务状态。')
    }
    throw error
  }

  const rawPayload = await response.text()
  let payload = {}
  try {
    payload = rawPayload ? JSON.parse(rawPayload) : {}
  } catch {
    // 代理层错误有时返回纯文本；保留正文，让界面能够展示真实故障而不是只显示状态码。
  }
  if (!response.ok) {
    const detail = typeof payload?.detail === 'string' ? payload.detail : rawPayload.trim()
    if (response.status === 404 && path.includes('/interview-library/parse-file')) {
      throw new Error('当前开发后端尚未加载面经文件解析接口。请使用项目启动脚本重启后端后重试。')
    }
    throw new Error(detail || `请求失败：${response.status}`)
  }
  return payload
}

async function requestFileParseWithProgress(data) {
  let response
  try {
    response = await fetch('/api/career/interview-library/parse-file-stream', {
      method: 'POST',
      body: data
    })
  } catch (error) {
    if (error instanceof TypeError && /failed to fetch/i.test(error.message)) {
      throw new Error('无法连接开发后端（18080）。请运行 scripts\\start_dev_backend.ps1，并访问 http://127.0.0.1:18080/api/health 检查服务状态。')
    }
    throw error
  }

  if (!response.ok) {
    const detail = await response.text()
    throw new Error(detail || `文件预解析请求失败：${response.status}`)
  }
  if (!response.body) {
    throw new Error('当前浏览器未返回解析进度流，请刷新页面后重试。')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let payload = null

  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done })
    let boundary = buffer.indexOf('\n')
    while (boundary >= 0) {
      const line = buffer.slice(0, boundary).trim()
      buffer = buffer.slice(boundary + 1)
      boundary = buffer.indexOf('\n')
      if (!line) continue
      let event
      try {
        event = JSON.parse(line)
      } catch {
        continue
      }
      if (event.event === 'progress') {
        importProgress.value = {
          percent: Math.max(0, Math.min(100, Number(event.percent) || 0)),
          phase: event.phase || '正在解析材料',
          detail: event.detail || '正在生成可编辑的面经草稿。'
        }
      } else if (event.event === 'result') {
        payload = event.payload
      } else if (event.event === 'error') {
        throw new Error(event.message || '文件解析未完成。')
      }
    }
    if (done) break
  }
  if (!payload) throw new Error('解析服务未返回可用草稿，请稍后重试。')
  return payload
}

async function loadTree({ preserveSelection = true } = {}) {
  treeLoading.value = true
  treeError.value = ''
  try {
    const params = new URLSearchParams()
    if (query.value.trim()) params.set('query', query.value.trim())
    const payload = await requestJson(`/api/career/interview-library/tree?${params.toString()}`)
    treeItems.value = payload.items ?? []

    if (preserveSelection && selectedExperienceId.value) {
      const stillExists = treeItems.value.some((company) =>
        company.children?.some((item) => item.id === selectedExperienceId.value)
      )
      if (!stillExists && query.value.trim()) {
        // 搜索过滤后保留当前正文，避免用户在编辑时意外丢失内容。
        return
      }
    }
  } catch (error) {
    treeError.value = error instanceof Error ? error.message : '面经树加载失败'
  } finally {
    treeLoading.value = false
  }
}

function queueTreeSearch() {
  if (queryTimer) window.clearTimeout(queryTimer)
  queryTimer = window.setTimeout(() => loadTree(), 220)
}

async function selectExperience(experienceId) {
  if (!experienceId || experienceId === selectedExperienceId.value) return
  if (editMode.value && !window.confirm('当前修改尚未保存，确定切换到另一份面经吗？')) return

  detailLoading.value = true
  detailError.value = ''
  editMode.value = false
  try {
    const payload = await requestJson(`/api/career/interview-library/experiences/${encodeURIComponent(experienceId)}`)
    selectedExperience.value = payload
    selectedExperienceId.value = payload.id
    syncEditor(payload)
  } catch (error) {
    detailError.value = error instanceof Error ? error.message : '面经正文加载失败'
  } finally {
    detailLoading.value = false
  }
}

function syncEditor(experience) {
  editorMarkdown.value = experience?.markdown_content ?? ''
  editorSummary.value = experience?.summary_text ?? ''
  editorTags.value = (experience?.tags ?? []).join('，')
}

function startEditing() {
  if (!selectedExperience.value) return
  syncEditor(selectedExperience.value)
  detailError.value = ''
  editMode.value = true
}

function cancelEditing() {
  syncEditor(selectedExperience.value)
  editMode.value = false
  detailError.value = ''
}

async function saveExperience() {
  if (!selectedExperience.value || !editorMarkdown.value.trim()) {
    detailError.value = '面经正文不能为空。'
    return
  }

  saving.value = true
  detailError.value = ''
  try {
    const payload = await requestJson(
      `/api/career/interview-library/experiences/${encodeURIComponent(selectedExperience.value.id)}`,
      {
        method: 'PUT',
        body: JSON.stringify({
          markdown_content: editorMarkdown.value,
          summary_text: editorSummary.value.trim() || null,
          tags: parseTags(editorTags.value)
        })
      }
    )
    selectedExperience.value = payload
    syncEditor(payload)
    editMode.value = false
    await loadTree()
  } catch (error) {
    detailError.value = error instanceof Error ? error.message : '保存失败，请稍后重试。'
  } finally {
    saving.value = false
  }
}

function openImport(mode = 'text') {
  importMode.value = mode
  importError.value = ''
  importNotice.value = ''
  if (mode === 'file') {
    fileParseResult.value = null
    parsedFileImports.value = []
  }
  showImportModal.value = true
}

function closeImport() {
  if (importing.value) return
  showImportModal.value = false
  importError.value = ''
  importNotice.value = ''
  fileParseResult.value = null
  parsedFileImports.value = []
}

async function openCollection(mode = 'keyword') {
  collectionMode.value = mode
  collectionError.value = ''
  collectionNotice.value = ''
  collectionJob.value = null
  collectionCandidates.value = []
  showCollectionModal.value = true
  if (collectionPlatforms.value.length || collectionLoading.value) return
  collectionLoading.value = true
  try {
    const payload = await requestJson('/api/career/interview-library/collection-platforms')
    collectionPlatforms.value = payload.items ?? []
    if (!collectionPlatforms.value.some((item) => item.key === collectionDraft.value.platformKey)) {
      collectionDraft.value.platformKey = collectionPlatforms.value[0]?.key ?? ''
    }
  } catch (error) {
    collectionError.value = error instanceof Error ? error.message : '采集平台信息加载失败。'
  } finally {
    collectionLoading.value = false
  }
}

function closeCollection() {
  if (collectionSubmitting.value) return
  showCollectionModal.value = false
  collectionError.value = ''
}

async function submitKeywordCollection() {
  const draft = collectionDraft.value
  if (!draft.platformKey || !draft.keyword.trim()) {
    collectionError.value = '请选择平台并输入检索关键词。'
    return
  }
  collectionSubmitting.value = true
  collectionError.value = ''
  collectionNotice.value = ''
  try {
    const payload = await requestJson('/api/career/interview-library/collection-jobs', {
      method: 'POST',
      body: JSON.stringify({
        platform_key: draft.platformKey,
        keyword: draft.keyword.trim(),
        requested_limit: Number(draft.requestedLimit) || 10
      })
    })
    collectionJob.value = payload
    collectionCandidates.value = []
    collectionNotice.value = payload.status === 'needs_user_interaction'
      ? '任务已记录：该平台尚未接入受条款允许的连接器，因此没有伪造抓取结果。可改用公开链接导入或粘贴正文。'
      : '检索任务已创建，候选资料将在此处展示。'
  } catch (error) {
    collectionError.value = error instanceof Error ? error.message : '创建检索任务失败。'
  } finally {
    collectionSubmitting.value = false
  }
}

async function submitUrlCollection() {
  const draft = collectionDraft.value
  if (!draft.sourceUrl.trim()) {
    collectionError.value = '请输入需要导入的公开 HTTPS 链接。'
    return
  }
  collectionSubmitting.value = true
  collectionError.value = ''
  collectionNotice.value = ''
  try {
    const payload = await requestJson('/api/career/interview-library/collect-url', {
      method: 'POST',
      body: JSON.stringify({ source_url: draft.sourceUrl.trim() })
    })
    collectionJob.value = payload.job
    collectionCandidates.value = [payload.candidate]
    collectionNotice.value = '已读取公开页面并生成候选正文。确认信息后可写入面经库。'
  } catch (error) {
    collectionError.value = error instanceof Error ? error.message : '公开链接读取失败。'
  } finally {
    collectionSubmitting.value = false
  }
}

async function importCollectionCandidate(candidate) {
  const draft = collectionDraft.value
  if (!draft.companyName.trim() || !draft.roleName.trim()) {
    collectionError.value = '导入前请填写公司名称和面试岗位。'
    return
  }
  collectionSubmitting.value = true
  collectionError.value = ''
  try {
    const payload = await requestJson(
      `/api/career/interview-library/collection-candidates/${encodeURIComponent(candidate.id)}/import`,
      {
        method: 'POST',
        body: JSON.stringify({
          company_name: draft.companyName.trim(),
          role_name: draft.roleName.trim(),
          interview_date: draft.interviewDate || null,
          summary_text: draft.summary.trim() || candidate.title || null,
          tags: parseTags(draft.tags)
        })
      }
    )
    await finishImport(payload)
    showCollectionModal.value = false
    collectionDraft.value = createCollectionDraft()
  } catch (error) {
    collectionError.value = error instanceof Error ? error.message : '候选资料入库失败。'
  } finally {
    collectionSubmitting.value = false
  }
}

function handleFileChange(event) {
  importFiles.value = Array.from(event.target.files ?? [])
  fileParseResult.value = null
  parsedFileImports.value = []
  importError.value = ''
  importNotice.value = importFiles.value.length
    ? `已选择 ${importFiles.value.length} 份材料。${fileStrategyDescription.value}`
    : ''
}

async function submitTextImport() {
  const draft = textDraft.value
  if (!draft.companyName.trim() || !draft.roleName.trim() || !draft.markdown.trim()) {
    importError.value = '请填写公司、岗位和面经正文。'
    return
  }
  importing.value = true
  importError.value = ''
  try {
    const payload = await requestJson('/api/career/interview-library/experiences', {
      method: 'POST',
      body: JSON.stringify({
        company_name: draft.companyName,
        role_name: draft.roleName,
        interview_date: draft.interviewDate || null,
        markdown_content: draft.markdown,
        source_type: draft.sourceUrl.trim() ? 'public_url' : 'manual_text',
        source_platform: draft.sourcePlatform.trim() || null,
        source_url: draft.sourceUrl.trim() || null,
        summary_text: draft.summary.trim() || null,
        tags: parseTags(draft.tags)
      })
    })
    await finishImport(payload)
    textDraft.value = createTextDraft()
  } catch (error) {
    importError.value = error instanceof Error ? error.message : '面经入库失败。'
  } finally {
    importing.value = false
  }
}

async function parseFileImport() {
  const manualDraft = { ...fileDraft.value }
  if (!importFiles.value.length) {
    importError.value = '请先选择至少一份文件。公司、岗位等信息可以由系统自动识别，也可以在解析后手动修正。'
    return
  }
  importing.value = true
  importProgress.value = {
    percent: 3,
    phase: '正在准备解析',
    detail: '正在安全传输文件，并准备提取面经正文。'
  }
  importError.value = ''
  try {
    const parsedItems = []
    for (let index = 0; index < importFiles.value.length; index += 1) {
      const file = importFiles.value[index]
      importProgress.value = {
        percent: Math.max(3, Math.round((index / importFiles.value.length) * 100)),
        phase: `正在解析第 ${index + 1}/${importFiles.value.length} 份材料`,
        detail: file.name
      }
      const data = new FormData()
      if (manualDraft.sourcePlatform.trim()) data.set('source_platform', manualDraft.sourcePlatform)
      if (manualDraft.sourceUrl.trim()) data.set('source_url', manualDraft.sourceUrl)
      data.set('source_file', file)
      const payload = await requestFileParseWithProgress(data)
      parsedItems.push({
        fileName: file.name,
        draft: normalizeParsedFileDraft(payload, manualDraft),
        recognition: payload.recognition ?? {}
      })
    }
    parsedFileImports.value = parsedItems
    if (fileImportStrategy.value === 'merge') {
      fileDraft.value = mergeParsedFileImports(parsedItems)
    } else {
      fileDraft.value = { ...parsedItems[0].draft }
    }
    fileParseResult.value = {
      confidence: parsedItems.reduce((total, item) => total + Number(item.recognition.confidence ?? 0), 0) / parsedItems.length,
      evidence: parsedItems.flatMap((item) => item.recognition.evidence ?? []).slice(0, 8),
      warnings: parsedItems.flatMap((item) => item.recognition.warnings ?? [])
    }
    importNotice.value = fileImportStrategy.value === 'merge'
      ? `已完成 ${parsedItems.length} 份材料的识别并归并为一份面经。请核对字段和正文后保存。`
      : `已完成 ${parsedItems.length} 份材料的识别。保存后会生成 ${parsedItems.length} 份独立面经；当前展示第一份内容供核对。`
  } catch (error) {
    importError.value = error instanceof Error ? error.message : '文件解析失败。'
  } finally {
    if (importProgress.value?.percent === 100) {
      await new Promise((resolve) => window.setTimeout(resolve, 240))
    }
    importing.value = false
    importProgress.value = null
  }
}

async function saveParsedFileImport() {
  if (!fileParseResult.value || !parsedFileImports.value.length || !fileDraft.value.markdown.trim()) {
    importError.value = '请先完成文件解析，并确认已生成可用的面经正文。'
    return
  }
  importing.value = true
  importError.value = ''
  try {
    const drafts = fileImportStrategy.value === 'merge'
      ? [fileDraft.value]
      : parsedFileImports.value.map((item) => item.draft)
    const imported = []
    for (const draft of drafts) {
      const payload = await requestJson('/api/career/interview-library/experiences', {
        method: 'POST',
        body: JSON.stringify(buildFileExperiencePayload(draft))
      })
      imported.push(payload)
    }
    await finishImport(imported[imported.length - 1], { importedCount: imported.length })
    fileDraft.value = createFileDraft()
    fileParseResult.value = null
    parsedFileImports.value = []
    importFiles.value = []
    if (fileInput.value) fileInput.value.value = ''
  } catch (error) {
    importError.value = error instanceof Error ? error.message : '面经入库失败。'
  } finally {
    importing.value = false
  }
}

async function finishImport(payload, { importedCount = 1 } = {}) {
  importNotice.value = importedCount > 1
    ? `已分别入库 ${importedCount} 份面经，并建立检索索引。`
    : `已入库并建立 ${payload.status === 'indexed' ? '检索索引' : '解析记录'}：${payload.job_name}`
  await loadTree({ preserveSelection: false })
  selectedExperience.value = payload
  selectedExperienceId.value = payload.id
  syncEditor(payload)
  window.setTimeout(() => {
    showImportModal.value = false
    importNotice.value = ''
  }, 700)
}

onMounted(() => loadTree({ preserveSelection: false }))

onBeforeUnmount(() => {
  if (queryTimer) window.clearTimeout(queryTimer)
})
</script>

<template>
  <section class="interview-library-shell">
    <header class="library-header">
      <div class="library-heading">
        <p class="library-description">把零散经历沉淀成可检索、可追溯的面试证据</p>
      </div>
      <div class="library-actions">
        <button type="button" class="quiet-action collection-action" @click="openCollection('keyword')">自动采集</button>
        <button type="button" class="quiet-action" @click="openImport('text')">粘贴正文</button>
        <button type="button" class="primary-action" @click="openImport('file')">导入材料</button>
      </div>
    </header>

    <section class="library-layout">
      <aside class="interview-tree-pane" aria-label="面经树">
        <div class="tree-pane-header">
          <div>
            <p class="section-label">面经树</p>
            <strong>{{ treeItems.length }} 家公司</strong>
          </div>
          <button type="button" class="tree-refresh" :disabled="treeLoading" title="刷新面经树" @click="loadTree()">↻</button>
        </div>

        <label class="tree-search">
          <span>⌕</span>
          <input v-model="query" type="search" placeholder="搜索公司、岗位或日期" @input="queueTreeSearch" />
        </label>

        <p v-if="treeError" class="inline-error">{{ treeError }}</p>
        <div v-else-if="treeLoading" class="tree-placeholder">正在整理面经树…</div>
        <div v-else-if="!treeItems.length" class="tree-placeholder">
          <strong>还没有面经</strong>
          <span>从顶部导入一份材料，系统会自动按公司和岗位归档。</span>
        </div>
        <nav v-else class="company-tree">
          <details v-for="company in treeItems" :key="company.id" open>
            <summary>
              <span class="tree-company-mark">◫</span>
              <span>{{ company.label }}</span>
              <em>{{ company.children?.length ?? 0 }}</em>
            </summary>
            <div class="tree-leaves">
              <button
                v-for="experience in company.children"
                :key="experience.id"
                type="button"
                class="tree-leaf"
                :class="{ active: selectedExperienceId === experience.id }"
                @click="selectExperience(experience.id)"
              >
                <span class="leaf-line"></span>
                <span class="leaf-copy">
                  <strong>{{ experience.label }}</strong>
                  <small>{{ experience.role_name }} · {{ formatDate(experience.interview_date) }}</small>
                </span>
                <i :title="statusText(experience.status)" :class="`status-dot status-${experience.status}`"></i>
              </button>
            </div>
          </details>
        </nav>
      </aside>

      <article class="interview-detail-pane">
        <div v-if="detailLoading" class="detail-state">
          <span class="thinking-orbit"></span>
          <strong>正在读取面经正文</strong>
          <p>加载来源、结构化 Markdown 与检索状态。</p>
        </div>

        <div v-else-if="detailError" class="detail-state error-state">
          <strong>正文暂时无法打开</strong>
          <p>{{ detailError }}</p>
          <button type="button" class="quiet-action" @click="selectedExperienceId && selectExperience(selectedExperienceId)">重试</button>
        </div>

        <div v-else-if="emptyState" class="detail-state library-empty-state">
          <div class="empty-index-mark">@</div>
          <p class="section-label">RETRIEVAL READY</p>
          <h3>从一份真实面经开始</h3>
          <p>导入 PDF、Word、Excel、图片或整理好的 Markdown。平台只持久化解析后的文本、来源与索引，不保留上传原件。</p>
          <button type="button" class="primary-action" @click="openImport('file')">导入第一份材料</button>
        </div>

        <template v-else>
          <header class="experience-header">
            <div>
              <p class="section-label">{{ selectedExperience.company_name }}</p>
              <h3>{{ selectedExperience.job_name }}</h3>
              <div class="experience-meta">
                <span>{{ selectedExperience.role_name }}</span>
                <span>{{ formatDate(selectedExperience.interview_date) }}</span>
                <span>{{ sourceText(selectedExperience.source_type) }}</span>
                <a v-if="selectedExperience.source_url" :href="selectedExperience.source_url" target="_blank" rel="noreferrer">查看来源 ↗</a>
              </div>
            </div>
            <div class="experience-actions">
              <span class="index-status" :class="`index-${selectedExperience.status}`">{{ statusText(selectedExperience.status) }}</span>
              <button v-if="!editMode" type="button" class="quiet-action" @click="startEditing">编辑正文</button>
              <template v-else>
                <button type="button" class="quiet-action" :disabled="saving" @click="cancelEditing">取消</button>
                <button type="button" class="primary-action" :disabled="saving" @click="saveExperience">
                  {{ saving ? '保存并重建索引…' : '保存并更新' }}
                </button>
              </template>
            </div>
          </header>

          <section class="evidence-strip">
            <div>
              <span>来源</span>
              <strong>{{ selectedExperience.source_platform || sourceText(selectedExperience.source_type) }}</strong>
            </div>
            <div>
              <span>索引版本</span>
              <strong>{{ selectedExperience.chunking_version || '等待建立' }}</strong>
            </div>
            <div>
              <span>最后更新</span>
              <strong>{{ formatUpdatedAt(selectedExperience.updated_at) }}</strong>
            </div>
            <div class="tag-list">
              <span v-for="tag in selectedTags" :key="tag"># {{ tag }}</span>
              <em v-if="!selectedTags.length">尚未标注主题</em>
            </div>
          </section>

          <div v-if="editMode" class="editor-layout">
            <label class="editor-field summary-field">
              <span>摘要</span>
              <input v-model="editorSummary" type="text" placeholder="一句话说明这份面经的价值" />
            </label>
            <label class="editor-field tag-field">
              <span>标签</span>
              <input v-model="editorTags" type="text" placeholder="例如：一面，Java，系统设计（用逗号分隔）" />
            </label>
            <label class="editor-field markdown-field">
              <span>面经 Markdown</span>
              <textarea v-model="editorMarkdown" spellcheck="false" aria-label="编辑面经 Markdown"></textarea>
            </label>
          </div>
          <section v-else class="reading-layout">
            <aside v-if="selectedExperience.summary_text" class="summary-note">
              <p>核心摘要</p>
              <strong>{{ selectedExperience.summary_text }}</strong>
            </aside>
            <article class="markdown-reading" aria-label="面经正文阅读器">
              <template v-for="(block, index) in renderedMarkdownBlocks" :key="`${block.type}-${index}`">
                <hr v-if="block.type === 'divider'" class="reading-divider" />
                <component
                  :is="`h${Math.min(block.level + 1, 4)}`"
                  v-else-if="block.type === 'heading'"
                  class="reading-heading"
                >{{ block.text }}</component>
                <ol v-else-if="block.type === 'list' && block.ordered" class="reading-list reading-ordered">
                  <li v-for="(item, itemIndex) in block.items" :key="itemIndex">{{ item }}</li>
                </ol>
                <ul v-else-if="block.type === 'list'" class="reading-list">
                  <li v-for="(item, itemIndex) in block.items" :key="itemIndex">{{ item }}</li>
                </ul>
                <p v-else class="reading-paragraph">{{ block.text }}</p>
              </template>
            </article>
          </section>
        </template>
      </article>
    </section>

    <Teleport to="body">
      <div v-if="showImportModal" class="import-backdrop" role="presentation" @click.self="closeImport">
        <section class="import-dialog" role="dialog" aria-modal="true" aria-labelledby="importTitle">
          <div v-if="importing && importMode === 'file' && !fileParseResult" class="file-parse-overlay" role="status" aria-live="polite">
            <div class="file-parse-progress-card">
              <div class="progress-heading"><span>材料解析中</span><strong>{{ importProgress?.percent ?? 0 }}%</strong></div>
              <div class="progress-track" aria-hidden="true"><i :style="{ width: `${importProgress?.percent ?? 0}%` }"></i></div>
              <strong class="progress-phase">{{ importProgress?.phase || '正在准备解析' }}</strong>
              <p>{{ importProgress?.detail || '系统会自动识别正文、公司、岗位与面试线索。' }}</p>
            </div>
          </div>
          <header>
            <div>
              <p class="library-kicker">NEW INTERVIEW EVIDENCE</p>
              <h2 id="importTitle">导入面经材料</h2>
              <p>上传原件只在解析任务中暂存；入库后仅保留 Markdown、来源信息与检索切片。</p>
            </div>
            <button type="button" class="close-button" :disabled="importing" aria-label="关闭" @click="closeImport">×</button>
          </header>

          <nav class="import-tabs" aria-label="导入方式">
            <button type="button" :class="{ active: importMode === 'text' }" @click="importMode = 'text'">粘贴正文</button>
            <button type="button" :class="{ active: importMode === 'file' }" @click="importMode = 'file'">上传文件</button>
          </nav>

          <p v-if="importError" class="dialog-error">{{ importError }}</p>
          <p v-if="importNotice" class="dialog-success">{{ importNotice }}</p>

          <form v-if="importMode === 'text'" class="import-form" @submit.prevent="submitTextImport">
            <div class="form-grid">
              <label>公司名称<input v-model="textDraft.companyName" required placeholder="例如：字节跳动" /></label>
              <label>面试岗位<input v-model="textDraft.roleName" required placeholder="例如：Java 后端开发工程师" /></label>
              <label>面试日期<input v-model="textDraft.interviewDate" type="date" /></label>
              <label>来源平台<input v-model="textDraft.sourcePlatform" placeholder="例如：牛客、小红书" /></label>
            </div>
            <label>来源 URL（可选）<input v-model="textDraft.sourceUrl" type="url" placeholder="https://…" /></label>
            <label>标签（可选）<input v-model="textDraft.tags" placeholder="一面，Java，系统设计" /></label>
            <label>摘要（可选）<input v-model="textDraft.summary" placeholder="这份面经适合解决什么问题？" /></label>
            <label>面经正文 Markdown<textarea v-model="textDraft.markdown" required placeholder="# 面试流程\n\n## 一面\n- …"></textarea></label>
            <footer><button type="button" class="quiet-action" :disabled="importing" @click="closeImport">取消</button><button class="primary-action" :disabled="importing">{{ importing ? '正在入库…' : '保存并建立索引' }}</button></footer>
          </form>

          <form v-else class="import-form" @submit.prevent="fileParseResult ? saveParsedFileImport() : parseFileImport()">
            <section class="file-intake-intro">
              <strong>先上传，系统自动预填</strong>
              <p>系统会解析公司、岗位、轮次、技术标签和面经正文；下方字段只用于核对与修正，不要求你先手工填写。</p>
            </section>
            <label class="file-picker">选择材料<input ref="fileInput" type="file" multiple accept=".pdf,.doc,.docx,.xls,.xlsx,.jpg,.jpeg,.png,.webp" @change="handleFileChange" /><span>{{ importFileCount ? `已选择 ${importFileCount} 份材料` : '支持多选：PDF、Word、Excel、JPG、PNG、WebP' }}</span></label>
            <fieldset class="file-import-strategy">
              <legend>归档方式</legend>
              <label><input v-model="fileImportStrategy" type="radio" value="merge" :disabled="Boolean(fileParseResult)" />多文件归并为一份面经</label>
              <label><input v-model="fileImportStrategy" type="radio" value="separate" :disabled="Boolean(fileParseResult)" />每个文件独立生成面经</label>
              <p>{{ fileStrategyDescription }}</p>
            </fieldset>
            <section v-if="fileParseResult" class="recognition-card">
              <div><strong>自动识别完成</strong><span>置信度 {{ Math.round((fileParseResult.confidence ?? 0) * 100) }}%</span></div>
              <p v-if="fileParseResult.evidence?.length">{{ fileParseResult.evidence.join(' · ') }}</p>
              <p v-for="warning in fileParseResult.warnings ?? []" :key="warning" class="recognition-warning">{{ warning }}</p>
            </section>
            <div class="form-grid">
              <label>公司名称（可修正）<input v-model="fileDraft.companyName" placeholder="识别失败时可手工补充，例如：字节跳动" /></label>
              <label>面试岗位（可修正）<input v-model="fileDraft.roleName" placeholder="识别失败时可手工补充，例如：AI Agent 开发" /></label>
              <label>面试日期<input v-model="fileDraft.interviewDate" type="date" /></label>
              <label>来源平台<input v-model="fileDraft.sourcePlatform" placeholder="例如：牛客、小红书" /></label>
            </div>
            <label>来源 URL（可选）<input v-model="fileDraft.sourceUrl" type="url" placeholder="https://…" /></label>
            <label>标签（可选）<input v-model="fileDraft.tags" placeholder="一面，Java，系统设计" /></label>
            <label>摘要（可选）<input v-model="fileDraft.summary" placeholder="这份材料的来源或特点" /></label>
            <label v-if="fileParseResult" class="markdown-field">{{ fileImportStrategy === 'merge' ? '归并后的面经正文（可编辑）' : '第一份面经正文（可编辑）' }}<textarea v-model="fileDraft.markdown" required placeholder="解析后的 Markdown 正文会显示在这里"></textarea></label>
            <footer><button type="button" class="quiet-action" :disabled="importing" @click="closeImport">取消</button><button class="primary-action" :disabled="importing">{{ importing ? (fileParseResult ? '正在建立索引…' : '正在解析…') : (fileParseResult ? '确认保存并建立索引' : '解析并预填') }}</button></footer>
          </form>
        </section>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="showCollectionModal" class="collection-backdrop" role="presentation" @click.self="closeCollection">
        <section class="collection-dialog" role="dialog" aria-modal="true" aria-labelledby="collectionTitle">
          <header class="collection-dialog-header">
            <div>
              <p class="library-kicker">PUBLIC INTERVIEW SOURCES</p>
              <h2 id="collectionTitle">采集公开面经资料</h2>
              <p>从公开链接导入正文，或登记关键词采集任务。受登录、验证码或平台规则限制的站点会明确提示需要授权连接器，不会伪造抓取结果。</p>
            </div>
            <button type="button" class="close-button" :disabled="collectionSubmitting" aria-label="关闭" @click="closeCollection">×</button>
          </header>

          <nav class="import-tabs collection-tabs" aria-label="采集方式">
            <button type="button" :class="{ active: collectionMode === 'keyword' }" @click="collectionMode = 'keyword'">关键词采集</button>
            <button type="button" :class="{ active: collectionMode === 'url' }" @click="collectionMode = 'url'">公开链接导入</button>
          </nav>

          <p v-if="collectionError" class="dialog-error">{{ collectionError }}</p>
          <p v-if="collectionNotice" class="dialog-success">{{ collectionNotice }}</p>

          <form v-if="collectionMode === 'keyword'" class="collection-form" @submit.prevent="submitKeywordCollection">
            <section class="collection-section">
              <div class="collection-section-heading">
                <div>
                  <h3>选择资料来源</h3>
                  <p>目前可登记平台关键词任务；只有具备平台许可的连接器才会执行批量采集。</p>
                </div>
                <span v-if="collectionLoading" class="collection-loading">正在加载平台</span>
              </div>
              <div class="platform-grid">
                <button
                  v-for="platform in collectionPlatforms"
                  :key="platform.key"
                  type="button"
                  class="platform-card"
                  :class="{ active: collectionDraft.platformKey === platform.key }"
                  @click="collectionDraft.platformKey = platform.key"
                >
                  <strong>{{ platform.label }}</strong>
                  <span>{{ platform.connector_kind === 'user_authorized_browser' ? '需授权连接器' : '平台能力待接入' }}</span>
                </button>
              </div>
            </section>

            <label class="collection-field">检索关键词
              <input v-model="collectionDraft.keyword" required maxlength="120" placeholder="例如：Agent 开发 面经" />
            </label>
            <label class="collection-field compact-field">计划获取数量
              <input v-model.number="collectionDraft.requestedLimit" type="number" min="1" max="30" />
            </label>

            <section v-if="collectionJob" class="collection-job-card" :class="`job-${collectionJob.status}`">
              <div>
                <span>任务状态</span>
                <strong>{{ collectionJob.status === 'needs_user_interaction' ? '需要授权连接器' : collectionJob.status }}</strong>
              </div>
              <p>{{ collectionJob.error_message || collectionJob.policy_decision || '任务已创建。' }}</p>
            </section>

            <footer>
              <button type="button" class="quiet-action" :disabled="collectionSubmitting" @click="closeCollection">取消</button>
              <button class="primary-action" :disabled="collectionSubmitting || collectionLoading">
                {{ collectionSubmitting ? '正在创建任务…' : '创建采集任务' }}
              </button>
            </footer>
          </form>

          <form v-else class="collection-form" @submit.prevent="submitUrlCollection">
            <section class="collection-section url-section">
              <div class="collection-section-heading">
                <div>
                  <h3>导入一篇公开页面</h3>
                  <p>仅访问你主动提交的公开 HTTPS 页面；不携带第三方登录态，不执行脚本，也不会保存网页原始 HTML。</p>
                </div>
              </div>
              <label class="collection-field">公开 URL
                <input v-model="collectionDraft.sourceUrl" type="url" required placeholder="https://example.com/interview-experience" />
              </label>
            </section>

            <footer v-if="!collectionCandidates.length">
              <button type="button" class="quiet-action" :disabled="collectionSubmitting" @click="closeCollection">取消</button>
              <button class="primary-action" :disabled="collectionSubmitting">{{ collectionSubmitting ? '正在读取页面…' : '读取并生成候选正文' }}</button>
            </footer>

            <section v-for="candidate in collectionCandidates" :key="candidate.id" class="candidate-card">
              <header>
                <div>
                  <p class="section-label">CANDIDATE ARTICLE</p>
                  <h3>{{ candidate.title || '未命名面经正文' }}</h3>
                  <a v-if="candidate.source_url" :href="candidate.source_url" target="_blank" rel="noreferrer">查看公开来源 ↗</a>
                </div>
                <span>{{ candidate.markdown_content?.length || 0 }} 字</span>
              </header>
              <p v-if="candidate.excerpt" class="candidate-excerpt">{{ candidate.excerpt }}</p>
              <details class="candidate-markdown">
                <summary>查看将要入库的 Markdown 正文</summary>
                <pre>{{ candidate.markdown_content }}</pre>
              </details>
              <div class="form-grid candidate-meta-grid">
                <label>公司名称<input v-model="collectionDraft.companyName" required placeholder="例如：字节跳动" /></label>
                <label>面试岗位<input v-model="collectionDraft.roleName" required placeholder="例如：后端开发工程师" /></label>
                <label>面试日期<input v-model="collectionDraft.interviewDate" type="date" /></label>
                <label>主题标签<input v-model="collectionDraft.tags" placeholder="一面，Java，系统设计" /></label>
              </div>
              <label class="collection-field">摘要（可选）
                <input v-model="collectionDraft.summary" placeholder="说明这份面经适合解决的问题" />
              </label>
              <footer>
                <button type="button" class="quiet-action" :disabled="collectionSubmitting" @click="closeCollection">稍后处理</button>
                <button type="button" class="primary-action" :disabled="collectionSubmitting" @click="importCollectionCandidate(candidate)">
                  {{ collectionSubmitting ? '正在入库…' : '确认并建立检索索引' }}
                </button>
              </footer>
            </section>
          </form>
        </section>
      </div>
    </Teleport>
  </section>
</template>

<style scoped>
.interview-library-shell { display: flex; min-height: 0; flex: 1; flex-direction: column; gap: 16px; color: #263425; }
.library-header { display: flex; align-items: end; justify-content: space-between; gap: 24px; padding: 2px 2px 0; }
.library-kicker,.section-label { margin: 0; color: #88a143; font-size: 10px; font-weight: 800; letter-spacing: .12em; }
.library-description { margin: 0; color: #7c8b75; font-size: 14px; line-height: 1.55; }
.library-actions,.experience-actions { display: flex; align-items: center; gap: 8px; }
.primary-action,.quiet-action,.tree-refresh,.close-button { border: 1px solid #dfe8d2; border-radius: 10px; cursor: pointer; font: inherit; font-weight: 750; transition: transform .16s ease, box-shadow .16s ease, background .16s ease; }
.primary-action { border-color: #8eae37; background: #91b236; color: white; box-shadow: 0 8px 16px rgba(112, 139, 37, .18); padding: 10px 15px; }
.quiet-action { background: #fff; color: #5d713b; padding: 9px 13px; }
.primary-action:hover:not(:disabled),.quiet-action:hover:not(:disabled),.tree-refresh:hover:not(:disabled) { transform: translateY(-1px); box-shadow: 0 8px 16px rgba(73, 94, 38, .1); }
button:focus-visible,input:focus-visible,textarea:focus-visible { outline: 3px solid rgba(116, 155, 48, .24); outline-offset: 2px; }
button:disabled { cursor: wait; opacity: .62; }
.library-layout { display: grid; min-height: 0; flex: 1; grid-template-columns: minmax(250px, 295px) minmax(0, 1fr); gap: 14px; }
.interview-tree-pane,.interview-detail-pane { min-height: 0; border: 1px solid #e3eade; border-radius: 18px; background: #fff; box-shadow: 0 14px 32px rgba(69, 87, 42, .06); }
.interview-tree-pane { display: flex; flex-direction: column; overflow: hidden; padding: 17px 14px 14px; }
.tree-pane-header { display: flex; align-items: center; justify-content: space-between; padding: 0 4px 12px; }
.tree-pane-header strong { font-size: 17px; }
.tree-refresh { width: 30px; height: 30px; background: #f5f8ee; color: #668438; font-size: 18px; line-height: 1; }
.tree-search { display: flex; align-items: center; gap: 8px; border: 1px solid #e3e9d7; border-radius: 11px; background: #fbfcf8; padding: 0 10px; color: #8b9982; }
.tree-search input { width: 100%; border: 0; outline: 0; background: transparent; color: #30422b; padding: 10px 0; font: inherit; font-size: 13px; }
.company-tree { min-height: 0; margin-top: 13px; overflow: auto; padding: 0 1px 8px; }
.company-tree details { border-bottom: 1px solid #f0f3ed; padding: 7px 0; }
.company-tree summary { display: flex; align-items: center; gap: 8px; cursor: pointer; list-style: none; color: #344432; font-size: 14px; font-weight: 800; }
.company-tree summary::-webkit-details-marker { display: none; }
.tree-company-mark { color: #7aa33f; font-size: 16px; }
.company-tree summary em { margin-left: auto; border-radius: 999px; background: #eef5df; color: #719036; padding: 2px 7px; font-size: 11px; font-style: normal; }
.tree-leaves { display: grid; gap: 3px; margin: 5px 0 2px 12px; border-left: 1px solid #e1e8d9; padding-left: 8px; }
.tree-leaf { display: grid; grid-template-columns: 8px minmax(0, 1fr) 7px; align-items: center; gap: 6px; border: 0; border-radius: 9px; background: transparent; color: #536150; cursor: pointer; padding: 8px 6px; text-align: left; }
.tree-leaf:hover { background: #f7faef; }.tree-leaf.active { background: #edf6d8; color: #39541d; }
.leaf-line { height: 1px; background: #d4dfc9; }.leaf-copy { min-width: 0; }.leaf-copy strong,.leaf-copy small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.leaf-copy strong { font-size: 12px; }.leaf-copy small { margin-top: 3px; color: #929d8c; font-size: 10px; }
.status-dot { width: 7px; height: 7px; border-radius: 99px; background: #b7c0b2; }.status-indexed { background: #85ad35; }.status-failed { background: #d16f6f; }.status-parsing,.status-indexing { background: #d69b36; }
.tree-placeholder { display: grid; gap: 7px; margin: auto 8px; color: #8a9684; font-size: 13px; line-height: 1.7; }.tree-placeholder strong { color: #52624e; }.inline-error { margin: 12px 4px 0; color: #bd5f5f; font-size: 12px; }
.interview-detail-pane { display: flex; min-width: 0; flex-direction: column; overflow: hidden; }
.detail-state { display: grid; place-content: center; justify-items: center; min-height: 360px; flex: 1; padding: 42px; color: #7e8d78; text-align: center; }.detail-state strong { margin: 12px 0 0; color: #3c5037; font-size: 17px; }.detail-state p { max-width: 360px; line-height: 1.7; }.error-state strong,.error-state p { color: #a84e4e; }
.thinking-orbit { width: 34px; height: 34px; border: 3px solid #e3ebd8; border-top-color: #88ab36; border-radius: 50%; animation: orbit .75s linear infinite; }@keyframes orbit { to { transform: rotate(360deg); } }
.library-empty-state { min-height: 420px; }.empty-index-mark { display: grid; width: 54px; height: 54px; place-items: center; border-radius: 18px 18px 18px 4px; background: #ebf4d4; color: #6e9832; font-size: 28px; font-weight: 800; }.library-empty-state h3 { margin: 9px 0 0; color: #334830; font-size: 24px; }.library-empty-state .primary-action { margin-top: 10px; }
.experience-header { display: flex; align-items: start; justify-content: space-between; gap: 22px; border-bottom: 1px solid #edf1e9; padding: 21px 24px 16px; }.experience-header h3 { margin: 4px 0 0; color: #273923; font-size: 23px; letter-spacing: -.035em; }.experience-meta { display: flex; flex-wrap: wrap; gap: 7px 12px; margin-top: 8px; color: #899588; font-size: 12px; }.experience-meta a { color: #668d38; font-weight: 700; text-decoration: none; }
.index-status { border-radius: 999px; background: #eef6df; color: #5f8730; padding: 5px 9px; font-size: 12px; font-weight: 800; }.index-failed { background: #fff0ef; color: #b65757; }.index-parsing,.index-indexing { background: #fff5df; color: #a26d1f; }
.evidence-strip { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)) minmax(160px, 1.5fr); gap: 12px; border-bottom: 1px solid #eef2e9; background: #fbfcf8; padding: 11px 24px; }.evidence-strip > div { min-width: 0; }.evidence-strip span,.evidence-strip em { display: block; color: #99a492; font-size: 10px; font-style: normal; }.evidence-strip strong { display: block; overflow: hidden; margin-top: 3px; color: #53634e; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }.tag-list { display: flex; flex-wrap: wrap; align-content: center; gap: 4px; }.tag-list span { border-radius: 5px; background: #edf4df; color: #6e8b37; padding: 3px 6px; }
.reading-layout,.editor-layout { min-height: 0; flex: 1; overflow: auto; padding: 30px 32px 38px; }.summary-note { max-width: 900px; margin: 0 auto 20px; border: 1px solid #dce9ca; border-left: 4px solid #89ad39; border-radius: 0 13px 13px 0; background: linear-gradient(90deg, #f4f9e9, #fbfdf8); padding: 15px 19px; }.summary-note p { margin: 0 0 5px; color: #6f9133; font-size: 11px; font-weight: 850; letter-spacing: .08em; }.summary-note strong { color: #385134; font-size: 15px; line-height: 1.75; }.markdown-reading { max-width: 900px; margin: 0 auto; border: 1px solid #edf1e8; border-radius: 16px; background: #fff; box-shadow: 0 12px 32px rgba(55, 72, 49, .05); color: #344333; padding: 32px clamp(22px, 4vw, 54px) 42px; font-family: "PingFang SC", "Microsoft YaHei", sans-serif; font-size: 16px; line-height: 1.95; word-break: break-word; }.reading-heading { margin: 34px 0 13px; color: #263a25; font-weight: 800; letter-spacing: -.02em; line-height: 1.35; }.reading-heading:first-child { margin-top: 0; }.markdown-reading h2 { border-bottom: 1px solid #e7eddc; padding-bottom: 9px; font-size: 22px; }.markdown-reading h3 { border-left: 3px solid #8eaf3b; padding-left: 11px; font-size: 18px; }.markdown-reading h4 { color: #54703d; font-size: 16px; }.reading-paragraph { margin: 0 0 17px; color: #40503e; }.reading-list { margin: 7px 0 19px; padding-left: 1.5em; color: #40503e; }.reading-list li { margin: 7px 0; padding-left: 4px; }.reading-ordered li::marker { color: #769b39; font-weight: 800; }.reading-divider { height: 1px; margin: 29px 0; border: 0; background: linear-gradient(90deg, #cddfba, #edf3e5 70%, transparent); }
.editor-layout { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, .8fr); gap: 14px; }.editor-field { display: grid; gap: 6px; color: #4d6049; font-size: 12px; font-weight: 800; }.editor-field input,.editor-field textarea,.import-form input,.import-form textarea { width: 100%; box-sizing: border-box; border: 1px solid #dce6d6; border-radius: 9px; background: #fff; color: #30422d; font: inherit; font-size: 13px; }.editor-field input,.import-form input { padding: 10px 11px; }.markdown-field { grid-column: 1 / -1; }.markdown-field textarea { min-height: 460px; resize: vertical; padding: 14px; line-height: 1.75; }
.import-backdrop { position: fixed; z-index: 30; inset: 0; display: grid; place-items: center; background: rgba(38, 51, 32, .34); padding: 20px; }.import-dialog { position: relative; width: min(860px, 100%); max-height: min(860px, calc(100vh - 40px)); overflow: auto; border: 1px solid #e1e8d6; border-radius: 20px; background: #fff; box-shadow: 0 30px 90px rgba(21, 33, 17, .24); }.import-dialog > header { display: flex; align-items: start; justify-content: space-between; gap: 20px; border-bottom: 1px solid #edf1e8; padding: 24px 26px 18px; }.import-dialog h2 { margin: 5px 0 0; font-size: 25px; }.import-dialog header p:not(.library-kicker) { max-width: 620px; margin: 7px 0 0; color: #81907b; font-size: 13px; line-height: 1.6; }.close-button { width: 34px; height: 34px; flex: none; background: #fafbf7; color: #728067; font-size: 26px; font-weight: 400; line-height: 1; }.file-parse-overlay { position: absolute; z-index: 4; inset: 0; display: grid; place-items: center; background: rgba(249, 252, 245, .78); backdrop-filter: blur(3px); }.file-parse-progress-card { width: min(390px, calc(100% - 40px)); border: 1px solid #d7e6bf; border-radius: 16px; background: rgba(255, 255, 255, .96); box-shadow: 0 18px 48px rgba(48, 71, 33, .16); padding: 22px; }.progress-heading { display: flex; align-items: center; justify-content: space-between; color: #3f5635; font-size: 15px; font-weight: 800; }.progress-heading strong { color: #73953a; font-size: 20px; }.progress-track { height: 9px; overflow: hidden; margin: 16px 0 14px; border-radius: 999px; background: #e9f0df; }.progress-track i { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, #8eb33e, #b9d768); box-shadow: 0 0 14px rgba(131, 167, 52, .42); transition: width .35s ease; }.progress-phase { color: #40523a; font-size: 14px; }.file-parse-progress-card p { margin: 6px 0 0; color: #7c8974; font-size: 12px; line-height: 1.65; }
.import-tabs { display: flex; gap: 6px; border-bottom: 1px solid #edf1e8; padding: 12px 26px 0; }.import-tabs button { border: 0; border-bottom: 2px solid transparent; background: transparent; color: #899486; cursor: pointer; padding: 8px 12px 10px; font: inherit; font-size: 13px; font-weight: 800; }.import-tabs button.active { border-bottom-color: #87a93a; color: #55752b; }.import-form { display: grid; gap: 12px; padding: 20px 26px 24px; }.import-form label { display: grid; gap: 6px; color: #4a5a47; font-size: 12px; font-weight: 800; }.import-form textarea { min-height: 220px; resize: vertical; padding: 12px; line-height: 1.7; }.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }.file-intake-intro { border-left: 3px solid #8bae39; border-radius: 0 9px 9px 0; background: #f6faed; padding: 11px 13px; }.file-intake-intro strong { color: #4c6a29; font-size: 13px; }.file-intake-intro p { margin: 4px 0 0; color: #718064; font-size: 12px; line-height: 1.6; }.file-picker { min-height: 68px; place-content: center; border: 1px dashed #b9cba2; border-radius: 10px; background: #f8fbf3; padding: 8px 12px; cursor: pointer; }.file-picker input { position: absolute; width: 1px; height: 1px; opacity: 0; }.file-picker span { color: #6b833e; font-size: 12px; }.file-import-strategy { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 14px; margin: 0; border: 1px solid #e2ead8; border-radius: 11px; background: #fcfdf9; padding: 11px 13px; }.file-import-strategy legend { padding: 0 5px; color: #4f6541; font-size: 12px; font-weight: 850; }.file-import-strategy label { display: flex; align-items: center; gap: 7px; color: #52634d; cursor: pointer; font-size: 12px; font-weight: 750; }.file-import-strategy input { width: auto; accent-color: #89ab38; }.file-import-strategy p { grid-column: 1 / -1; margin: 2px 0 0; color: #7a8972; font-size: 12px; line-height: 1.55; }.recognition-card { display: grid; gap: 5px; border: 1px solid #dcebc6; border-radius: 11px; background: #fbfdf7; padding: 12px 13px; }.recognition-card > div { display: flex; align-items: center; justify-content: space-between; gap: 10px; }.recognition-card strong { color: #48642b; font-size: 13px; }.recognition-card span { border-radius: 999px; background: #eaf4d7; color: #648331; padding: 4px 8px; font-size: 11px; font-weight: 800; }.recognition-card p { margin: 0; color: #71806b; font-size: 12px; line-height: 1.55; }.recognition-card .recognition-warning { color: #a37237; }.import-form footer { display: flex; justify-content: end; gap: 8px; margin-top: 4px; }.dialog-error,.dialog-success { margin: 14px 26px 0; border-radius: 9px; padding: 10px 12px; font-size: 13px; }.dialog-error { background: #fff1ef; color: #ab5252; }.dialog-success { background: #eef7df; color: #5f8830; }
.collection-backdrop { position: fixed; z-index: 31; inset: 0; display: grid; place-items: center; background: rgba(31, 43, 27, .43); padding: 20px; }.collection-dialog { width: min(960px, 100%); max-height: min(850px, calc(100vh - 40px)); overflow: auto; border: 1px solid #dfe8d2; border-radius: 20px; background: #fff; box-shadow: 0 30px 90px rgba(21, 33, 17, .28); }.collection-dialog-header { display: flex; align-items: start; justify-content: space-between; gap: 20px; border-bottom: 1px solid #edf1e8; padding: 24px 26px 18px; }.collection-dialog h2 { margin: 5px 0 0; font-size: 25px; }.collection-dialog-header p:not(.library-kicker) { max-width: 690px; margin: 7px 0 0; color: #71806c; font-size: 13px; line-height: 1.65; }.collection-tabs { background: #fbfcf8; }.collection-form { display: grid; gap: 16px; padding: 22px 26px 26px; }.collection-section { border: 1px solid #e6eddf; border-radius: 14px; background: #fbfcf9; padding: 16px; }.collection-section-heading { display: flex; align-items: start; justify-content: space-between; gap: 16px; margin-bottom: 14px; }.collection-section h3,.candidate-card h3 { margin: 0; color: #314230; font-size: 16px; }.collection-section-heading p { max-width: 640px; margin: 5px 0 0; color: #7e8d77; font-size: 12px; line-height: 1.6; }.collection-loading { flex: none; border-radius: 999px; background: #edf4df; color: #6d8937; padding: 5px 9px; font-size: 11px; font-weight: 800; }.platform-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 9px; }.platform-card { display: grid; gap: 4px; min-height: 78px; border: 1px solid #dfe8d5; border-radius: 11px; background: #fff; color: #405040; cursor: pointer; padding: 12px; text-align: left; transition: border-color .16s ease, background .16s ease, transform .16s ease; }.platform-card strong { font-size: 13px; }.platform-card span { color: #8b9786; font-size: 11px; }.platform-card:hover,.platform-card.active { border-color: #91b236; background: #f2f8e5; }.platform-card:hover { transform: translateY(-1px); }.collection-field { display: grid; gap: 6px; color: #4a5a47; font-size: 12px; font-weight: 800; }.collection-field input { width: 100%; box-sizing: border-box; border: 1px solid #dce6d6; border-radius: 9px; background: #fff; color: #30422d; padding: 10px 11px; font: inherit; font-size: 13px; }.compact-field { max-width: 220px; }.collection-form footer { display: flex; justify-content: end; gap: 8px; }.collection-job-card { display: grid; gap: 7px; border: 1px solid #dcebc6; border-radius: 12px; background: #f6faee; padding: 14px 16px; }.collection-job-card > div { display: flex; align-items: center; justify-content: space-between; gap: 14px; }.collection-job-card span { color: #7d8d6b; font-size: 11px; }.collection-job-card strong { color: #55752b; font-size: 13px; }.collection-job-card p { margin: 0; color: #5d6d56; font-size: 12px; line-height: 1.6; }.candidate-card { display: grid; gap: 14px; border: 1px solid #dce8cb; border-radius: 14px; background: #fcfdf9; padding: 17px; }.candidate-card > header { display: flex; align-items: start; justify-content: space-between; gap: 14px; }.candidate-card header > div { display: grid; gap: 5px; }.candidate-card header > span { flex: none; border-radius: 999px; background: #edf4df; color: #688436; padding: 5px 8px; font-size: 11px; font-weight: 800; }.candidate-card a { color: #638331; font-size: 12px; font-weight: 750; text-decoration: none; }.candidate-excerpt { margin: 0; color: #596856; font-size: 13px; line-height: 1.7; }.candidate-markdown { border-radius: 10px; background: #f6f8f3; color: #586656; }.candidate-markdown summary { cursor: pointer; padding: 10px 12px; font-size: 12px; font-weight: 800; }.candidate-markdown pre { max-height: 240px; overflow: auto; margin: 0; border-top: 1px solid #e7ece0; padding: 12px; color: #52604f; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 11px; line-height: 1.65; white-space: pre-wrap; word-break: break-word; }.candidate-meta-grid { margin-top: 2px; }.candidate-meta-grid label { display: grid; gap: 6px; color: #4a5a47; font-size: 12px; font-weight: 800; }.candidate-meta-grid input { width: 100%; box-sizing: border-box; border: 1px solid #dce6d6; border-radius: 9px; background: #fff; color: #30422d; padding: 10px 11px; font: inherit; font-size: 13px; }
@media (max-width: 900px) { .library-layout { grid-template-columns: 1fr; }.interview-tree-pane { max-height: 290px; }.evidence-strip { grid-template-columns: 1fr 1fr; }.library-header,.experience-header { align-items: start; flex-direction: column; }.experience-actions { width: 100%; justify-content: space-between; }.library-title-row { align-items: start; flex-direction: column; gap: 3px; }.editor-layout,.form-grid,.platform-grid { grid-template-columns: 1fr; }.collection-dialog { max-height: calc(100vh - 24px); }.collection-backdrop { padding: 12px; }.collection-dialog-header,.collection-form { padding-right: 18px; padding-left: 18px; }.collection-tabs { padding-right: 18px; padding-left: 18px; } }
@media (prefers-reduced-motion: reduce) { .thinking-orbit { animation: none; } }
</style>
