<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'

const props = defineProps({
  currentRoute: {
    type: String,
    default: '/admin/modules'
  }
})

const emit = defineEmits(['navigation-config-updated', 'navigate'])

const adminSections = [
  {
    path: '/admin/modules',
    label: '可见模块',
    description: '控制普通用户登录后可以看到和直接访问的顶级模块。'
  },
  {
    path: '/admin/github',
    label: 'GitHub 热门',
    description: '管理每周热门项目的选题范围、数量与数据快照。'
  },
  {
    path: '/admin/prompts',
    label: '生成策略',
    description: '管理文章、图片和视频任务使用的提示词。'
  }
]

const activeSection = computed(() => adminSections.find((item) => item.path === props.currentRoute) ?? adminSections[0])

const loading = ref(true)
const saving = ref(false)
const errorMessage = ref('')
const successMessage = ref('')
const version = ref(null)
const githubSnapshot = ref(null)
const snapshotLoading = ref(true)
const snapshotRefreshing = ref(false)
const snapshotError = ref('')
const routeModules = ref([])
const routeConfigLoading = ref(true)
const routeConfigSaving = ref(false)
const routeConfigError = ref('')
const routeConfigSuccess = ref('')
const enabledRouteModuleCount = computed(() => routeModules.value.filter((item) => item.enabled).length)

const form = reactive({
  top_n: 5,
  github_keywords: 'agent, AI, LLM, RAG',
  summary_prompt: '',
  image_prompt: '',
  video_prompt: ''
})

const sectionStatus = computed(() => {
  if (activeSection.value.path === '/admin/modules') {
    return `${enabledRouteModuleCount.value}/${routeModules.value.length || 9} 已启用`
  }
  return version.value ? `当前版本 v${version.value}` : '尚未保存版本'
})

onMounted(loadActiveSection)

watch(() => props.currentRoute, loadActiveSection)

function loadActiveSection() {
  errorMessage.value = ''
  successMessage.value = ''
  routeConfigError.value = ''
  routeConfigSuccess.value = ''
  if (activeSection.value.path === '/admin/modules') {
    void loadRouteModules()
    return
  }
  void loadConfig()
  if (activeSection.value.path === '/admin/github') void loadGithubSnapshot()
}

async function loadRouteModules() {
  routeConfigLoading.value = true
  routeConfigError.value = ''
  try {
    const response = await fetch('/api/navigation/modules', { credentials: 'include', cache: 'no-store' })
    if (!response.ok) throw new Error(await responseError(response, '无法读取路由模块配置'))
    routeModules.value = (await response.json()).items ?? []
  } catch (error) {
    routeConfigError.value = error instanceof Error ? error.message : '无法读取路由模块配置'
  } finally {
    routeConfigLoading.value = false
  }
}

async function saveRouteModules() {
  if (routeConfigSaving.value) return
  routeConfigSaving.value = true
  routeConfigError.value = ''
  routeConfigSuccess.value = ''
  try {
    const response = await fetch('/api/admin/navigation-modules', {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        modules: Object.fromEntries(routeModules.value.map((item) => [item.key, Boolean(item.enabled)]))
      })
    })
    if (!response.ok) throw new Error(await responseError(response, '保存路由模块失败'))
    const items = (await response.json()).items ?? []
    routeModules.value = items
    routeConfigSuccess.value = `路由模块已更新，当前启用 ${enabledRouteModuleCount.value} 个。`
    emit('navigation-config-updated', items)
  } catch (error) {
    routeConfigError.value = error instanceof Error ? error.message : '保存路由模块失败'
  } finally {
    routeConfigSaving.value = false
  }
}

async function loadGithubSnapshot() {
  snapshotLoading.value = true
  snapshotError.value = ''
  try {
    const response = await fetch('/api/admin/github-snapshot', { credentials: 'include', cache: 'no-store' })
    if (!response.ok) throw new Error(await responseError(response, '无法读取 GitHub 热门项目快照'))
    githubSnapshot.value = (await response.json()).item ?? null
  } catch (error) {
    snapshotError.value = error instanceof Error ? error.message : '无法读取 GitHub 热门项目快照'
  } finally {
    snapshotLoading.value = false
  }
}

async function refreshGithubSnapshot() {
  if (snapshotRefreshing.value) return
  snapshotRefreshing.value = true
  snapshotError.value = ''
  successMessage.value = ''
  try {
    const response = await fetch('/api/admin/github-snapshot/refresh', {
      method: 'POST',
      credentials: 'include'
    })
    if (!response.ok) throw new Error(await responseError(response, '刷新 GitHub 热门项目失败'))
    const payload = await response.json()
    githubSnapshot.value = payload.item ?? null
    successMessage.value = `GitHub 热门项目快照已刷新，共 ${githubSnapshot.value?.project_count ?? 0} 个项目。`
  } catch (error) {
    snapshotError.value = error instanceof Error ? error.message : '刷新 GitHub 热门项目失败'
  } finally {
    snapshotRefreshing.value = false
  }
}

function snapshotTime(value) {
  if (!value) return '暂无更新时间'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString('zh-CN', { hour12: false })
}

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
        <h2>{{ activeSection.label }}</h2>
        <p>{{ activeSection.description }}</p>
      </div>
      <div class="admin-config-version">{{ sectionStatus }}</div>
    </header>

    <nav class="admin-console-subnav" aria-label="管理台子页面">
      <button
        v-for="item in adminSections"
        :key="item.path"
        type="button"
        :class="{ active: item.path === activeSection.path }"
        :aria-current="item.path === activeSection.path ? 'page' : undefined"
        @click="emit('navigate', item.path)"
      >
        {{ item.label }}
      </button>
    </nav>

    <section v-if="activeSection.path === '/admin/modules'" class="admin-config-card admin-route-module-card">
      <div class="admin-card-heading">
        <div>
          <h3>用户可见模块</h3>
          <p>模块开关只影响普通用户；平台管理员始终可以访问全部页面。</p>
        </div>
      </div>

      <p v-if="routeConfigError" class="admin-console-alert danger">{{ routeConfigError }}</p>
      <p v-if="routeConfigSuccess" class="admin-console-alert success">{{ routeConfigSuccess }}</p>
      <div v-if="routeConfigLoading" class="admin-route-state">正在读取路由模块…</div>
      <div v-else class="admin-route-list" role="list" aria-label="顶级路由模块配置">
        <article v-for="item in routeModules" :key="item.key" class="admin-route-item" role="listitem">
          <div class="admin-route-copy">
            <div>
              <strong>{{ item.label }}</strong>
              <span v-if="item.admin_only">仅管理员</span>
              <span v-if="item.locked" class="locked">固定开启</span>
            </div>
            <p>{{ item.description }}</p>
            <code>{{ item.path }}</code>
          </div>
          <label class="admin-route-switch" :class="{ disabled: item.locked }">
            <input v-model="item.enabled" type="checkbox" :disabled="item.locked" :aria-label="`${item.label}模块`" />
            <span aria-hidden="true"></span>
            <em>{{ item.enabled ? '已启用' : '已隐藏' }}</em>
          </label>
        </article>
      </div>

      <footer class="admin-route-footer">
        <p>保存后，普通用户的导航和直接地址访问会同步更新，不影响管理员当前访问。</p>
        <button class="refresh-button" type="button" :disabled="routeConfigLoading || routeConfigSaving" @click="saveRouteModules">
          {{ routeConfigSaving ? '正在保存…' : '保存模块配置' }}
        </button>
      </footer>
    </section>

    <section v-else-if="loading" class="admin-console-state">正在读取工作流配置…</section>

    <form v-else-if="activeSection.path === '/admin/github'" class="admin-config-form" @submit.prevent="saveConfig">
      <section v-if="errorMessage" class="admin-console-alert danger">{{ errorMessage }}</section>
      <section v-if="successMessage" class="admin-console-alert success">{{ successMessage }}</section>

      <section class="admin-config-card">
        <div class="admin-card-heading">
          <div>
            <h3>热门项目筛选</h3>
            <p>配置选题范围后可手动刷新本周快照。</p>
          </div>
        </div>
        <div class="github-snapshot-bar">
          <div class="github-snapshot-meta">
            <strong>当前项目快照</strong>
            <span v-if="snapshotLoading">正在读取…</span>
            <template v-else-if="githubSnapshot">
              <span>{{ githubSnapshot.week_start }} 至 {{ githubSnapshot.week_end }}</span>
              <span>{{ githubSnapshot.project_count }} 个项目</span>
              <span>更新于 {{ snapshotTime(githubSnapshot.updated_at) }}</span>
            </template>
            <span v-else>尚无快照，请先刷新 GitHub 热门项目。</span>
          </div>
          <button class="secondary-button" type="button" :disabled="snapshotRefreshing" @click="refreshGithubSnapshot">
            {{ snapshotRefreshing ? '正在刷新…' : '刷新 GitHub 热门项目' }}
          </button>
        </div>
        <p v-if="snapshotError" class="github-snapshot-error">{{ snapshotError }}</p>
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

      <footer class="admin-config-footer">
        <p>保存仅影响后续任务，当前运行和历史内容不会被改写。</p>
        <button class="refresh-button" type="submit" :disabled="saving">
          {{ saving ? '正在保存…' : '保存为新版本' }}
        </button>
      </footer>
    </form>

    <form v-else class="admin-config-form" @submit.prevent="saveConfig">
      <section v-if="errorMessage" class="admin-console-alert danger">{{ errorMessage }}</section>
      <section v-if="successMessage" class="admin-console-alert success">{{ successMessage }}</section>

      <section class="admin-config-card">
        <div class="admin-card-heading">
          <div>
            <h3>任务提示词</h3>
            <p>留空时继续使用系统默认模板。</p>
          </div>
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
        <p>保存仅影响后续任务，当前运行和历史内容不会被改写。</p>
        <button class="refresh-button" type="submit" :disabled="saving">
          {{ saving ? '正在保存…' : '保存为新版本' }}
        </button>
      </footer>
    </form>
  </section>
</template>
