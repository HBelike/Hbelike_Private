<script setup>
import { computed, onMounted, ref } from 'vue'
import { shareInboxRequest, sharedMaterialOptions } from '../material-share.js'

const props = defineProps({ busy: Boolean, error: { type: String, default: '' } })
const emit = defineEmits(['continue', 'navigate'])
const material = ref(null)
const loading = ref(true)
const localError = ref('')
const materialType = ref('job')
const sourceMode = ref('text')
const selectedUrl = ref('')
const options = computed(() => sharedMaterialOptions(material.value || {}))
const canContinue = computed(() => material.value && !localError.value && !material.value.error && (
  materialType.value === 'resume' ? options.value.file
    : sourceMode.value === 'file' ? options.value.file
      : sourceMode.value === 'url' ? selectedUrl.value : options.value.text.trim()
))
const shareId = new URLSearchParams(window.location.search).get('share') || ''

onMounted(async () => {
  try {
    if (new URLSearchParams(window.location.search).has('share-error')) throw new Error('浏览器未能暂存分享资料，请重新分享或手动上传。')
    if (!shareId) return
    material.value = await shareInboxRequest('share:read', shareId)
    if (!material.value) throw new Error('这份分享资料已处理或已过期，请重新分享。')
    sourceMode.value = options.value.file ? 'file' : options.value.links.length ? 'url' : 'text'
    selectedUrl.value = options.value.links.length === 1 ? options.value.links[0] : ''
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '分享资料读取失败，请重新分享。'
  } finally {
    loading.value = false
  }
})

function continueImport() {
  if (!canContinue.value || props.busy) return
  emit('continue', {
    id: shareId, type: materialType.value, mode: materialType.value === 'resume' ? 'file' : sourceMode.value,
    file: options.value.file, url: selectedUrl.value, text: options.value.text, title: material.value.title || ''
  })
}

async function discard() {
  if (props.busy) return
  try {
    if (shareId) await shareInboxRequest('share:delete', shareId)
    emit('navigate', '/career')
  } catch (error) {
    localError.value = error.message
  }
}
</script>

<template>
  <section class="material-share-page" aria-labelledby="material-share-heading">
    <header class="share-page-header">
      <div class="share-page-mark" aria-hidden="true">↓</div>
      <div><h2 id="material-share-heading">导入分享资料</h2><p>先选择资料用途，再到求职对话中校对。确认前不会保存或分析。</p></div>
    </header>
    <p v-if="loading" role="status">正在接收分享资料…</p>
    <p v-if="localError || material?.error || error" class="share-error" role="alert">{{ localError || material?.error || error }}</p>
    <template v-if="material && !material.error && !localError">
      <section class="share-received" aria-label="收到的资料">
        <h3>收到的资料</h3>
        <strong v-if="material.title">{{ material.title }}</strong>
        <p v-if="options.file" class="share-file-name">{{ options.file.name }} <small>{{ (options.file.size / 1024).toFixed(1) }} KB</small></p>
        <pre v-if="options.text">{{ options.text }}</pre>
      </section>
      <fieldset class="share-purpose"><legend>这份资料用于</legend>
        <label :class="{ selected: materialType === 'job' }"><input v-model="materialType" type="radio" value="job" />目标岗位<small>职位正文、公开链接或截图文件</small></label>
        <label :class="{ selected: materialType === 'resume' }"><input v-model="materialType" type="radio" value="resume" :disabled="!options.file" />个人简历<small>{{ options.file ? 'PDF、Word 或单张简历图片' : '简历请分享 PDF、Word 或图片文件' }}</small></label>
      </fieldset>
      <fieldset v-if="materialType === 'job'" class="share-source"><legend>导入内容</legend>
        <label v-if="options.file"><input v-model="sourceMode" type="radio" value="file" />截图或文件</label>
        <label v-if="options.text"><input v-model="sourceMode" type="radio" value="text" />职位正文</label>
        <label v-if="options.links.length"><input v-model="sourceMode" type="radio" value="url" />职位链接</label>
        <label v-if="sourceMode === 'url'" class="share-url-choice">选择具体职位链接<select v-model="selectedUrl" aria-label="选择具体职位链接"><option disabled value="">请选择一个链接</option><option v-for="url in options.links" :key="url" :value="url">{{ url }}</option></select></label>
      </fieldset>
      <p class="share-note">导入后仍需选择会话、解析并校对资料。链接需要登录时，可改用截图或正文。</p>
      <footer class="share-actions"><button type="button" :disabled="busy" @click="discard">取消导入</button><button type="button" class="share-primary" :disabled="!canContinue || busy" @click="continueImport">{{ busy ? '正在移交…' : '前往对话校对' }}</button></footer>
    </template>
    <template v-else-if="!loading">
      <div class="share-install-guide">
        <h3>从手机浏览器分享职位</h3>
        <ol><li>用 Android Chrome 打开本平台，在浏览器菜单中选择“安装应用”或“添加到主屏幕”。</li><li>在招聘网页或 App 中打开具体岗位，点“分享”，选择“求职助手”。</li><li>选择岗位或简历用途，在本平台校对后确认。</li></ol>
        <p>系统分享需要浏览器支持且完成安装；必须使用 HTTPS。iPhone 或未出现分享入口时，继续复制职位正文、链接，或上传文件和截图。</p>
        <button type="button" @click="discard">返回求职助手，手动导入</button>
      </div>
    </template>
  </section>
</template>

<style scoped>
.material-share-page{width:min(800px,100%);margin:16px auto 32px;padding:28px;box-sizing:border-box;border:1px solid var(--ui-line);border-radius:20px;background:var(--ui-surface);color:var(--ui-text);overflow-wrap:anywhere}
.share-page-header{display:flex;align-items:flex-start;gap:16px;margin-bottom:24px}.share-page-mark{display:grid;place-items:center;width:48px;height:48px;flex:none;border-radius:14px;background:var(--ui-surface-active);color:var(--ui-accent);font-size:30px}.share-page-header h2{margin:0 0 8px;font-size:24px}.share-page-header p,.share-note,.share-install-guide p{margin:0;color:var(--ui-text-secondary);line-height:1.7}
.share-received{padding:18px;background:var(--ui-surface-soft);border:1px solid var(--ui-line);border-radius:12px}.share-received h3{margin:0 0 12px;font-size:15px}.share-received pre{max-height:180px;overflow:auto;white-space:pre-wrap;font:inherit;font-size:14px;line-height:1.6;margin-bottom:0}.share-file-name{font-weight:700;overflow-wrap:anywhere}.share-file-name small{font-weight:400;color:var(--ui-text-secondary)}
.share-purpose,.share-source{padding:0;margin:24px 0;border:0;min-width:0}.share-purpose legend,.share-source legend{padding:0;margin-bottom:12px;font-weight:750}.share-purpose{display:grid;grid-template-columns:1fr 1fr;gap:12px}.share-purpose label{display:grid;grid-template-columns:18px minmax(0,1fr);align-items:center;gap:8px;padding:16px;border:1px solid var(--ui-line);border-radius:12px;cursor:pointer}.share-purpose label.selected{border-color:var(--ui-accent);background:var(--ui-surface-active)}.share-purpose small{grid-column:2;color:var(--ui-text-secondary);font-size:13px;line-height:1.5}.share-source>label{display:inline-flex;gap:6px;margin-right:20px}.share-source>.share-url-choice{display:flex;flex-direction:column;margin:16px 0 0;font-size:14px;gap:8px}.share-url-choice select{max-width:100%;min-width:0;min-height:42px;padding:8px;border:1px solid var(--ui-line-strong);border-radius:8px;color:var(--ui-text);background:var(--ui-surface)}
.share-actions{display:flex;justify-content:flex-end;gap:12px;margin-top:24px}.material-share-page button{min-height:44px;padding:10px 18px;border:1px solid var(--ui-line-strong);border-radius:10px;background:var(--ui-surface);color:var(--ui-accent-ink);font:inherit;font-weight:700;cursor:pointer}.material-share-page button.share-primary{background:var(--ui-accent);border-color:var(--ui-accent);color:#fff}.material-share-page button:disabled{opacity:.5;cursor:default}.share-error{padding:14px;border-radius:10px;background:var(--ui-surface-soft);color:var(--ui-danger)}.share-install-guide h3{margin-top:8px}.share-install-guide ol{padding-left:22px;line-height:1.9}.share-install-guide button{margin-top:24px}
@media(max-width:600px){.material-share-page{margin:8px auto 20px;padding:16px;border-radius:14px}.share-page-header{gap:12px;margin-bottom:20px}.share-page-header h2{font-size:21px}.share-page-header p,.share-note{font-size:14px}.share-page-mark{width:38px;height:38px;font-size:26px;border-radius:11px}.share-purpose{grid-template-columns:1fr;gap:10px}.share-purpose label{padding:12px}.share-received{padding:14px}.share-received pre{max-height:140px}.share-source>label{margin-bottom:10px}.share-actions{display:grid;grid-template-columns:1fr 1.2fr;padding:12px 0 0;border-top:1px solid var(--ui-line)}.share-actions button{padding:10px 8px;font-size:14px}.share-install-guide button{width:100%}}
</style>
