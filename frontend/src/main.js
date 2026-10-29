import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './styles.css'
import './ui-polish.css'
import App from './App.vue'
import router from './router'

createApp(App).use(createPinia()).use(router).mount('#app')
