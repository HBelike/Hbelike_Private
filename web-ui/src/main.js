import { createApp } from 'vue'
import App from './App.vue'
import './styles.css'
import './theme.css'
import './mobile.css'
import { registerShareWorker } from './material-share.js'

const storedTheme = window.localStorage.getItem('find-job-ui-theme')
document.documentElement.dataset.uiTheme = storedTheme === 'green' ? 'green' : 'blue'

createApp(App).mount('#app')
// 不支持安装或分享的浏览器继续使用原网页和文件入口。
registerShareWorker().catch(() => {})
