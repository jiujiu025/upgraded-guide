import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './styles.css'
import './ui-polish.css'
import App from './App.vue'
import router from './router'
import { initImpersonation } from './api/session'

// 先读 URL hash / sessionStorage 里的模拟 token，再挂载，避免守卫把模拟态当成未登录
initImpersonation()
createApp(App).use(createPinia()).use(router).mount('#app')
