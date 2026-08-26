<script setup>
import { computed, onMounted, ref } from 'vue'
import { CAREER_MEMORY_LABELS, CAREER_MEMORY_TYPES, groupCareerMemories, memorySourceLabel } from '../career-memory-view.js'

const emit = defineEmits(['navigate'])
const spaces = ref([])
const selectedSpaceId = ref('')
const memories = ref([])
const loading = ref(false)
const error = ref('')
const grouped = computed(() => groupCareerMemories(memories.value))
const SPACE_STORAGE_KEY = 'career-selected-space-id'

async function requestJson(url, options = {}) {
  const response = await fetch(url, options)
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload.detail?.message || payload.detail || `请求失败：${response.status}`)
  return payload
}

async function loadSpaces() {
  const payload = await requestJson('/api/career/career-spaces')
  spaces.value = payload.items || []
  selectedSpaceId.value ||= spaces.value.find((item) => item.is_default)?.id || spaces.value[0]?.id || ''
  const saved = window.localStorage.getItem(SPACE_STORAGE_KEY)
  if (spaces.value.some((item) => item.id === saved)) selectedSpaceId.value = saved
}

async function loadMemories() {
  if (!selectedSpaceId.value) return
  window.localStorage.setItem(SPACE_STORAGE_KEY, selectedSpaceId.value)
  loading.value = true
  error.value = ''
  try {
    const payload = await requestJson(`/api/career/memories?career_space_id=${encodeURIComponent(selectedSpaceId.value)}`)
    memories.value = payload.items || []
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '读取求职记忆失败'
  } finally {
    loading.value = false
  }
}

async function operate(item, action) {
  if (action === 'delete' && !window.confirm('永久删除这条求职记忆？删除后不会再用于回答。')) return
  let url = `/api/career/memories/${item.id}`
  const options = { method: action === 'delete' ? 'DELETE' : 'POST' }
  if (action === 'confirm') url += '/confirm'
  if (action === 'disable') url += '/disable'
  if (action === 'correct') {
    const text = window.prompt('请输入修正后的事实', item.display_text)?.trim()
    if (!text) return
    options.method = 'PATCH'
    options.headers = { 'Content-Type': 'application/json' }
    const normalizedKey = item.memory_type === 'job_intention' ? 'statement' : 'summary'
    options.body = JSON.stringify({ display_text: text, normalized_value: { [normalizedKey]: text } })
  }
  try {
    await requestJson(url, options)
    await loadMemories()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '操作失败'
  }
}

onMounted(async () => {
  try { await loadSpaces(); await loadMemories() } catch (reason) { error.value = reason.message }
})
</script>

<template>
  <main class="career-memory-page">
    <header class="memory-page-header">
      <div><button type="button" class="back-button" @click="emit('navigate', '/career')">← 返回求职助手</button><h1>我的求职记忆</h1><p>只保存岗位意向和可用于求职的个人优势事实，你可以随时确认、修正、停用或删除。</p></div>
      <label>职业空间<select v-model="selectedSpaceId" @change="loadMemories"><option v-for="space in spaces" :key="space.id" :value="space.id">{{ space.name }}</option></select></label>
    </header>
    <p v-if="error" class="memory-error">{{ error }}</p>
    <p v-if="loading" class="memory-empty">正在读取…</p>
    <section v-else class="memory-groups">
      <article v-for="type in CAREER_MEMORY_TYPES" :key="type" class="memory-group">
        <header><h2>{{ CAREER_MEMORY_LABELS[type] }}</h2><span>{{ grouped[type].active.length + grouped[type].candidate.length }}</span></header>
        <p v-if="!grouped[type].active.length && !grouped[type].candidate.length" class="memory-empty">暂无有效信息</p>
        <div v-for="item in [...grouped[type].candidate, ...grouped[type].active, ...grouped[type].disabled]" :key="item.id" class="memory-card">
          <p>{{ item.display_text }}</p><small>{{ memorySourceLabel(item) }} · {{ item.status === 'candidate' ? '待确认' : (item.status === 'disabled' ? '已停用' : '已启用') }}</small>
          <div><button v-if="item.status === 'candidate'" type="button" @click="operate(item, 'confirm')">确认</button><button v-if="item.status !== 'disabled'" type="button" @click="operate(item, 'correct')">修正</button><button v-if="item.status === 'active'" type="button" @click="operate(item, 'disable')">停用</button><button type="button" class="danger" @click="operate(item, 'delete')">删除</button></div>
        </div>
      </article>
    </section>
  </main>
</template>

<style scoped>
.career-memory-page{min-height:100%;background:#f5f7f2;padding:30px 34px;color:#28352b}.memory-page-header{display:flex;justify-content:space-between;gap:24px;align-items:flex-end;max-width:1200px;margin:auto}.memory-page-header h1{margin:12px 0 6px;font-size:30px}.memory-page-header p{margin:0;color:#73806e}.back-button{border:0;background:transparent;color:#5f7d35;padding:0;font-weight:700}.memory-page-header label{display:grid;gap:6px;font-size:12px;font-weight:800}.memory-page-header select{min-width:220px;border:1px solid #dce5d6;border-radius:10px;background:#fff;padding:10px}.memory-groups{max-width:1200px;margin:24px auto;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.memory-group{background:#fff;border:1px solid #e2e9de;border-radius:16px;padding:18px}.memory-group>header{display:flex;justify-content:space-between}.memory-group h2{margin:0;font-size:18px}.memory-group>header span{color:#7e906f}.memory-card{margin-top:12px;border-top:1px solid #edf0e9;padding-top:12px}.memory-card p{margin:0 0 6px;line-height:1.65}.memory-card small{color:#83907f}.memory-card div{display:flex;gap:7px;margin-top:10px}.memory-card button{border:1px solid #dce6d5;border-radius:8px;background:#fff;padding:6px 10px}.memory-card button.danger,.memory-error{color:#a34b43}.memory-empty{color:#929d8f}
@media(max-width:900px){.memory-groups{grid-template-columns:1fr}.memory-page-header{align-items:flex-start;flex-direction:column}}
</style>
