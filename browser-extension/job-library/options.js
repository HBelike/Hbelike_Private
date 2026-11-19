const TOKEN_KEY = 'find_job_liepin_user_token_v1'
const form = document.getElementById('token-form')
const input = document.getElementById('token')
const status = document.getElementById('status')
const saveButton = document.getElementById('save')
const removeButton = document.getElementById('remove')

function showStatus(message, error = false) {
  status.textContent = message
  status.classList.toggle('error', error)
}

async function refreshState() {
  const saved = await chrome.storage.local.get(TOKEN_KEY)
  const configured = Boolean(String(saved[TOKEN_KEY] ?? '').trim())
  removeButton.disabled = !configured
  showStatus(configured ? '猎聘令牌已保存在本机扩展中。' : '尚未配置猎聘令牌。')
}

form.addEventListener('submit', async (event) => {
  event.preventDefault()
  const token = input.value.trim()
  if (token.length < 8 || token.length > 4096) {
    showStatus('令牌长度不符合要求，请从官方授权页重新复制。', true)
    return
  }
  saveButton.disabled = true
  try {
    await chrome.storage.local.set({ [TOKEN_KEY]: token })
    input.value = ''
    showStatus('猎聘令牌已保存在本机扩展中。')
    removeButton.disabled = false
  } catch {
    showStatus('保存失败，请检查扩展状态后重试。', true)
  } finally {
    saveButton.disabled = false
  }
})

removeButton.addEventListener('click', async () => {
  removeButton.disabled = true
  try {
    await chrome.storage.local.remove(TOKEN_KEY)
    showStatus('已从本机扩展中移除猎聘令牌。')
  } catch {
    showStatus('移除失败，请重试。', true)
    removeButton.disabled = false
  }
})

void refreshState().catch(() => showStatus('无法读取扩展本机设置，请重新加载扩展后重试。', true))
