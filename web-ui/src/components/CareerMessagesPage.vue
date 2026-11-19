<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch, nextTick } from 'vue'
import { jobLibraryBridge } from '../job-library-bridge.js'
import { bossChatSourceUrl, createExternalChatClient, createExternalChatInbox, externalMessageTypeLabel } from '../external-chat-client.js'
import { BOSS_EXTENSION_GUIDE_URL, BOSS_EXTENSION_DOWNLOAD_URL } from '../boss-extension-onboarding.js'

const emit = defineEmits(['navigate'])
const state = ref({ accounts: [], conversations: [], messages: [], loading: true, connection: 'checking' })
const messageList = ref(null)
const draft = ref('')
const inbox = createExternalChatInbox({ api: createExternalChatClient(), bridge: jobLibraryBridge, onChange: (value) => { state.value = value } })
const account = computed(() => state.value.accounts.find((item) => item.id === state.value.selectedAccountId))
const conversation = computed(() => state.value.conversations.find((item) => item.id === state.value.selectedConversationId))
const sourceUrl = computed(() => bossChatSourceUrl(conversation.value?.source_url))
const connectionText = computed(() => ({ checking: '正在连接浏览器助手', ready: '自动接收已开启', paused: '接收已暂停', history_only: '仅查看历史', offline: '浏览器助手未连接', needs_update: '需要更新浏览器助手', service_unavailable: '招聘沟通服务未就绪' })[state.value.connection] || '等待连接')
const operations = computed(() => (state.value.operations || []).filter((item) => item.conversation_id === conversation.value?.id))
const canSend = computed(() => state.value.connection === 'ready' && state.value.canReply && !state.value.sending && draft.value.trim())
const operationLabel = (value) => ({ pending: '等待提交', executing: '正在核对身份', submitting: '正在提交，结果待核对', submitted: '已触发提交，结果待核对', accepted: '平台已接受', failed: '明确失败', unknown: '结果待核对', cancelled: '已取消', expired: '已过期，未提交' })[value] || '结果待核对'
let timer = null

function formatTime(value, { short = false } = {}) {
  const date = new Date(value || '')
  if (!Number.isFinite(date.getTime())) return short ? '' : '尚未同步'
  return date.toLocaleString('zh-CN', short ? { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false } : { month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
}

function handleVisibility() { void inbox.setVisible(!document.hidden) }
async function sendDraft() {
  const text = draft.value
  if (await inbox.sendMessage(text) && draft.value === text) draft.value = ''
}

onMounted(() => {
  void inbox.setVisible(!document.hidden)
  void inbox.start()
  document.addEventListener('visibilitychange', handleVisibility)
  timer = window.setInterval(() => { void inbox.refresh() }, 30_000)
})
onBeforeUnmount(() => {
  window.clearInterval(timer)
  document.removeEventListener('visibilitychange', handleVisibility)
  inbox.dispose()
})

watch(() => [state.value.selectedConversationId, state.value.messages.at(-1)?.platform_message_id], async () => {
  await nextTick()
  messageList.value?.scrollTo({ top: messageList.value.scrollHeight, behavior: 'auto' })
})
watch(() => state.value.selectedConversationId, () => { draft.value = '' })
</script>

<template>
  <section class="career-inbox" aria-labelledby="career-inbox-title">
    <header class="inbox-heading">
      <div class="heading-copy"><button class="back-link" type="button" @click="emit('navigate', '/career')">← 返回求职助手</button><h2 id="career-inbox-title">招聘沟通</h2><p>接收 BOSS 上的 HR 回复，查看最近 30 天已同步的记录。</p></div>
      <div class="sync-actions">
        <span class="connection-status" :class="state.connection"><i aria-hidden="true"></i>{{ connectionText }}</span>
        <button v-if="state.connection === 'history_only'" class="inbox-button primary" type="button" :disabled="state.syncing" @click="inbox.takeover()">在此接管</button>
        <button class="inbox-button primary" type="button" :disabled="state.syncing || state.loading" @click="inbox.refresh({ manual: true })">{{ state.syncing ? '正在同步…' : (state.connection === 'paused' ? '重新检测并继续' : '立即同步') }}</button>
      </div>
    </header>

    <div v-if="state.pauseReason" class="inbox-notice warning" role="alert"><strong>接收已暂停</strong><p>{{ state.pauseReason }}</p><span>已有历史仍可查看，处理后请手动继续。</span></div>
    <div v-else-if="state.error" class="inbox-notice" role="alert"><p>{{ state.error }}</p><template v-if="['offline', 'needs_update'].includes(state.connection)"><a :href="BOSS_EXTENSION_GUIDE_URL" target="_blank" rel="noopener noreferrer">{{ state.connection === 'needs_update' ? '查看更新说明' : '查看安装说明' }}</a><a :href="BOSS_EXTENSION_DOWNLOAD_URL" download>下载最新版助手</a></template><button v-else type="button" @click="inbox.refresh({ manual: true })">重新检测</button></div>
    <div v-if="state.syncIncomplete" class="inbox-notice" role="status"><p>{{ state.syncIncomplete }}</p></div>

    <div class="inbox-workspace">
      <aside class="conversation-panel" aria-label="BOSS 会话列表">
        <div class="account-picker">
          <div class="platform-label"><span class="boss-mark" aria-hidden="true">B</span><strong>BOSS 直聘</strong><span>求职者账号</span></div>
          <label for="external-chat-account">查看账号</label>
          <select id="external-chat-account" :value="state.selectedAccountId || ''" :disabled="state.loading" @change="inbox.selectAccount($event.target.value)">
            <option v-for="item in state.accounts" :key="item.id" :value="item.id">{{ item.display_name || 'BOSS 账号' }} · {{ item.platform_account_id }}</option>
            <option value="">同步当前浏览器账号</option>
          </select>
          <p v-if="account" class="account-caption">账号标识 {{ account.platform_account_id }}</p>
        </div>
        <div class="conversation-list-heading"><h3>会话</h3><span>{{ state.conversations.length }} 个已保存</span></div>
        <div class="conversation-list" :aria-busy="state.loading">
          <p v-if="state.loading" class="panel-empty">正在读取平台历史…</p>
          <div v-else-if="!state.conversations.length" class="panel-empty"><strong>还没有已保存的会话</strong><p>在电脑浏览器登录 BOSS 并启用助手后，点击“立即同步”接收会话。</p></div>
          <button v-for="item in state.conversations" :key="item.id" type="button" class="conversation-row" :class="{ selected: item.id === state.selectedConversationId }" :aria-pressed="item.id === state.selectedConversationId" @click="inbox.selectConversation(item.id)">
            <span class="peer-avatar" aria-hidden="true">{{ (item.peer_name || '聘').slice(0, 1) }}</span>
            <span class="conversation-copy"><span class="peer-line"><strong>{{ item.peer_name || '未命名招聘者' }}</strong><time>{{ formatTime(item.last_message_at, { short: true }) }}</time></span><span class="peer-company">{{ [item.company, item.job_title].filter(Boolean).join(' · ') || '暂无关联岗位信息' }}</span><span class="preview-line"><span>{{ item.last_message_preview || '暂无最近消息摘要' }}</span><b v-if="item.unread_count > 0" class="unread-badge" :aria-label="`${item.unread_count} 条未读`">{{ item.unread_count > 99 ? '99+' : item.unread_count }}</b><small v-else-if="item.unread_count == null">未读未知</small></span></span>
          </button>
        </div>
        <div class="list-footer"><p v-if="state.listHasMore">这里只显示 BOSS 本次可读取的会话。</p><p>最后同步：{{ formatTime(state.lastSyncedAt) }}</p></div>
      </aside>

      <section class="message-panel" aria-label="当前会话消息">
        <template v-if="conversation">
          <header class="conversation-heading"><div><h3>{{ conversation.peer_name || '未命名招聘者' }}</h3><p>{{ [conversation.company, conversation.job_title].filter(Boolean).join(' · ') || '此会话暂无关联岗位信息' }}</p></div><a class="inbox-button" :href="sourceUrl" target="_blank" rel="noopener noreferrer">到 BOSS 回复 <span aria-hidden="true">↗</span></a></header>
          <div ref="messageList" class="external-message-list" :aria-busy="state.loadingMessages" aria-live="polite">
            <button v-if="state.hasMore" class="load-older" type="button" :disabled="state.loadingMessages" @click="inbox.loadOlder()">加载更早的已保存消息</button>
            <p class="history-boundary">仅显示最近 30 天已同步的消息</p>
            <p v-if="state.loadingMessages && !state.messages.length" class="panel-empty">正在读取会话…</p>
            <div v-else-if="!state.messages.length" class="panel-empty"><strong>暂无可显示的消息</strong><p>此会话尚未同步到正文，或原消息已超过 30 天。可到 BOSS 查看完整会话。</p></div>
            <article v-for="message in state.messages" :key="message.platform_message_id" class="external-message" :class="message.direction" :aria-label="message.direction === 'incoming' ? 'HR 消息' : '我的消息'">
              <div class="message-author"><span>{{ message.direction === 'incoming' ? (conversation.peer_name || '招聘者') : '我' }}</span><time>{{ formatTime(message.sent_at, { short: true }) }}</time></div>
              <p v-if="message.message_type === 'text'" class="message-bubble">{{ message.text }}</p>
              <div v-else class="message-bubble media-placeholder"><strong>{{ externalMessageTypeLabel(message.message_type) }}</strong><a :href="bossChatSourceUrl(message.source_url || conversation.source_url)" target="_blank" rel="noopener noreferrer">到 BOSS 查看</a></div>
            </article>
          </div>
          <div v-if="operations.length" class="operation-list" aria-label="发送操作状态">
            <article v-for="item in operations" :key="item.client_operation_id" class="operation-row">
              <div><strong>{{ operationLabel(item.status) }}</strong><p>{{ item.text }}</p><small v-if="item.error">{{ item.error }}</small></div>
              <button v-if="['pending', 'executing'].includes(item.status)" class="inbox-button" type="button" @click="inbox.cancelOperation(item.client_operation_id)">取消本次操作</button>
              <a v-if="['submitted', 'submitting', 'unknown'].includes(item.status)" :href="sourceUrl" target="_blank" rel="noopener noreferrer">到 BOSS 核对</a>
            </article>
          </div>
          <form class="inbox-composer" @submit.prevent="sendDraft">
            <label for="boss-reply-text">回复 {{ conversation.peer_name || '当前招聘者' }}</label>
            <textarea id="boss-reply-text" v-model="draft" rows="3" maxlength="5000" :disabled="state.connection !== 'ready' || !state.canReply || state.sending" placeholder="输入最终正文，确认后通过本地 BOSS 页面提交。" />
            <div class="composer-actions"><p>每次点击只确认这一条消息。待核对操作不会重发；另发相同正文可能重复。</p><button class="inbox-button primary" type="submit" :disabled="!canSend">{{ state.sending ? '正在处理…' : '确认发送' }}</button></div>
          </form>
          <footer class="inbox-reply-note"><span class="read-only-icon" aria-hidden="true">↗</span><div><strong>{{ state.connection === 'ready' && state.canReply ? '使用当前浏览器提交，结果以平台证据为准' : '仍可到 BOSS 继续回复' }}</strong><p>未选会话只同步摘要；读取当前会话可能产生正常已读行为。离页或等待超时将停止尚未提交的操作。</p></div></footer>
        </template>
        <div v-else class="no-conversation"><span class="inbox-empty-mark" aria-hidden="true"><svg viewBox="0 0 48 48" fill="none"><path d="M8 12h32v25H18L8 43V12Z"/><path d="M16 21h16M16 28h10"/></svg></span><h3>选择一段招聘沟通</h3><p>从左侧会话查看 HR 回复。没有浏览器助手时，也可以查看平台已经保存的历史。</p><a :href="sourceUrl" target="_blank" rel="noopener noreferrer">打开 BOSS 沟通页 ↗</a></div>
      </section>
    </div>
    <p class="inbox-page-note">页面可见且连接就绪时，每 30 秒同步一次。浏览器关闭、页面休眠或平台要求验证时，将暂停接收。</p>
  </section>
</template>

<style scoped>
.inbox-composer{flex:none;border-top:1px solid var(--ui-line);padding:12px 24px;background:var(--ui-surface)}.inbox-composer label{display:block;font-size:12px;font-weight:600;margin-bottom:7px}.inbox-composer textarea{width:100%;resize:vertical;min-height:64px;max-height:180px;box-sizing:border-box;border:1px solid var(--ui-line-strong);border-radius:8px;padding:9px;background:var(--ui-surface);color:inherit;font:inherit;font-size:13px}.composer-actions{display:flex;align-items:center;gap:16px;margin-top:7px}.composer-actions p{flex:1;margin:0;color:var(--ui-text-muted);font-size:10px;line-height:1.6}.operation-list{flex:none;max-height:150px;overflow:auto;border-top:1px solid var(--ui-line);padding:8px 24px}.operation-row{display:flex;align-items:center;gap:12px;font-size:11px;padding:5px 0}.operation-row>div{flex:1;min-width:0}.operation-row p{margin:4px 0;white-space:pre-wrap;overflow-wrap:anywhere;color:var(--ui-text-secondary)}.operation-row small{color:var(--ui-text-muted)}.operation-row a{color:var(--ui-accent-ink);white-space:nowrap}
.career-inbox{display:flex;flex-direction:column;min-height:0;flex:1;color:var(--ui-text,#17223b);font-family:var(--ui-font-body);padding:20px 24px 12px;gap:14px;overflow:hidden;background:var(--ui-canvas,#f5f8fd)}
.inbox-heading{display:flex;justify-content:space-between;gap:24px;align-items:center;flex:none}.heading-copy h2{margin:7px 0;font:750 25px/1.25 var(--ui-font-display)}.heading-copy>p{margin:0;color:var(--ui-text-secondary);font-size:13px}.back-link{border:0;background:transparent;padding:0;color:var(--ui-accent-ink);font-size:12px;cursor:pointer}.sync-actions{display:flex;align-items:center;gap:15px}.connection-status{display:inline-flex;align-items:center;gap:7px;white-space:nowrap;color:var(--ui-text-secondary);font-size:12px}.connection-status i{width:7px;height:7px;border-radius:50%;background:var(--ui-text-muted)}.connection-status.ready i{background:var(--ui-success,#247a5a)}.connection-status.paused i{background:var(--ui-warning,#9a650d)}
.inbox-button{display:inline-flex;justify-content:center;align-items:center;gap:8px;min-height:36px;padding:8px 13px;border:1px solid var(--ui-line-strong);border-radius:8px;background:var(--ui-surface,#fff);color:var(--ui-accent-ink);font-size:12px;font-weight:650;text-decoration:none;cursor:pointer;white-space:nowrap}.inbox-button.primary{background:var(--ui-accent);border-color:var(--ui-accent);color:#fff}.inbox-button:disabled{opacity:.55;cursor:wait}.inbox-notice{display:flex;align-items:center;flex-wrap:wrap;gap:8px 14px;padding:11px 14px;border:1px solid var(--ui-line-strong);border-radius:9px;background:var(--ui-surface);font-size:12px;color:var(--ui-text-secondary)}.inbox-notice p{margin:0;line-height:1.6}.inbox-notice button{border:0;background:none;color:var(--ui-accent-ink);font-weight:650;cursor:pointer}.inbox-notice.warning{display:block;border-color:#ebd5a7;background:var(--ui-warning-soft,#fff8e8);color:var(--ui-warning,#9a650d)}.inbox-notice.warning strong{font-size:13px}.inbox-notice.warning p{margin:4px 0}.inbox-notice.warning>span{font-size:11px}
.inbox-workspace{display:grid;grid-template-columns:320px minmax(0,1fr);flex:1;min-height:0;overflow:hidden;border:1px solid var(--ui-line);border-radius:13px;background:var(--ui-surface,#fff);box-shadow:var(--ui-shadow)}.conversation-panel{display:flex;flex-direction:column;min-height:0;border-right:1px solid var(--ui-line);background:var(--ui-sidebar,#f7faff)}.account-picker{padding:18px 16px 13px;border-bottom:1px solid var(--ui-line)}.platform-label{display:flex;gap:8px;align-items:center;margin-bottom:16px;font-size:13px}.platform-label>span:last-child{margin-left:auto;font-size:10px;color:var(--ui-text-muted)}.boss-mark{display:grid;place-items:center;width:26px;height:26px;border-radius:7px;background:var(--ui-surface-active);color:var(--ui-accent-ink);font-weight:800}.account-picker label{display:block;margin-bottom:5px;font-size:11px;color:var(--ui-text-secondary)}.account-picker select{width:100%;height:36px;padding:0 9px;border:1px solid var(--ui-line-strong);border-radius:7px;background:var(--ui-surface);color:var(--ui-text);font-size:12px}.account-caption{margin:7px 0 0;color:var(--ui-text-muted);font-size:10px;overflow-wrap:anywhere}.conversation-list-heading{display:flex;align-items:center;justify-content:space-between;padding:13px 16px 8px}.conversation-list-heading h3{font-size:12px;margin:0}.conversation-list-heading>span{font-size:10px;color:var(--ui-text-muted)}.conversation-list{flex:1;min-height:0;overflow:auto;padding:0 7px 10px}.conversation-row{display:flex;align-items:flex-start;width:100%;gap:10px;text-align:left;padding:14px 9px;border:1px solid transparent;border-radius:9px;background:transparent;color:inherit;cursor:pointer}.conversation-row:hover{background:var(--ui-surface)}.conversation-row.selected{background:var(--ui-surface-active);border-color:var(--ui-line-strong)}.peer-avatar{display:grid;place-items:center;flex:none;width:37px;height:37px;border:1px solid var(--ui-line);border-radius:11px;background:var(--ui-surface);font-size:15px;font-weight:700;color:var(--ui-accent-ink)}.conversation-copy{flex:1;min-width:0}.peer-line{display:flex;align-items:center;justify-content:space-between;gap:8px}.peer-line strong{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.peer-line time{flex:none;font-size:9px;color:var(--ui-text-muted)}.peer-company{display:block;font-size:10px;color:var(--ui-text-secondary);margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.preview-line{display:flex;align-items:center;gap:5px;margin-top:8px;color:var(--ui-text-muted);font-size:11px}.preview-line>span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}.preview-line small{font-size:9px;white-space:nowrap}.unread-badge{display:grid;place-items:center;min-width:18px;height:18px;padding:0 4px;border-radius:9px;background:var(--ui-accent);color:#fff;font-size:10px}.list-footer{padding:8px 16px;border-top:1px solid var(--ui-line);font-size:10px;color:var(--ui-text-muted)}.list-footer p{margin:4px 0}
.inbox-notice a{color:var(--ui-accent-ink);font-weight:650;text-decoration:none}
.message-panel{display:flex;flex-direction:column;min-height:0;min-width:0;background:var(--ui-surface)}.conversation-heading{display:flex;align-items:center;justify-content:space-between;gap:15px;min-height:76px;padding:16px 24px;border-bottom:1px solid var(--ui-line)}.conversation-heading h3{margin:0;font-size:16px}.conversation-heading p{margin:5px 0 0;font-size:12px;color:var(--ui-text-secondary);overflow-wrap:anywhere}.external-message-list{flex:1;min-height:0;overflow:auto;padding:18px 28px 28px;background:linear-gradient(180deg,var(--ui-surface-soft,#f4f8fe),var(--ui-surface,#fff) 150px)}.history-boundary{text-align:center;font-size:10px;color:var(--ui-text-muted);margin:0 0 24px}.load-older{display:block;border:0;background:none;color:var(--ui-accent-ink);font-size:11px;margin:0 auto 15px;cursor:pointer}.external-message{display:flex;flex-direction:column;align-items:flex-start;margin:0 0 20px}.external-message.outgoing{align-items:flex-end}.message-author{display:flex;gap:9px;font-size:10px;color:var(--ui-text-muted);margin-bottom:6px}.message-author span{color:var(--ui-text-secondary);font-weight:600}.message-bubble{margin:0;max-width:min(76%,720px);padding:12px 15px;border:1px solid var(--ui-line);border-radius:0 12px 12px 12px;background:var(--ui-surface);font-size:13px;line-height:1.8;white-space:pre-wrap;overflow-wrap:anywhere}.outgoing .message-bubble{border-radius:12px 0 12px 12px;background:var(--ui-surface-active);border-color:var(--ui-line-strong)}.media-placeholder{display:flex;flex-direction:column;gap:6px;min-width:150px}.media-placeholder strong{font-size:12px}.media-placeholder a{color:var(--ui-accent-ink);font-size:11px;text-decoration:none}.inbox-reply-note{display:flex;gap:12px;padding:15px 24px;border-top:1px solid var(--ui-line);background:var(--ui-surface-soft)}.read-only-icon{color:var(--ui-accent-ink);font-size:20px;line-height:1}.inbox-reply-note strong{font-size:12px}.inbox-reply-note p{font-size:11px;line-height:1.7;color:var(--ui-text-secondary);margin:5px 0 0}.panel-empty{padding:24px 14px;color:var(--ui-text-muted);font-size:12px;line-height:1.8}.panel-empty strong{font-size:12px;color:var(--ui-text-secondary)}.panel-empty p{margin:6px 0 0}.no-conversation{display:flex;flex:1;align-items:center;justify-content:center;flex-direction:column;padding:36px;color:var(--ui-text-secondary);text-align:center}.inbox-empty-mark{display:grid;place-items:center;width:72px;height:72px;border:1px solid var(--ui-line);border-radius:19px;color:var(--ui-accent-ink);background:var(--ui-surface-active);margin-bottom:12px}.inbox-empty-mark svg{width:38px;height:38px;stroke:currentColor;stroke-width:1.5}.no-conversation h3{font-size:18px;color:var(--ui-text);margin:10px 0}.no-conversation p{max-width:330px;font-size:12px;line-height:1.9;margin:0 0 16px}.no-conversation a{color:var(--ui-accent-ink);text-decoration:none;font-size:12px}.inbox-page-note{margin:0;font-size:10px;color:var(--ui-text-muted);flex:none}
</style>
