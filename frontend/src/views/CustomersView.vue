<template><section class="page"><div class="toolbar"><div><p class="eyebrow">客户资产 / 归属清晰</p><h2>客户 360</h2></div><button class="primary small" @click="show = !show">{{ show ? '收起表单' : '新增客户' }}</button></div><form v-if="show" class="inline-form" @submit.prevent="add"><input v-model="form.name" placeholder="客户名称" required /><input v-model="form.email" type="email" placeholder="邮箱" required /><select v-model="form.level"><option value="standard">标准客户</option><option value="vip">重点客户</option></select><button class="primary small">保存</button></form><div class="table-panel"><div class="table-head"><span>客户</span><span>生命周期</span><span>等级</span><span>负责人</span><span>余额</span></div><div v-for="customer in customers" :key="customer.id" class="table-row"><div><strong>{{ customer.name }}</strong><small>{{ customer.email }}</small></div><span class="status active">{{ customer.status }}</span><span>{{ customer.level === 'vip' ? '重点客户' : '标准客户' }}</span><span>—</span><strong>¥{{ customer.balance.toFixed(2) }}</strong></div><div v-if="!customers.length" class="empty">暂无可见客户</div></div></section></template>
<script setup>
import { ref, reactive, onMounted } from 'vue'
import { getCustomers, createCustomer } from '../api'
const customers = ref([]); const show = ref(false); const form = reactive({ name: '', email: '', level: 'standard' })
async function load () { customers.value = (await getCustomers()).data }
async function add () { await createCustomer(form); form.name = ''; form.email = ''; show.value = false; await load() }
onMounted(load)
</script>
