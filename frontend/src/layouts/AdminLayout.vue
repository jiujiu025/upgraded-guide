<script setup>
import { computed, ref } from 'vue'
import { LogOut, Menu, X } from 'lucide-vue-next'
import { RouterView, useRoute, useRouter } from 'vue-router'
import BrandLogo from '../components/BrandLogo.vue'
import { logout as endSession } from '../api'
import { session } from '../api/session'
import { getAdminNavigation } from '../navigation/adminNavigation'

const route = useRoute()
const router = useRouter()
const mobileOpen = ref(false)
const user = computed(() => session.user || {})
const navigation = computed(() => getAdminNavigation(user.value.role))
const isActive = (path) => route.path === path || (path !== '/admin' && route.path.startsWith(`${path}/`))

async function logout() {
  await endSession()
  router.replace('/admin/login')
}
</script>

<template>
  <div class="admin-layout">
    <aside class="admin-sidebar" :class="{ open: mobileOpen }">
      <div class="admin-brand"><BrandLogo eager /><button class="admin-close" aria-label="关闭导航" @click="mobileOpen = false"><X :size="20" /></button></div>
      <p class="admin-role">管理后台</p>
      <nav aria-label="管理导航">
        <button v-for="[label, path, icon] in navigation" :key="path" :class="{ active: isActive(path) }" :aria-current="isActive(path) ? 'page' : undefined" @click="router.push(path)"><component :is="icon" :size="18" />{{ label }}</button>
      </nav>
      <div class="admin-user"><span>{{ user.display_name || user.username }}</span><small>{{ user.role }}</small><button @click="logout"><LogOut :size="15" />退出登录</button></div>
    </aside>
    <button v-if="mobileOpen" class="admin-scrim" aria-label="关闭导航" @click="mobileOpen = false"></button>
    <button class="admin-mobile-menu" aria-label="打开管理导航" @click="mobileOpen = true"><Menu :size="20" /></button>
    <main class="admin-content">
      <RouterView v-slot="{ Component }">
        <Transition name="route-view" mode="out-in">
          <component :is="Component" :key="route.path" />
        </Transition>
      </RouterView>
    </main>
  </div>
</template>

<style>
.admin-layout{min-height:100vh;display:grid;grid-template-columns:250px minmax(0,1fr);background:#f2f5f3;color:#20342d}
.admin-sidebar{position:sticky;top:0;z-index:40;height:100vh;padding:22px 14px;display:flex;flex-direction:column;background:#fff;border-right:1px solid #dce5e1}
.admin-brand{height:48px;padding:0 9px;display:flex;align-items:center;justify-content:space-between}.admin-brand .brand-logo{max-width:150px}
.admin-role{margin:6px 10px 23px;color:#74887f;font-size:12px}
.admin-sidebar nav{display:grid;gap:4px;overflow-y:auto;scrollbar-width:thin;scrollbar-color:#c5d8cd transparent}
.admin-sidebar nav button{min-height:44px;padding:0 11px;display:flex;align-items:center;gap:10px;border-radius:6px;color:#526860;text-align:left;font-size:13px;transition:background .18s,color .18s}
.admin-sidebar nav button:hover,.admin-sidebar nav button.active{background:#e7f4ef;color:#176b58}.admin-sidebar nav button.active svg{color:#17856b}
.admin-user{margin-top:auto;padding:16px 10px 0;display:grid;border-top:1px solid #e1e9e5}.admin-user>span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px;font-weight:650}.admin-user small{color:#71857d;font-size:11px}.admin-user button{display:flex;align-items:center;gap:7px;margin-top:13px;color:#637970;font-size:12px}
.admin-content{min-width:0;min-height:100vh}.admin-close,.admin-mobile-menu,.admin-scrim{display:none}
.admin-content .workspace-shell{min-height:100vh;display:block!important}.admin-content .workspace-sidebar,.admin-content .customer-admin-sidebar,.admin-content .customer-admin-scrim,.admin-content .workspace-overlay{display:none!important}.admin-content .workspace-main,.admin-content .customer-admin-main{min-width:0;min-height:100vh}.admin-content .customer-admin-shell{min-height:100vh;display:block!important}.admin-content .workspace-menu,.admin-content .customer-admin-menu{display:none!important}
@media(max-width:760px){.admin-layout{display:block}.admin-sidebar{position:fixed;left:0;top:0;width:260px;transform:translateX(-102%);transition:transform .2s ease;box-shadow:8px 0 26px #10251c1c}.admin-sidebar.open{transform:translateX(0)}.admin-close{width:34px;height:34px;display:grid;place-items:center;color:#536b61}.admin-mobile-menu{position:fixed;left:12px;top:16px;z-index:25;width:38px;height:38px;display:grid;place-items:center;border:1px solid #d8e3dd;border-radius:6px;background:#fff;color:#405f52;box-shadow:0 2px 8px #16342812}.admin-scrim{position:fixed;inset:0;z-index:39;display:block;background:#10231d66}.admin-content .workspace-header{padding-left:60px!important}.admin-content .customer-admin-header{padding-left:60px!important}}
@media(prefers-reduced-motion:reduce){.admin-sidebar{transition:none}}
</style>
