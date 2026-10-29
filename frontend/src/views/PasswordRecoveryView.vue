<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, KeyRound } from 'lucide-vue-next'
import BrandLogo from '../components/BrandLogo.vue'
import { recoverPassword } from '../api'

const router = useRouter()
const route = useRoute()
const busy = ref(false)
const error = ref('')
const submitted = ref(false)
const form = reactive({
  username: '',
  question_1: '',
  answer_1: '',
  question_2: '',
  answer_2: '',
  new_password: '',
  confirm_password: '',
})
const questionOptions = [
  ['childhood_friend', '您童年最好的朋友叫什么？'],
  ['birth_city', '您出生的城市是哪里？'],
  ['first_school', '您上的第一所学校叫什么？'],
  ['favorite_teacher', '您最喜欢的老师叫什么？'],
  ['childhood_nickname', '您小时候的昵称是什么？'],
  ['first_job', '您的第一份工作是什么？'],
  ['favorite_book', '您最喜欢的一本书是什么？'],
  ['favorite_movie', '您最喜欢的一部电影是什么？'],
]
const loginPath = () => route.query.portal === 'admin' ? '/admin/login' : route.query.portal === 'sales' ? '/sales/login' : '/login'

async function submit() {
  error.value = ''
  if (form.question_1 === form.question_2) {
    error.value = '请选择两个不同的密保问题'
    return
  }
  if (form.new_password !== form.confirm_password) {
    error.value = '两次输入的新密码不一致'
    return
  }
  if (!/[a-z]/.test(form.new_password) || !/[A-Z]/.test(form.new_password) || !/\d/.test(form.new_password) || !/[^A-Za-z0-9]/.test(form.new_password)) {
    error.value = '新密码需包含大小写字母、数字和特殊字符'
    return
  }
  busy.value = true
  try {
    await recoverPassword({
      username: form.username.trim(),
      question_1: form.question_1,
      answer_1: form.answer_1,
      question_2: form.question_2,
      answer_2: form.answer_2,
      new_password: form.new_password,
    })
    submitted.value = true
    form.answer_1 = ''
    form.answer_2 = ''
    form.new_password = ''
    form.confirm_password = ''
  } catch (requestError) {
    error.value = requestError.userMessage || '暂时无法处理，请稍后重试'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="recovery-page">
    <section class="recovery-card">
      <BrandLogo full eager />
      <template v-if="!submitted">
        <div class="recovery-title"><KeyRound :size="20" /><div><h1>找回密码</h1><p>使用账号已设置的两项密保问题重置密码，问题顺序不限</p></div></div>
        <form class="recovery-form" @submit.prevent="submit">
          <label><span>登录用户名</span><input v-model="form.username" autocomplete="username" minlength="3" maxlength="80" required /></label>
          <label><span>第一道密保问题</span><select v-model="form.question_1" required><option value="" disabled>选择问题</option><option v-for="[id, label] in questionOptions" :key="id" :value="id">{{ label }}</option></select></label>
          <label><span>第一题答案</span><input v-model="form.answer_1" type="password" autocomplete="off" maxlength="128" required /></label>
          <label><span>第二道密保问题</span><select v-model="form.question_2" required><option value="" disabled>选择与第一题不同的问题</option><option v-for="[id, label] in questionOptions" :key="id" :value="id" :disabled="id === form.question_1">{{ label }}</option></select></label>
          <label><span>第二题答案</span><input v-model="form.answer_2" type="password" autocomplete="off" maxlength="128" required /></label>
          <label><span>新密码</span><input v-model="form.new_password" type="password" autocomplete="new-password" minlength="10" maxlength="128" required /><small>至少 10 位，包含大小写字母、数字和特殊字符</small></label>
          <label><span>确认新密码</span><input v-model="form.confirm_password" type="password" autocomplete="new-password" minlength="10" maxlength="128" required /></label>
          <p class="recovery-warning">密保答案需与设置时一致。为保护账号隐私，页面不会提示用户名或答案是否正确。</p>
          <p v-if="error" class="recovery-error" role="alert">{{ error }}</p>
          <button class="recovery-submit" type="submit" :disabled="busy">{{ busy ? '正在验证…' : '验证并重置密码' }}</button>
        </form>
      </template>
      <section v-else class="recovery-result">
        <h1>请求已处理</h1>
        <p>如账号和密保信息匹配，密码已重置。请使用新密码登录；若无法登录，请联系管理员。</p>
        <button class="recovery-submit" @click="router.replace(loginPath())"><ArrowLeft :size="16" />返回登录</button>
      </section>
    </section>
  </main>
</template>

<style scoped>
.recovery-page{min-height:100vh;display:grid;place-items:center;padding:24px;background:#edf2f0;color:#1d302c}
.recovery-card{width:min(100%,520px);padding:24px;background:#f9fbfa;border:1px solid #d8e2df;border-radius:6px}
.recovery-title{display:flex;align-items:flex-start;gap:12px;margin:24px 0 16px;color:#267b6d}
.recovery-title h1,.recovery-result h1{margin:0;color:#1d302c;font-size:21px}
.recovery-title p,.recovery-result p{margin:4px 0 0;color:#6e817b;font-size:12px;line-height:1.6}
.recovery-form{display:grid;gap:13px}
.recovery-form label{display:grid;gap:5px;color:#526862;font-size:12px;font-weight:650}
.recovery-form input,.recovery-form select{width:100%;min-height:40px;padding:7px 10px;border:1px solid #c8d6d2;border-radius:5px;background:#fff;color:#263b37}
.recovery-form small,.recovery-warning{color:#71847e;font-size:11px;font-weight:400;line-height:1.6}
.recovery-warning{margin:0;padding:10px;background:#fff3de;border-radius:5px}
.recovery-error{margin:0;color:#a94432;font-size:12px}
.recovery-submit{min-height:40px;display:inline-flex;align-items:center;justify-content:center;gap:7px;padding:8px 13px;border-radius:5px;background:#227f70;color:#fff;font-size:12px;font-weight:700}
.recovery-submit:disabled{opacity:.55;cursor:not-allowed}
.recovery-result{display:grid;gap:12px;margin-top:24px}
@media(max-width:520px){.recovery-page{padding:12px}.recovery-card{padding:18px}}
</style>
