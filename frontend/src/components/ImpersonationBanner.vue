<script setup>
// 模拟态顶部横幅：必须一直看得见，否则管理员会忘了自己在别人账号里。
// 不再有倒计时——模拟不预设时长，什么时候退出由人决定；横幅本身就是提醒。
import { onMounted, onUnmounted } from 'vue'
import { ShieldAlert } from 'lucide-vue-next'
import { logoutImpersonation } from '../api'
import { clearImpersonation, impersonation, impersonationActive } from '../api/session'

let finishing = false

async function finish() {
  if (finishing) return
  finishing = true
  document.body.classList.remove('impersonation-on')
  try {
    await logoutImpersonation()
  } catch { /* 已过期时后端会 401，本地清理即可 */ }
  clearImpersonation()
  window.location.replace('/admin/login?imp=ended')
}

onMounted(() => {
  document.body.classList.add('impersonation-on')
})

onUnmounted(() => {
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
    <span class="imp-state">不限时长 · 由你手动退出</span>
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
.imp-state{
  flex:none;padding:3px 9px;border-radius:99px;
  background:#3d3a33;color:#c9bfa9;font-size:11px;font-weight:600;white-space:nowrap;
}
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
