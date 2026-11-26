<script setup>
// 模拟态顶部横幅：必须一直看得见，否则管理员会忘了自己在别人账号里。
// 到点自动退出（服务端也会过期，这里是第二道），退出后回后台登录页。
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ShieldAlert } from 'lucide-vue-next'
import { logoutImpersonation } from '../api'
import { clearImpersonation, impersonation, impersonationActive } from '../api/session'

const now = ref(Date.now())
let timer = null
let finishing = false

const remaining = computed(() => Math.max(0, Math.floor((impersonation.expiresAt - now.value) / 1000)))
const clock = computed(() => {
  const minutes = String(Math.floor(remaining.value / 60)).padStart(2, '0')
  const seconds = String(remaining.value % 60).padStart(2, '0')
  return `${minutes}:${seconds}`
})
const urgent = computed(() => remaining.value <= 60)

async function finish() {
  if (finishing) return
  finishing = true
  if (timer) clearInterval(timer)
  document.body.classList.remove('impersonation-on')
  try {
    await logoutImpersonation()
  } catch { /* 已过期时后端会 401，本地清理即可 */ }
  clearImpersonation()
  window.location.replace('/admin/login?imp=ended')
}

onMounted(() => {
  document.body.classList.add('impersonation-on')
  timer = setInterval(() => {
    now.value = Date.now()
    if (remaining.value <= 0) finish()
  }, 1000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
  document.body.classList.remove('impersonation-on')
})
</script>

<template>
  <div v-if="impersonationActive()" class="imp-bar" role="status">
    <ShieldAlert :size="15" />
    <span class="imp-text">
      正在以 <b>{{ impersonation.targetName || impersonation.user?.display_name }}</b>
      （{{ impersonation.user?.role === 'sales' ? '销售' : '客户' }}）身份查看，
      所有操作记在 <b>{{ impersonation.actorName || '超级管理员' }}</b> 名下
    </span>
    <span class="imp-clock" :class="{ urgent }">剩余 {{ clock }}</span>
    <button type="button" class="imp-exit" @click="finish">退出模拟</button>
  </div>
</template>

<style scoped>
.imp-bar{
  position:fixed;left:0;right:0;top:0;z-index:9999;
  display:flex;align-items:center;gap:10px;
  min-height:44px;padding:0 18px;
  background:#2b2a26;color:#f7f3ec;font-size:12px;
  box-shadow:0 1px 0 #0000001a;
}
.imp-text{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.imp-text b{font-weight:700;color:#ffd7a8}
.imp-clock{
  flex:none;padding:3px 9px;border-radius:99px;
  background:#3d3a33;color:#ffd7a8;font-size:11px;font-weight:700;
  font-variant-numeric:tabular-nums;
}
.imp-clock.urgent{background:#6b2b22;color:#ffd9d2}
.imp-exit{
  flex:none;min-height:28px;padding:0 12px;border-radius:5px;
  background:#f0e6d6;color:#3b3327;font-size:11px;font-weight:700;
}
.imp-exit:hover{background:#fff}
:global(body.impersonation-on){padding-top:44px}
@media(max-width:640px){
  .imp-bar{padding:0 12px;gap:8px}
  .imp-text{font-size:11px}
}
</style>
