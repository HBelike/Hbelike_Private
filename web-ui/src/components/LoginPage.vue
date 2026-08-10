<script setup>
import { computed, onMounted, ref } from 'vue'

const emit = defineEmits(['authenticated'])

const loading = ref(true)
const submitting = ref(false)
const requiresBootstrap = ref(false)
const publicRegistrationEnabled = ref(true)
const cliBootstrapOnly = ref(false)
const mode = ref('login')
const verificationStage = ref(false)
const challengeId = ref('')
const identity = ref('')
const email = ref('')
const displayName = ref('')
const password = ref('')
const confirmPassword = ref('')
const verificationCode = ref('')
const errorMessage = ref('')
const successMessage = ref('')
const requiresCliBootstrap = computed(() => requiresBootstrap.value && cliBootstrapOnly.value)

const panelTitle = computed(() => {
  if (requiresCliBootstrap.value) return '请先在服务器初始化管理员'
  if (requiresBootstrap.value) return '创建管理员账户'
  if (mode.value === 'register') return verificationStage.value ? '验证邮箱并创建账户' : '创建平台账户'
  if (mode.value === 'reset') return verificationStage.value ? '设置新密码' : '找回登录密码'
  return '登录职业工作台'
})

const panelDescription = computed(() => {
  if (requiresCliBootstrap.value) return '为了避免公网首个账户被抢先注册，管理员初始化只能由服务器拥有者在交互式终端完成。完成后刷新此页即可登录。'
  if (requiresBootstrap.value) return '首个账户将获得管理员权限。使用邮箱完成验证后，即可配置成员、角色与平台能力。'
  if (mode.value === 'register') return '注册后默认获得基础访问权限。高级管理功能需由管理员单独授权。'
  if (mode.value === 'reset') return '验证码仅发送到已验证邮箱，用于确认本次密码重置操作。'
  return '使用邮箱和密码继续。历史账户也可暂时使用原用户名登录，并在进入平台后绑定邮箱。'
})

const primaryLabel = computed(() => {
  if (requiresBootstrap.value) return verificationStage.value ? '验证并进入平台' : '发送管理员验证码'
  if (mode.value === 'register') return verificationStage.value ? '验证并创建账户' : '发送注册验证码'
  if (mode.value === 'reset') return verificationStage.value ? '保存新密码' : '发送重置验证码'
  return '登录并进入平台'
})

const showAccountTabs = computed(() => !requiresBootstrap.value && !verificationStage.value && publicRegistrationEnabled.value)

onMounted(loadBootstrapStatus)

async function loadBootstrapStatus() {
  loading.value = true
  clearMessages()
  try {
    const response = await fetch('/api/auth/bootstrap-status', {
      credentials: 'include',
      cache: 'no-store'
    })
    if (!response.ok) throw new Error(await responseError(response, '无法读取账户服务状态'))
    const payload = await response.json()
    requiresBootstrap.value = Boolean(payload.requires_bootstrap)
    publicRegistrationEnabled.value = payload.public_registration_enabled !== false
    cliBootstrapOnly.value = Boolean(payload.cli_bootstrap_only)
    if (requiresBootstrap.value) mode.value = 'register'
    if (!requiresBootstrap.value && !publicRegistrationEnabled.value && mode.value === 'register') {
      mode.value = 'login'
    }
  } catch (error) {
    errorMessage.value = readableError(error, '账户服务暂不可用。请确认后端服务和 PostgreSQL 已启动。')
  } finally {
    loading.value = false
  }
}

function switchMode(nextMode) {
  mode.value = nextMode
  verificationStage.value = false
  challengeId.value = ''
  verificationCode.value = ''
  password.value = ''
  confirmPassword.value = ''
  clearMessages()
}

function clearMessages() {
  errorMessage.value = ''
  successMessage.value = ''
}

function validatePassword() {
  if (password.value.length < 8) {
    errorMessage.value = '密码至少需要 8 位。'
    return false
  }
  if (mode.value !== 'login' && password.value !== confirmPassword.value) {
    errorMessage.value = '两次输入的密码不一致。'
    return false
  }
  return true
}

async function submit() {
  if (submitting.value) return
  clearMessages()

  if (requiresBootstrap.value || mode.value === 'register') {
    await submitRegistration()
    return
  }
  if (mode.value === 'reset') {
    await submitReset()
    return
  }
  await submitLogin()
}

async function submitRegistration() {
  if (!email.value.trim() || !displayName.value.trim()) {
    errorMessage.value = '请填写邮箱和显示名称。'
    return
  }
  if (!verificationStage.value && !validatePassword()) return
  if (verificationStage.value && !verificationCode.value.trim()) {
    errorMessage.value = '请输入 6 位邮箱验证码。'
    return
  }

  submitting.value = true
  try {
    const prefix = requiresBootstrap.value ? '/api/auth/bootstrap' : '/api/auth/register'
    const response = await fetch(
      verificationStage.value ? `${prefix}/verify` : `${prefix}/send-code`,
      {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(
          verificationStage.value
            ? { challenge_id: challengeId.value, code: verificationCode.value.trim() }
            : { email: email.value.trim(), display_name: displayName.value.trim(), password: password.value }
        )
      }
    )
    if (!response.ok) throw new Error(await responseError(response, '账户验证失败'))
    const payload = await response.json()
    if (!verificationStage.value) {
      challengeId.value = payload.challenge_id
      verificationStage.value = true
      successMessage.value = '验证码已发送，请在有效期内完成验证。'
    } else {
      emit('authenticated', payload.user)
    }
  } catch (error) {
    errorMessage.value = readableError(error, '账户验证失败，请稍后再试。')
  } finally {
    submitting.value = false
  }
}

async function submitReset() {
  if (!email.value.trim()) {
    errorMessage.value = '请输入已验证的登录邮箱。'
    return
  }
  if (!verificationStage.value && !validateEmail(email.value)) {
    errorMessage.value = '请输入有效的邮箱地址。'
    return
  }
  if (verificationStage.value && (!verificationCode.value.trim() || !validatePassword())) return

  submitting.value = true
  try {
    const response = await fetch(
      verificationStage.value ? '/api/auth/password-reset/verify' : '/api/auth/password-reset/send-code',
      {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(
          verificationStage.value
            ? { challenge_id: challengeId.value, code: verificationCode.value.trim(), new_password: password.value }
            : { email: email.value.trim() }
        )
      }
    )
    if (!response.ok) throw new Error(await responseError(response, '密码重置失败'))
    const payload = await response.json()
    if (!verificationStage.value) {
      challengeId.value = payload.challenge_id
      verificationStage.value = true
      successMessage.value = '验证码已发送，请输入验证码和新密码。'
    } else {
      successMessage.value = '密码已更新，请使用新密码登录。'
      verificationStage.value = false
      mode.value = 'login'
      password.value = ''
      confirmPassword.value = ''
      verificationCode.value = ''
    }
  } catch (error) {
    errorMessage.value = readableError(error, '密码重置失败，请稍后再试。')
  } finally {
    submitting.value = false
  }
}

async function submitLogin() {
  if (!identity.value.trim() || !password.value) {
    errorMessage.value = '请输入邮箱（或历史用户名）和密码。'
    return
  }
  submitting.value = true
  try {
    const response = await fetch('/api/auth/login', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ identity: identity.value.trim(), password: password.value })
    })
    if (!response.ok) throw new Error(await responseError(response, '登录失败'))
    const payload = await response.json()
    emit('authenticated', payload.user)
  } catch (error) {
    errorMessage.value = readableError(error, '登录失败，请检查账号和密码。')
  } finally {
    submitting.value = false
  }
}

function validateEmail(value) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim())
}

async function responseError(response, fallback) {
  try {
    const payload = await response.json()
    return payload?.detail || fallback
  } catch {
    return fallback
  }
}

function readableError(error, fallback) {
  return error instanceof Error && error.message ? error.message : fallback
}
</script>

<template>
  <main class="login-page">
    <section class="login-orbit-panel" aria-label="平台介绍">
      <div class="login-orbit-grid" aria-hidden="true"></div>
      <div class="login-orbit-rings" aria-hidden="true"><i></i><i></i><i></i></div>
      <div class="login-orbit-core" aria-hidden="true"><span>AI</span></div>

      <div class="login-brand"><div class="login-brand-mark">AI</div><span>职业智能工作台</span></div>
      <div class="login-intro">
        <p class="login-kicker">CAREER ORBIT SYSTEM</p>
        <h1>让职业信息<br><em>形成可追踪的轨道</em></h1>
        <p>面经沉淀、岗位解析、求职对话与内容生产，在一个可部署、可管理的平台内协同运行。</p>
      </div>
      <div class="login-capabilities"><span>面经知识库</span><span>职业 Agent</span><span>内容工作流</span></div>
    </section>

    <section class="login-form-panel">
      <div class="login-form-wrap">
        <div class="login-form-heading">
          <p class="login-kicker">SECURE ACCESS</p>
          <h2>{{ panelTitle }}</h2>
          <p>{{ panelDescription }}</p>
        </div>

        <div v-if="loading" class="login-loading" role="status">正在连接账户服务…</div>
        <template v-else>
          <section v-if="requiresCliBootstrap" class="login-cli-bootstrap" aria-live="polite">
            <strong>管理员尚未初始化</strong>
            <p>请登录部署服务器后，在项目目录执行：</p>
            <code>docker compose --env-file .env.production -f docker-compose.production.yml exec -it career-api python scripts/bootstrap_first_admin.py</code>
            <p class="login-hint">命令会在终端中安全读取邮箱、显示名称和密码；请勿添加 <code>-T</code>，也不要把密码写入命令行。</p>
          </section>

          <template v-else>
            <nav v-if="showAccountTabs" class="login-mode-tabs" aria-label="账户操作">
              <button :class="{ active: mode === 'login' }" type="button" @click="switchMode('login')">登录</button>
              <button :class="{ active: mode === 'register' }" type="button" @click="switchMode('register')">注册</button>
            </nav>

          <form class="login-form" @submit.prevent="submit">
            <template v-if="mode === 'login' && !requiresBootstrap">
              <label><span>登录邮箱 <small>历史账号可填用户名</small></span><input v-model="identity" autocomplete="username" maxlength="160" placeholder="name@example.com" /></label>
              <label><span>密码</span><input v-model="password" type="password" autocomplete="current-password" placeholder="输入密码" /></label>
            </template>

            <template v-else-if="(mode === 'register' || requiresBootstrap) && !verificationStage">
              <label><span>邮箱</span><input v-model="email" type="email" autocomplete="email" maxlength="254" placeholder="name@example.com" /></label>
              <label><span>显示名称</span><input v-model="displayName" autocomplete="name" maxlength="120" placeholder="例如：徐兴龙" /></label>
              <label><span>设置密码 <small>至少 8 位</small></span><input v-model="password" type="password" autocomplete="new-password" minlength="8" placeholder="输入至少 8 位密码" /></label>
              <label><span>确认密码</span><input v-model="confirmPassword" type="password" autocomplete="new-password" minlength="8" placeholder="再次输入密码" /></label>
            </template>

            <template v-else-if="mode === 'reset' && !verificationStage">
              <label><span>已验证邮箱</span><input v-model="email" type="email" autocomplete="email" maxlength="254" placeholder="name@example.com" /></label>
            </template>

            <template v-else>
              <label><span>邮箱验证码</span><input v-model="verificationCode" autocomplete="one-time-code" inputmode="numeric" maxlength="6" placeholder="输入 6 位验证码" /></label>
              <template v-if="mode === 'reset'">
                <label><span>新密码 <small>至少 8 位</small></span><input v-model="password" type="password" autocomplete="new-password" minlength="8" placeholder="输入至少 8 位新密码" /></label>
                <label><span>确认新密码</span><input v-model="confirmPassword" type="password" autocomplete="new-password" minlength="8" placeholder="再次输入新密码" /></label>
              </template>
            </template>

            <p v-if="successMessage" class="login-success">{{ successMessage }}</p>
            <p v-if="errorMessage" class="login-error" role="alert">{{ errorMessage }}</p>
            <button class="login-submit" type="submit" :disabled="submitting">{{ submitting ? '正在处理中…' : primaryLabel }}</button>
          </form>

          <div class="login-switches">
            <button v-if="!requiresBootstrap && mode === 'login'" type="button" @click="switchMode('reset')">忘记密码？</button>
            <button v-if="!requiresBootstrap && mode === 'reset'" type="button" @click="switchMode('login')">返回登录</button>
            <button v-if="verificationStage" type="button" @click="switchMode(requiresBootstrap ? 'register' : mode)">修改邮箱或重新发送</button>
          </div>
          </template>
        </template>

        <p v-if="requiresCliBootstrap" class="login-footer">管理员完成服务器端初始化后，刷新本页并使用初始化邮箱登录。</p>
        <p v-else class="login-footer">验证码有效期为 10 分钟；连续 7 天未操作将自动退出，单次登录最长保留 30 天。</p>
      </div>
    </section>
  </main>
</template>
