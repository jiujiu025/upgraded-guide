<script setup>
import { reactive, ref } from 'vue'
import { ArrowRight, Eye, EyeOff, LockKeyhole, Mail, Phone, UserRound } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import { clearSession, session } from '../api/session'
import { getAuthMe, getCustomerMe, login, register } from '../api'
import BrandLogo from './BrandLogo.vue'

const route = useRoute()
const router = useRouter()
const isRegister = ref(route.meta.section === 'register')
const account = ref('')
const password = ref('')
const registerForm = reactive({ username: '', password: '', confirmPassword: '', name: '', email: '', phone: '' })
const show = ref(false)
const busy = ref(false)
const error = ref('')
const roleHome = (role) => role === 'customer' ? '/client' : role === 'sales' ? '/sales' : ['admin', 'super_admin'].includes(role) ? '/admin' : '/'
const canOpen = (role, path) => (role === 'customer' && path.startsWith('/client')) || (role === 'sales' && path.startsWith('/sales')) || (['admin', 'super_admin'].includes(role) && path.startsWith('/admin')) || path === '/checkout'

async function submit() {
  if (isRegister.value) return submitRegister()
  if (!account.value || password.value.length < 8) { error.value = '请输入账号，并确保密码不少于 8 位'; return }
  error.value = ''
  busy.value = true
  try {
    const { data } = await login({ username: account.value.trim(), password: password.value })
    if (data.user.must_change_password) { await router.replace('/change-password'); return }
    const { data: verifiedUser } = await getAuthMe()
    if (verifiedUser.role === 'customer') await getCustomerMe()
    session.user = verifiedUser
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : ''
    await router.replace(redirect && canOpen(verifiedUser.role, redirect) ? redirect : roleHome(verifiedUser.role))
  } catch (requestError) {
    clearSession()
    error.value = requestError.userMessage || '登录失败，请稍后重试'
  } finally { busy.value = false }
}

async function establishCustomerSession() {
  const { data: verifiedUser } = await getAuthMe()
  if (verifiedUser.role !== 'customer') throw new Error('注册账号角色异常')
  await getCustomerMe()
  session.user = verifiedUser
  await router.replace('/client')
}

function validateRegister() {
  const username = registerForm.username.trim().toLowerCase()
  if (!/^[a-z0-9][a-z0-9_.-]{2,79}$/.test(username)) return '用户名需为 3-80 位小写字母、数字、点、下划线或短横线'
  if (registerForm.password.length < 10 || registerForm.password.length > 128) return '密码长度需为 10-128 位'
  if (!/[a-z]/.test(registerForm.password) || !/[A-Z]/.test(registerForm.password) || !/\d/.test(registerForm.password) || !/[^A-Za-z0-9]/.test(registerForm.password)) return '密码必须包含大小写字母、数字和特殊字符'
  if (registerForm.password !== registerForm.confirmPassword) return '两次输入的密码不一致'
  if (!registerForm.name.trim() || registerForm.name.trim().length > 120) return '请输入 1-120 个字符的客户名称'
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(registerForm.email.trim()) || registerForm.email.trim().length > 160) return '请输入有效的邮箱地址'
  if (registerForm.phone.trim().length > 40) return '联系电话不能超过 40 个字符'
  return ''
}

async function submitRegister() {
  const validationError = validateRegister()
  if (validationError) { error.value = validationError; return }
  error.value = ''
  busy.value = true
  try {
    const { data } = await register({
      username: registerForm.username.trim().toLowerCase(),
      password: registerForm.password,
      name: registerForm.name.trim(),
      email: registerForm.email.trim().toLowerCase(),
      phone: registerForm.phone.trim() || null,
    })
    await establishCustomerSession()
  } catch (requestError) {
    clearSession()
    error.value = requestError.userMessage || requestError.message || '注册失败，请检查填写内容后重试'
  } finally { busy.value = false }
}
</script>

<template>
  <section class="auth-page">
    <div class="auth-aside">
      <p class="eyebrow">CUSTOMER PORTAL</p>
      <h1>把每一次连接，<br /><span>留在自己的业务里。</span></h1>
      <p>登录玖玖IP客户中心，查看订单、线路交付和订阅信息。</p>
    </div>
    <form class="auth-card" :class="{ 'register-card': isRegister }" @submit.prevent="submit">
      <BrandLogo class="auth-brand-logo" full eager />
      <div class="auth-tabs">
        <button type="button" :class="{ active: !isRegister }" @click="isRegister = false; error = ''">登录</button>
        <button type="button" :class="{ active: isRegister }" @click="isRegister = true; error = ''">注册</button>
      </div>
      <template v-if="isRegister">
        <h2>创建客户账号</h2>
        <p class="auth-muted">注册后将自动登录并进入客户中心。</p>
        <div class="register-grid">
          <label class="field"><span>用户名</span><div class="input-wrap"><UserRound :size="17" /><input v-model="registerForm.username" autocomplete="username" maxlength="80" placeholder="小写字母、数字及 . _ -" required /></div></label>
          <label class="field"><span>客户名称</span><div class="input-wrap"><UserRound :size="17" /><input v-model="registerForm.name" autocomplete="organization" maxlength="120" placeholder="企业或个人名称" required /></div></label>
          <label class="field"><span>邮箱</span><div class="input-wrap"><Mail :size="17" /><input v-model="registerForm.email" type="email" autocomplete="email" maxlength="160" placeholder="name@example.com" required /></div></label>
          <label class="field"><span>联系电话（可选）</span><div class="input-wrap"><Phone :size="17" /><input v-model="registerForm.phone" autocomplete="tel" maxlength="40" placeholder="手机号或座机" /></div></label>
          <label class="field full-field"><span>密码</span><div class="input-wrap"><LockKeyhole :size="17" /><input v-model="registerForm.password" :type="show ? 'text' : 'password'" autocomplete="new-password" maxlength="128" placeholder="至少 10 位，包含四类字符" required /><button type="button" class="input-action" :aria-label="show ? '隐藏密码' : '显示密码'" @click="show = !show"><EyeOff v-if="show" :size="16" /><Eye v-else :size="16" /></button></div></label>
          <label class="field full-field"><span>确认密码</span><div class="input-wrap"><LockKeyhole :size="17" /><input v-model="registerForm.confirmPassword" :type="show ? 'text' : 'password'" autocomplete="new-password" maxlength="128" placeholder="再次输入密码" required /></div></label>
        </div>
        <p class="password-hint">密码须包含大写字母、小写字母、数字和特殊字符。</p>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <button class="button full-button" :disabled="busy" type="submit">{{ busy ? '正在创建账号…' : '注册并进入客户中心' }} <ArrowRight :size="16" /></button>
      </template>
      <template v-else>
        <h2>欢迎回来</h2>
        <p class="auth-muted">使用已绑定客户、销售或管理员账号登录。</p>
        <label class="field"><span>用户名</span><div class="input-wrap"><UserRound :size="17" /><input v-model="account" autocomplete="username" placeholder="请输入用户名" @keyup.enter="submit" /></div></label>
        <label class="field"><span>密码</span><div class="input-wrap"><LockKeyhole :size="17" /><input v-model="password" :type="show ? 'text' : 'password'" autocomplete="current-password" placeholder="至少 8 位" @keyup.enter="submit" /><button type="button" class="input-action" :aria-label="show ? '隐藏密码' : '显示密码'" @click="show = !show"><EyeOff v-if="show" :size="16" /><Eye v-else :size="16" /></button></div></label>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <button class="button full-button" :disabled="busy" type="submit">{{ busy ? '正在验证…' : '登录并继续' }} <ArrowRight :size="16" /></button>
      </template>
    </form>
  </section>
</template>

<style scoped>
.register-card { width: 100%; }
.register-grid .field, .input-wrap { min-width: 0; }
.input-wrap input { min-height: 0; padding: 0; }
</style>
