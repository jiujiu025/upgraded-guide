<template><main class="login"><section class="login-copy"><span class="eyebrow">99 CLOUD / OPS</span><h1>把线路风险，<em>变成下一步动作。</em></h1><p>统一管理客户、订单、线路交付和健康状态，让运营团队在一个工作台内完成判断与处理。</p><div class="login-stats"><span><b>4</b>角色隔离</span><span><b>24/7</b>健康监测</span><span><b>1</b>运营视图</span></div></section><form class="login-form" @submit.prevent="submit"><div class="form-head"><span>安全登录</span><small>运营中台 v0.1</small></div><label>账号<input v-model="form.username" autocomplete="username" required /></label><label>密码<input v-model="form.password" type="password" autocomplete="current-password" required /></label><p v-if="error" class="error">{{ error }}</p><button class="primary" :disabled="loading">{{ loading ? '验证中…' : '进入工作台' }} <span>→</span></button><small class="hint">演示账号：admin / ChangeMe_123!</small></form></main></template>
<script setup>
import { reactive, ref } from 'vue'
import { login } from '../api'
const form = reactive({ username: 'admin', password: 'ChangeMe_123!' }); const loading = ref(false); const error = ref('')
async function submit () { loading.value = true; error.value = ''; try { await login(form); location.href = '/' } catch (e) { error.value = e.response?.data?.detail || '登录失败，请检查服务状态' } finally { loading.value = false } }
</script>
