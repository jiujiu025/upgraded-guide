<script setup>
import { computed, ref } from 'vue'
import { ArrowUpRight, Menu, X } from 'lucide-vue-next'
import { RouterView, useRoute, useRouter } from 'vue-router'
import BrandLogo from './components/BrandLogo.vue'
import { session } from './api/session'

const route = useRoute()
const router = useRouter()
const menuOpen = ref(false)
const loading = ref(true)
const isWorkspace = computed(() => Boolean(route.meta.workspace || route.meta.standalone))
// 已登录用户从官网页面（产品详情、选购、文章）回不到工作台，是实际反馈过的问题：
// 头部把「客户登录」换成「返回工作台」，选购按钮也直接进客户中心内的选购页。
const signedIn = computed(() => Boolean(session.user))
const workspaceHome = computed(() => {
  const role = session.user?.role
  if (role === 'customer') return { label: '返回客户中心', path: '/client' }
  if (role === 'sales') return { label: '返回销售工作台', path: '/sales' }
  if (['admin', 'super_admin'].includes(role)) return { label: '返回管理后台', path: '/admin' }
  return null
})
const buyPath = computed(() => (session.user?.role === 'customer' ? '/client/buy' : '/buy'))
setTimeout(() => { loading.value = false }, 700)
function go(path) { menuOpen.value = false; router.push(path) }
</script>

<template>
  <div class="app-shell">
    <template v-if="isWorkspace">
      <RouterView v-slot="{ Component }">
        <Transition name="route-view" mode="out-in">
          <component :is="Component" />
        </Transition>
      </RouterView>
    </template>
    <template v-else>
      <div v-if="loading" class="boot-screen" aria-label="页面加载中">
        <BrandLogo class="boot-logo" eager />
        <div class="boot-line"><b></b></div>
        <p>INITIALIZING GLOBAL ROUTES</p>
      </div>
      <template v-else>
        <header class="site-header">
          <button class="brand" aria-label="返回首页" @click="go('/')"><BrandLogo eager /></button>
          <nav class="desktop-nav" aria-label="主导航">
            <button @click="go('/products')">产品服务</button><button @click="go('/regions')">覆盖地区</button>
            <button @click="go('/about')">解决方案</button><button @click="go('/help')">用户教程</button>
            <button @click="go('/resources')">资源中心</button><button @click="go('/about')">关于我们</button>
            <button @click="go('/contact')">联系我们</button>
          </nav>
          <div class="header-actions">
            <button v-if="workspaceHome" class="login-link" @click="go(workspaceHome.path)">{{ workspaceHome.label }}</button>
            <button v-else class="login-link" @click="go('/login')">客户登录</button>
            <button class="button button-small" @click="go(buyPath)">立即选购 <ArrowUpRight :size="15" /></button>
            <button class="mobile-menu" aria-label="打开菜单" @click="menuOpen = true"><Menu :size="22" /></button>
          </div>
        </header>
        <div v-if="menuOpen" class="mobile-drawer">
          <div class="drawer-head"><BrandLogo /><button aria-label="关闭菜单" @click="menuOpen = false"><X :size="22" /></button></div>
          <nav><button v-for="item in [['产品服务','/products'],['覆盖地区','/regions'],['解决方案','/about'],['用户教程','/help'],['资源中心','/resources'],['联系我们','/contact']]" :key="item[1]" @click="go(item[1])">{{ item[0] }}</button></nav>
          <button v-if="workspaceHome" class="button drawer-cta" @click="go(workspaceHome.path)">{{ workspaceHome.label }}</button>
          <button class="button drawer-cta" @click="go(buyPath)">立即选购 <ArrowUpRight :size="15" /></button>
        </div>
        <main><RouterView v-slot="{ Component }"><Transition name="route-view" mode="out-in"><component :is="Component" :key="route.path" /></Transition></RouterView></main>
      </template>
    </template>
  </div>
</template>
