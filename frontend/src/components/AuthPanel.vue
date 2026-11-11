<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ArrowRight, Eye, EyeOff, LockKeyhole, Mail, Phone, Ticket, UserRound } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import { clearSession, session } from '../api/session'
import { getAuthMe, getCustomerMe, getInviteCodeCheck, login, logout as endSession, register } from '../api'
import BrandLogo from './BrandLogo.vue'
import ImageCaptcha from './ImageCaptcha.vue'

const props = defineProps({ portal: { type: String, default: 'customer' } })
const route = useRoute()
const router = useRouter()
const isRegister = ref(route.meta.section === 'register')
const account = ref('')
const password = ref('')
const registerForm = reactive({ username: '', password: '', confirmPassword: '', name: '', email: '', phone: '' })
// 销售邀请码：走谁的码注册，客户就归谁。URL 带 ?invite= 自动填入。
const inviteCode = ref(typeof route.query.invite === 'string' ? route.query.invite.trim().toUpperCase() : '')
const inviterName = ref('')
const inviteChecked = ref(false)
onMounted(async () => {
  if (!isRegister.value || !inviteCode.value) return
  try {
    const { data } = await getInviteCodeCheck(inviteCode.value)
    inviteChecked.value = true
    if (data.valid) inviterName.value = data.inviter_name || ''
  } catch {
    // 校验接口失败不阻塞注册：后端会在注册事务里再判一次。
  }
})
const show = ref(false)
const busy = ref(false)
const error = ref('')
// 图形验证码：后端强制校验，前端只负责把 token 与选中的格子原样交回去。
const captchaRef = ref(null)
const portalCopy = computed(() => ({
  customer: { eyebrow: 'CUSTOMER PORTAL', headline: '把每一次连接，', highlight: '留在自己的业务里。', description: '登录玖玖IP客户中心，查看订单、线路交付和订阅信息。', title: '欢迎回来', hint: '使用客户账号登录客户中心。' },
  admin: { eyebrow: 'ADMIN CONSOLE', headline: '运营管理，', highlight: '从专属入口开始。', description: '管理员账号请通过此入口进入运营工作台。', title: '管理员登录', hint: '仅管理员和超级管理员账号可进入。' },
  sales: { eyebrow: 'SALES WORKSPACE', headline: '客户服务，', highlight: '从专属入口开始。', description: '销售账号请通过此入口进入销售工作台。', title: '销售登录', hint: '仅销售账号可进入销售工作台。' },
})[props.portal] || {})
const roleHome = (role) => role === 'customer' ? '/client' : role === 'sales' ? '/sales' : ['admin', 'super_admin'].includes(role) ? '/admin' : '/'
// 登录后允许回到官网页面：客户从「登录查看价格」进来时带着 /products/... 的 redirect，
// 如果这里不放行，登录完会被扔到客户中心，等于白点一次。
const isSitePath = (path) => path.startsWith('/') && !path.startsWith('//') && !path.startsWith('/admin') && !path.startsWith('/sales') && !path.startsWith('/client')
const canOpen = (role, path) => (role === 'customer' && (path.startsWith('/client') || isSitePath(path))) || (role === 'sales' && path.startsWith('/sales')) || (['admin', 'super_admin'].includes(role) && path.startsWith('/admin')) || path === '/checkout'
const roleMatchesPortal = (role) => props.portal === 'customer' ? role === 'customer' : props.portal === 'sales' ? role === 'sales' : ['admin', 'super_admin'].includes(role)

async function submit() {
  if (isRegister.value) return submitRegister()
  if (!account.value || password.value.length < 8) { error.value = '请输入账号，并确保密码不少于 8 位'; return }
  error.value = ''
  busy.value = true
  try {
    const { data } = await login({ username: account.value.trim(), password: password.value })
    if (!roleMatchesPortal(data.user.role)) {
      try { await endSession() } catch { clearSession() }
      error.value = '用户名或密码错误'
      return
    }
    const { data: verifiedUser } = await getAuthMe()
    if (verifiedUser.role === 'customer') await getCustomerMe()
    session.user = verifiedUser
    // 管理员重置过密码 / 首次登录：业务接口会被后端 428 拦住，先来改密。
    if (verifiedUser.must_change_password) {
      await router.replace('/change-password')
      return
    }
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
  const code = inviteCode.value.trim().toUpperCase()
  if (code && !/^[0-9A-Z]{4,12}$/.test(code)) return '邀请码格式不正确（4-12 位数字或大写字母）'
  return ''
}

async function submitRegister() {
  const validationError = validateRegister()
  if (validationError) { error.value = validationError; return }
  const captcha = captchaRef.value?.answer()
  if (!captcha) { error.value = '请先点选图片完成安全验证'; return }
  error.value = ''
  busy.value = true
  try {
    const { data } = await register({
      username: registerForm.username.trim().toLowerCase(),
      password: registerForm.password,
      name: registerForm.name.trim(),
      email: registerForm.email.trim().toLowerCase(),
      phone: registerForm.phone.trim() || null,
      invite_code: inviteCode.value.trim().toUpperCase() || null,
      captcha_token: captcha.token,
      captcha_selection: captcha.selection,
    })
    await establishCustomerSession()
  } catch (requestError) {
    clearSession()
    error.value = requestError.userMessage || requestError.message || '注册失败，请检查填写内容后重试'
    // 验证码是一次性的：这道题在服务端已经用掉或作废了，换一题再试。
    captchaRef.value?.refresh()
  } finally { busy.value = false }
}
</script>

<template>
  <section class="auth-page" :class="{ 'staff-auth-page': portal !== 'customer' }">
    <div class="auth-aside">
      <p class="eyebrow">{{ portalCopy.eyebrow }}</p>
      <h1>{{ portalCopy.headline }}<br /><span>{{ portalCopy.highlight }}</span></h1>
      <p>{{ portalCopy.description }}</p>
    </div>
    <form class="auth-card" :class="{ 'register-card': isRegister }" @submit.prevent="submit">
      <BrandLogo class="auth-brand-logo" full eager />
      <div v-if="portal === 'customer'" class="auth-tabs">
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
          <label class="field full-field"><span>邀请码（可选）</span><div class="input-wrap"><Ticket :size="17" /><input v-model="inviteCode" maxlength="12" placeholder="销售提供的邀请码，如从专属链接进入则自动填写" /></div></label>
          <p v-if="inviterName" class="invite-hint">邀请人：{{ inviterName }}，注册后由 TA 为您服务。</p>
          <p v-else-if="inviteChecked && inviteCode" class="invite-hint invite-hint-muted">邀请码已失效，仍可正常注册，稍后由管理员为您分配服务人员。</p>
          <label class="field full-field"><span>密码</span><div class="input-wrap"><LockKeyhole :size="17" /><input v-model="registerForm.password" :type="show ? 'text' : 'password'" autocomplete="new-password" maxlength="128" placeholder="至少 10 位，包含四类字符" required /><button type="button" class="input-action" :aria-label="show ? '隐藏密码' : '显示密码'" @click="show = !show"><EyeOff v-if="show" :size="16" /><Eye v-else :size="16" /></button></div></label>
          <label class="field full-field"><span>确认密码</span><div class="input-wrap"><LockKeyhole :size="17" /><input v-model="registerForm.confirmPassword" :type="show ? 'text' : 'password'" autocomplete="new-password" maxlength="128" placeholder="再次输入密码" required /></div></label>
          <div class="captcha-field">
            <ImageCaptcha ref="captchaRef" />
          </div>
        </div>
        <p class="password-hint">密码须包含大写字母、小写字母、数字和特殊字符。</p>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <button class="button full-button" :disabled="busy" type="submit">{{ busy ? '正在创建账号…' : '注册并进入客户中心' }} <ArrowRight :size="16" /></button>
      </template>
      <template v-else>
        <h2>{{ portalCopy.title }}</h2>
        <p class="auth-muted">{{ portalCopy.hint }}</p>
        <label class="field"><span>用户名</span><div class="input-wrap"><UserRound :size="17" /><input v-model="account" autocomplete="username" placeholder="请输入用户名" @keyup.enter="submit" /></div></label>
        <label class="field"><span>密码</span><div class="input-wrap"><LockKeyhole :size="17" /><input v-model="password" :type="show ? 'text' : 'password'" autocomplete="current-password" placeholder="至少 8 位" @keyup.enter="submit" /><button type="button" class="input-action" :aria-label="show ? '隐藏密码' : '显示密码'" @click="show = !show"><EyeOff v-if="show" :size="16" /><Eye v-else :size="16" /></button></div></label>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <button class="button full-button" :disabled="busy" type="submit">{{ busy ? '正在验证…' : '登录并继续' }} <ArrowRight :size="16" /></button>
        <button class="recovery-link" type="button" @click="router.push({ path: '/recover-password', query: { portal } })">忘记密码？通过密保问题找回</button>
      </template>
    </form>
  </section>
</template>

<style scoped>
.register-card { width: 100%; }
.register-grid .field, .input-wrap { min-width: 0; }
.captcha-field { grid-column: 1 / -1; margin-top: 4px; }
.input-wrap input { min-height: 0; padding: 0; }
.recovery-link { justify-self: end; color: #397e73; font-size: 12px; }
.invite-hint { margin: 10px 0 0; font-size: 12px; color: #2f7a6d; }
.invite-hint-muted { color: #8a7355; }
</style>
