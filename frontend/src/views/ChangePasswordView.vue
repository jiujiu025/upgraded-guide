<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { changePassword, currentSessionUser } from '../api'
import { clearSession } from '../api/session'

const router = useRouter()
const form = reactive({ current_password: '', new_password: '', confirm_password: '' })
const busy = ref(false)
const error = ref('')
const done = ref(false)
const loginPath = ref('/login')

async function submit() {
  if (form.new_password !== form.confirm_password) { error.value = '两次输入的新密码不一致'; return }
  busy.value = true
  error.value = ''
  try {
    // 改密会清掉会话，跳转目标必须在登出前按角色算好，否则销售/管理员会被丢到客户端登录页。
    const role = currentSessionUser()?.role
    await changePassword({ current_password: form.current_password, new_password: form.new_password })
    loginPath.value = role === 'sales' ? '/sales/login' : ['admin', 'super_admin'].includes(role) ? '/admin/login' : '/login'
    clearSession()
    done.value = true
  } catch (requestError) { error.value = requestError.userMessage || '修改密码失败' }
  finally { busy.value = false }
}
</script>

<template>
  <main class="password-page">
    <form v-if="!done" class="password-form" @submit.prevent="submit">
      <h1>修改密码</h1>
      <label>当前密码<input v-model="form.current_password" type="password" autocomplete="current-password" required /></label>
      <label>新密码<input v-model="form.new_password" type="password" autocomplete="new-password" minlength="10" required /></label>
      <label>确认新密码<input v-model="form.confirm_password" type="password" autocomplete="new-password" minlength="10" required /></label>
      <p v-if="error" role="alert">{{ error }}</p>
      <button :disabled="busy">{{ busy ? '提交中' : '确认修改' }}</button>
    </form>
    <section v-else class="password-form"><h1>密码已修改</h1><p class="password-note">为安全起见，所有设备都需要重新登录。</p><button @click="router.replace(loginPath)">重新登录</button></section>
  </main>
</template>

<style scoped>
.password-page{min-height:100vh;display:grid;place-items:center;background:#f4f8f6;padding:20px;color:#203c34}.password-form{width:min(100%,400px);display:grid;gap:16px;padding:24px;border:1px solid #cddbd5;border-radius:6px;background:white}.password-form h1{font-size:22px;margin:0}.password-form label{display:grid;gap:6px;font-size:13px}.password-form input{height:40px;border:1px solid #b9cfc5;border-radius:4px;padding:0 10px}.password-form p{color:#a13c35;margin:0;font-size:12px}.password-form button{height:40px;background:#227f70;color:white;border:0;border-radius:4px;font-weight:700}.password-form button:disabled{opacity:.6}.password-note{color:#546b64!important;margin:0;font-size:12px}
</style>
