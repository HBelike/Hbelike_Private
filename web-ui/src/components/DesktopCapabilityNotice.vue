<script setup>
const props = defineProps({
  title: { type: String, default: '此功能需要电脑端' },
  description: { type: String, default: '请在电脑端使用已连接的浏览器助手完成操作。' },
  returnPath: { type: String, default: '/career' },
  embedded: { type: Boolean, default: false }
})
const emit = defineEmits(['navigate'])
function returnToCareer(event) {
  if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return
  event.preventDefault()
  emit('navigate', props.returnPath)
}
</script>

<template>
  <section class="desktop-capability-notice" :class="{ embedded }" role="status">
    <span class="capability-icon" aria-hidden="true">▣</span>
    <h2>{{ title }}</h2>
    <p>{{ description }}</p>
    <p class="capability-detail">手机端仍可进行求职对话、查看已绑定的资料和匹配报告。</p>
    <a v-if="returnPath" :href="returnPath" @click="returnToCareer">返回求职助手</a>
  </section>
</template>

<style scoped>
.desktop-capability-notice{box-sizing:border-box;display:flex;min-width:0;min-height:100dvh;flex-direction:column;align-items:center;justify-content:center;gap:18px;padding:max(32px,env(safe-area-inset-top)) 24px max(32px,env(safe-area-inset-bottom));background:var(--ui-canvas,#f5f8fd);color:var(--ui-text,#17223b);text-align:center;font-family:var(--ui-font-body,sans-serif)}
.desktop-capability-notice.embedded{min-height:280px;border:1px solid var(--ui-line,#d7e4f6);border-radius:12px;padding:28px 20px;background:var(--ui-surface,#fff)}
.capability-icon{display:grid;width:56px;height:56px;place-items:center;border-radius:16px;background:var(--ui-surface-active,#eaf3ff);color:var(--ui-accent,#0064dd);font-size:30px}
h2{margin:0;font-size:22px;line-height:1.5}p{max-width:540px;margin:0;color:var(--ui-text-secondary,#526078);font-size:16px;line-height:1.8}.capability-detail{font-size:14px}a{display:inline-flex;min-height:48px;align-items:center;border-radius:10px;background:var(--ui-accent,#0064dd);padding:0 20px;color:white;text-decoration:none;font-weight:600}
</style>
