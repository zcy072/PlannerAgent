import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHistory } from 'vue-router'
import 'element-plus/dist/index.css'

import App from './App.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'Home', component: () => import('./views/Home.vue') },
    { path: '/result', name: 'Result', component: () => import('./views/Result.vue') }
  ]
})

const app = createApp(App)

app.use(createPinia())
app.use(router)

app.mount('#app')
