<script setup>
import BrandLogo from '../components/BrandLogo.vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Activity, ArrowLeft, ArrowDownToLine, BadgeCheck, ChevronLeft, ChevronRight,
  CircleDollarSign, ClipboardList, CreditCard, FileClock, LogOut, Menu,
  MessageSquare, Package, Pencil, RefreshCw, Search, ShieldCheck, UserPlus, UserRound, Users, X,
} from 'lucide-vue-next'
import { getAdminCustomer, getAdminCustomerRecords, getAdminCustomers, getAdminSalesOptions, createOfflineRecharge, updateAdminCustomer, logout as endSession } from '../api'
import { session } from '../api/session'
import { getAdminNavigation } from '../navigation/adminNavigation'

const route = useRoute()
const router = useRouter()
const detailId = computed(() => route.params.id ? Number(route.params.id) : null)
const pageTitle = computed(() => detailId.value ? '客户资料' : '客户管理')
const mobileNav = ref(false)
const loading = ref(false)
const saving = ref(false)
let listRequestId = 0
let detailRequestId = 0
const error = ref('')
const notice = ref('')
const customers = ref([])
const total = ref(0)
const salesOptions = ref([])
const detail = ref(null)
const recordLoading = reactive({ lines: false, transactions: false, audit_logs: false })
const activeTab = ref('overview')
const editing = ref(false)
const page = ref(1)
const pageSize = 25
const filters = reactive({ keyword: '', sales_id: '', status: '' })
const editForm = reactive({ name: '', email: '', phone: '', status: '', level: '', sales_id: '' })
const lineFilters = reactive({ keyword: '', expiry: 'all' })
const rechargeTarget = ref(null)
const rechargeError = ref('')
const rechargeAmount = ref('')
const rechargeRemark = ref('')
const rechargeKey = ref('')
const statuses = ['潜在客户', '跟进中', '成交客户', '暂停合作']
const nav = computed(() => getAdminNavigation(session.user?.role))
const tabs = [
  ['overview', '客户资料', UserRound],
  ['lines', '订阅与线路', Package],
  ['transactions', '交易流水', CreditCard],
  ['logs', '操作日志', FileClock],
]
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const balanceLabel = (cents) => `¥${(Number(cents || 0) / 100).toFixed(2)}`
const dateLabel = (value) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
const lineStatus = (value, expiresAt) => value === 'active' && expiresAt && Date.parse(expiresAt) <= Date.now()
  ? '已到期'
  : ({ active: '使用中', expired: '已到期', disabled: '已停用', deleted: '已删除' })[value] || value || '—'
const lineTone = (line) => line.status === 'active' && line.expires_at && Date.parse(line.expires_at) <= Date.now() ? 'expired' : line.status
const transactionType = (value) => ({
  offline_recharge: '线下充值', online_recharge: '在线充值', wallet_recharge: '账户充值',
  order_payment: '订单支付', balance_payment: '余额支付', order_refund: '订单退款', refund: '退款',
  commission: '佣金', adjustment: '余额调整',
})[value] || value || '资金记录'
const channelLabel = (value) => ({ wallet: '钱包', balance: '余额', online: '在线支付', offline: '线下', alipay: '支付宝', wxpay: '微信支付' })[value] || value || '—'
const actionLabel = (value) => ({ update_customer: '编辑客户资料', assign_sales: '调整业务归属', offline_recharge: '线下充值', create: '创建记录', deliver: '交付线路', cancel: '取消订单', refund: '登记退款', reset_password: '重置密码', unlock_login: '解除登录锁定' })[value] || value || '管理操作'
const paymentStatus = (value) => ({ paid: '已完成', completed: '已完成', pending: '待支付', failed: '失败', refunded: '已退款', manual_review: '待核查' })[value] || value || '—'
const makeKey = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`

function showNotice(message) {
  notice.value = message
  window.clearTimeout(showNotice.timer)
  showNotice.timer = window.setTimeout(() => { notice.value = '' }, 3200)
}

async function loadList() {
  const requestId = ++listRequestId
  ++detailRequestId
  loading.value = true
  error.value = ''
  try {
    const params = { page: page.value, page_size: pageSize }
    if (filters.keyword.trim()) params.keyword = filters.keyword.trim()
    if (filters.sales_id) params.sales_id = Number(filters.sales_id)
    if (filters.status) params.status = filters.status
    const { data } = await getAdminCustomers(params)
    if (requestId !== listRequestId || detailId.value) return
    customers.value = data.items
    total.value = data.total
  } catch (err) {
    if (requestId === listRequestId && !detailId.value) error.value = err.userMessage || '客户列表加载失败，请重试。'
  } finally {
    if (requestId === listRequestId) loading.value = false
  }
}

async function loadDetail() {
  const customerId = detailId.value
  if (!customerId) return
  const requestId = ++detailRequestId
  ++listRequestId
  loading.value = true
  error.value = ''
  detail.value = null
  recordLoading.lines = false
  recordLoading.transactions = false
  recordLoading.audit_logs = false
  lineFilters.keyword = ''
  lineFilters.expiry = 'all'
  try {
    const summary = (await getAdminCustomer(customerId)).data
    const [lines, transactions, auditLogs] = await Promise.all([
      getAdminCustomerRecords(customerId, 'lines'),
      getAdminCustomerRecords(customerId, 'transactions'),
      getAdminCustomerRecords(customerId, 'audit_logs'),
    ])
    if (requestId !== detailRequestId || detailId.value !== customerId) return
    detail.value = {
      ...summary,
      lines: lines.data.items,
      transactions: transactions.data.items,
      audit_logs: auditLogs.data.items,
      all_line_count: summary.record_totals.lines,
      active_line_count: summary.record_totals.active_lines,
    }
    const customer = detail.value.customer
    Object.assign(editForm, {
      name: customer.name || '', email: customer.email || '', phone: customer.phone || '',
      status: customer.status || '潜在客户', level: customer.level || 'standard',
      sales_id: customer.sales_id == null ? '' : String(customer.sales_id),
    })
    editing.value = route.query.edit === '1'
  } catch (err) {
    if (requestId === detailRequestId && detailId.value === customerId) error.value = err.userMessage || '客户详情加载失败，请重试。'
  } finally {
    if (requestId === detailRequestId) loading.value = false
  }
}

async function loadMoreRecords(kind) {
  if (!detail.value || recordLoading[kind]) return
  const customerId = detailId.value
  const currentDetail = detail.value
  recordLoading[kind] = true
  try {
    const filters = kind === 'lines' ? { keyword: lineFilters.keyword.trim() || undefined, expiry: lineFilters.expiry } : {}
    const { data } = await getAdminCustomerRecords(customerId, kind, currentDetail[kind].length, filters)
    if (detailId.value === customerId && detail.value === currentDetail) currentDetail[kind].push(...data.items)
  } catch (err) {
    if (detailId.value === customerId && detail.value === currentDetail) error.value = err.userMessage || '历史记录加载失败，请重试。'
  } finally {
    if (detailId.value === customerId && detail.value === currentDetail) recordLoading[kind] = false
  }
}

async function applyLineFilters() {
  if (!detail.value || recordLoading.lines) return
  const customerId = detailId.value
  const currentDetail = detail.value
  recordLoading.lines = true
  try {
    const filters = { keyword: lineFilters.keyword.trim() || undefined, expiry: lineFilters.expiry }
    const { data } = await getAdminCustomerRecords(customerId, 'lines', 0, filters)
    if (detailId.value === customerId && detail.value === currentDetail) {
      currentDetail.lines = data.items
      currentDetail.record_totals.lines = data.total
    }
  } catch (err) {
    if (detailId.value === customerId && detail.value === currentDetail) error.value = err.userMessage || '线路筛选失败，请重试。'
  } finally {
    if (detailId.value === customerId && detail.value === currentDetail) recordLoading.lines = false
  }
}

function recordTotal(kind) {
  return detail.value?.record_totals?.[kind] ?? detail.value?.[kind]?.length ?? 0
}

async function load() {
  if (detailId.value) await loadDetail()
  else await loadList()
}

async function loadSales() {
  try { salesOptions.value = (await getAdminSalesOptions()).data }
  catch (err) { error.value = err.userMessage || '销售列表暂时无法加载。' }
}

async function applyFilters() {
  page.value = 1
  await loadList()
}

async function changePage(next) {
  page.value = Math.min(totalPages.value, Math.max(1, next))
  await loadList()
}

function openDetail(customer, edit = false) {
  router.push({ path: `/admin/customers/${customer.id}`, query: edit ? { edit: '1' } : {} })
}

function startEdit() {
  editing.value = true
  activeTab.value = 'overview'
}

async function cancelEdit() {
  editing.value = false
  const cleanQuery = { ...route.query }
  delete cleanQuery.edit
  await router.replace({ path: route.path, query: cleanQuery })
}

async function saveCustomer() {
  if (!detail.value) return
  saving.value = true
  error.value = ''
  let persisted = false
  try {
    const payload = {
      name: editForm.name.trim(), email: editForm.email.trim(),
      phone: editForm.phone.trim() || null, status: editForm.status.trim(),
      level: editForm.level.trim(), sales_id: editForm.sales_id ? Number(editForm.sales_id) : null,
    }
    await updateAdminCustomer(detail.value.customer.id, payload)
    persisted = true
    editing.value = false
    const cleanQuery = { ...route.query }
    delete cleanQuery.edit
    await router.replace({ path: route.path, query: cleanQuery })
    showNotice('客户资料已保存')
    await loadDetail()
  } catch (err) {
    if (persisted) {
      showNotice('客户资料已保存')
      error.value = '客户资料已保存，但页面刷新失败，请重新加载详情。'
    } else error.value = err.userMessage || '保存失败，请检查资料后重试。'
  }
  finally { saving.value = false }
}

function openRecharge(customer) {
  if (!customer.user_id || customer.account_active === false) {
    error.value = customer.user_id ? '该客户登录账号已停用，当前不能执行钱包充值。' : '该客户档案尚未绑定登录账号，无法充值到钱包。'
    return
  }
  rechargeTarget.value = customer
  rechargeError.value = ''
  rechargeAmount.value = ''
  rechargeRemark.value = ''
  rechargeKey.value = makeKey()
}

async function submitRecharge() {
  if (saving.value || !rechargeTarget.value) return
  const match = String(rechargeAmount.value).trim().match(/^(\d{1,6})(?:\.(\d{1,2}))?$/)
  const amountCents = match ? Number(match[1]) * 100 + Number((match[2] || '').padEnd(2, '0')) : 0
  if (amountCents < 1 || amountCents > 10000000) {
    rechargeError.value = '充值金额需在 ¥0.01 至 ¥100,000.00 之间。'
    return
  }
  if (rechargeRemark.value.trim().length < 2) {
    rechargeError.value = '请填写至少 2 个字符的到账凭证或经办说明。'
    return
  }
  saving.value = true
  rechargeError.value = ''
  try {
    await createOfflineRecharge({
      user_id: rechargeTarget.value.user_id,
      amount_cents: amountCents,
      idempotency_key: rechargeKey.value,
      remark: rechargeRemark.value.trim(),
    })
    rechargeTarget.value = null
    showNotice('线下充值已入账，账务流水已记录')
    await load()
  } catch (err) { rechargeError.value = err.userMessage || '充值未完成，请核实账务后重试。' }
  finally { saving.value = false }
}

function cancelRecharge() {
  if (!saving.value) { rechargeTarget.value = null; rechargeError.value = '' }
}

async function logout() {
  await endSession()
  router.replace('/admin/login')
}

watch(() => route.params.id, load)
onMounted(async () => {
  await Promise.all([loadSales(), load()])
})
</script>

<template>
  <div class="customer-admin-shell">
    <aside class="customer-admin-sidebar" :class="{ open: mobileNav }">
      <div class="customer-admin-brand"><BrandLogo eager /><button aria-label="关闭导航" @click="mobileNav = false"><X :size="19" /></button></div>
      <p class="customer-admin-role">管理后台</p>
      <nav aria-label="管理导航">
        <button v-for="[label, path, icon] in nav" :key="path" class="nav-link" :class="{ active: route.path === path || (path !== '/admin' && route.path.startsWith(`${path}/`)) }" :aria-current="route.path === path || (path !== '/admin' && route.path.startsWith(`${path}/`)) ? 'page' : undefined" @click="router.push(path); mobileNav = false"><component :is="icon" :size="17" />{{ label }}</button>
      </nav>
      <div class="customer-admin-user"><span>{{ session.user?.display_name || session.user?.username }}</span><small>{{ session.user?.role }}</small><button @click="logout"><LogOut :size="15" />退出登录</button></div>
    </aside>
    <button v-if="mobileNav" class="customer-admin-scrim" aria-label="关闭导航" @click="mobileNav = false"></button>
    <div class="customer-admin-main">
      <header class="customer-admin-header">
        <button class="customer-admin-menu" aria-label="打开导航" @click="mobileNav = true"><Menu :size="20" /></button>
        <div><h1>{{ pageTitle }}</h1><p>{{ detailId ? '客户资料、账务和订阅集中查看' : '按业务归属和客户状态管理账户' }}</p></div>
        <button class="icon-refresh" aria-label="刷新数据" title="刷新" :disabled="loading" @click="load"><RefreshCw :size="17" /></button>
      </header>
      <main class="customer-admin-content">
        <div v-if="notice" class="customer-notice" role="status"><BadgeCheck :size="16" />{{ notice }}</div>
        <div v-if="error" class="customer-error" role="alert"><span>{{ error }}</span><button aria-label="关闭提示" @click="error = ''"><X :size="16" /></button></div>

        <template v-if="!detailId">
          <section class="customer-toolbar">
            <div><div class="customer-heading-row"><Users :size="19" /><h2>客户档案</h2><span class="count-label">{{ total }} 位客户</span></div><p>搜索客户、检查负责人和账户状态；余额变动会记录到财务账本。</p></div>
            <button class="refresh-text" :disabled="loading" @click="load"><RefreshCw :size="15" />刷新列表</button>
          </section>
          <form class="customer-filters" @submit.prevent="applyFilters">
            <label class="keyword-filter"><span>关键词</span><div><Search :size="16" /><input v-model="filters.keyword" maxlength="120" placeholder="姓名、手机号、邮箱或登录名" /><button type="submit">查询</button></div></label>
            <label><span>业务归属</span><select v-model="filters.sales_id"><option value="">全部销售</option><option v-for="item in salesOptions" :key="item.id" :value="String(item.id)">{{ item.display_name }} · {{ item.username }}{{ item.is_active ? '' : '（停用）' }}</option></select></label>
            <label><span>客户状态</span><select v-model="filters.status"><option value="">全部状态</option><option v-for="item in statuses" :key="item" :value="item">{{ item }}</option></select></label>
            <button class="filter-reset" type="button" @click="filters.keyword = ''; filters.sales_id = ''; filters.status = ''; applyFilters()">重置</button>
          </form>
          <section class="customer-table-section" aria-label="客户列表">
            <div v-if="loading" class="customer-loading"><span></span><span></span><span></span><p>正在读取客户档案…</p></div>
            <div v-else-if="customers.length" class="customer-table-wrap">
              <table><thead><tr><th>客户</th><th>业务归属</th><th>状态</th><th>账户余额</th><th>订阅</th><th>创建时间</th><th class="action-column">操作</th></tr></thead>
                <tbody><tr v-for="customer in customers" :key="customer.id" @dblclick="openDetail(customer)">
                  <td><button class="customer-name" @click="openDetail(customer)">{{ customer.name }}</button><small>{{ customer.phone || customer.email || '未填写联系方式' }}<template v-if="customer.username"> · {{ customer.username }}</template></small></td>
                  <td><span>{{ customer.sales_name || '未分配' }}</span><small v-if="customer.sales_username">{{ customer.sales_username }}</small></td>
                  <td><span class="customer-status" :data-status="customer.status">{{ customer.status }}</span></td>
                  <td class="money-cell">{{ balanceLabel(customer.balance_cents) }}</td>
                  <td>{{ customer.line_count }} 条</td>
                  <td>{{ dateLabel(customer.created_at) }}</td>
                  <td class="row-actions"><button title="查看客户详情" aria-label="查看客户详情" @click="openDetail(customer)"><UserRound :size="16" /></button><button title="编辑客户资料" aria-label="编辑客户资料" @click="openDetail(customer, true)"><Pencil :size="15" /></button><button :title="customer.account_active === false ? '账号已停用，无法充值' : '线下充值'" aria-label="线下充值" :disabled="!customer.user_id || customer.account_active === false" @click="openRecharge(customer)"><ArrowDownToLine :size="16" /></button></td>
                </tr></tbody>
              </table>
            </div>
            <div v-else class="customer-empty"><Users :size="24" /><b>{{ filters.keyword || filters.sales_id || filters.status ? '没有匹配的客户' : '暂无客户档案' }}</b><span>{{ filters.keyword || filters.sales_id || filters.status ? '调整筛选条件后再试。' : '客户注册或由销售创建后会显示在这里。' }}</span></div>
            <footer v-if="!loading && total" class="table-footer"><span>第 {{ page }} / {{ totalPages }} 页 · 共 {{ total }} 条</span><div><button aria-label="上一页" :disabled="page <= 1" @click="changePage(page - 1)"><ChevronLeft :size="17" /></button><button aria-label="下一页" :disabled="page >= totalPages" @click="changePage(page + 1)"><ChevronRight :size="17" /></button></div></footer>
          </section>
        </template>

        <template v-else>
          <button class="back-link" @click="router.push('/admin/customers')"><ArrowLeft :size="16" />返回客户列表</button>
          <div v-if="loading" class="customer-loading detail-loading"><span></span><span></span><span></span><p>正在读取客户详情…</p></div>
          <template v-else-if="detail">
            <section class="customer-profile-head">
              <div class="profile-avatar"><UserRound :size="25" /></div>
              <div class="profile-identity"><h2>{{ detail.customer.name }}</h2><p>{{ detail.customer.username ? `账号 ${detail.customer.username}` : '未绑定登录账号' }}<span>·</span>ID {{ detail.customer.id }}</p></div>
              <span class="customer-status" :data-status="detail.customer.status">{{ detail.customer.status }}</span>
              <div class="profile-actions"><button class="secondary-control" @click="startEdit"><Pencil :size="15" />编辑资料</button><button class="primary-control" :disabled="!detail.customer.user_id || detail.customer.account_active === false" @click="openRecharge(detail.customer)"><ArrowDownToLine :size="15" />线下充值</button></div>
            </section>
            <section class="customer-facts">
              <div><small>钱包余额</small><strong class="large-balance">{{ balanceLabel(detail.customer.balance_cents) }}</strong><button class="fact-link" :disabled="!detail.customer.user_id" @click="openRecharge(detail.customer)"><CircleDollarSign :size="14" />充值</button></div>
              <div><small>业务归属</small><strong>{{ detail.customer.sales_name || '未分配' }}</strong><span>{{ salesOptions.find(item => item.id === detail.customer.sales_id)?.username || '—' }}</span></div>
              <div><small>已开通线路</small><strong>{{ detail.all_line_count }} 条</strong><span>{{ detail.active_line_count }} 条使用中</span></div>
              <div><small>账户状态</small><strong>{{ detail.customer.account_active == null ? '未绑定账号' : detail.customer.account_active ? '正常' : '已停用' }}</strong><span>注册于 {{ dateLabel(detail.customer.created_at) }}</span></div>
            </section>
            <nav class="customer-tabs" aria-label="客户详情分类" role="tablist">
              <button v-for="[key, label, icon] in tabs" :key="key" role="tab" :aria-selected="activeTab === key" :class="{ active: activeTab === key }" @click="activeTab = key"><component :is="icon" :size="16" />{{ label }}<span v-if="key === 'lines'">{{ recordTotal('lines') }}</span><span v-if="key === 'transactions'">{{ recordTotal('transactions') }}</span><span v-if="key === 'logs'">{{ recordTotal('audit_logs') }}</span></button>
            </nav>
            <section v-if="activeTab === 'overview'" class="detail-panel">
              <div class="detail-section-head"><div><h3>客户资料</h3><p>联系资料与销售归属</p></div><button class="text-control" @click="startEdit"><Pencil :size="14" />编辑</button></div>
              <form v-if="editing" class="edit-customer-form" @submit.prevent="saveCustomer">
                <label><span>客户名称</span><input v-model="editForm.name" required maxlength="120" /></label>
                <label><span>邮箱</span><input v-model="editForm.email" type="email" required maxlength="160" /></label>
                <label><span>手机号</span><input v-model="editForm.phone" type="tel" maxlength="40" /></label>
                <label><span>客户状态</span><input v-model="editForm.status" list="customer-statuses" required maxlength="30" /><datalist id="customer-statuses"><option v-for="item in statuses" :key="item" :value="item" /></datalist></label>
                <label><span>客户等级</span><select v-model="editForm.level"><option value="standard">标准客户</option><option value="vip">重点客户</option></select></label>
                <label><span>业务归属</span><select v-model="editForm.sales_id"><option value="">未分配</option><option v-for="item in salesOptions.filter(option => option.is_active || option.id === detail.customer.sales_id)" :key="item.id" :value="String(item.id)">{{ item.display_name }} · {{ item.username }}</option></select></label>
                <p class="form-note"><ShieldCheck :size="15" />姓名和手机号也会同步到客户登录账号；不会在此处修改密码或账号权限。</p>
                <div class="form-actions"><button class="secondary-control" type="button" :disabled="saving" @click="cancelEdit">取消</button><button class="primary-control" type="submit" :disabled="saving">{{ saving ? '保存中…' : '保存资料' }}</button></div>
              </form>
              <dl v-else class="customer-data-grid"><div><dt>客户名称</dt><dd>{{ detail.customer.name }}</dd></div><div><dt>登录账号</dt><dd>{{ detail.customer.username || '未绑定' }}</dd></div><div><dt>手机号</dt><dd>{{ detail.customer.phone || '—' }}</dd></div><div><dt>邮箱</dt><dd>{{ detail.customer.email || '—' }}</dd></div><div><dt>客户等级</dt><dd>{{ detail.customer.level === 'vip' ? '重点客户' : '标准客户' }}</dd></div><div><dt>业务归属</dt><dd>{{ detail.customer.sales_name || '未分配' }}</dd></div><div><dt>创建时间</dt><dd>{{ dateLabel(detail.customer.created_at) }}</dd></div><div><dt>最近更新</dt><dd>{{ dateLabel(detail.customer.updated_at) }}</dd></div></dl>
            </section>
            <section v-else-if="activeTab === 'lines'" class="detail-panel">
              <div class="detail-section-head"><div><h3>订阅与线路</h3><p>只展示已经交付的客户线路</p></div><span>共 {{ recordTotal('lines') }} 条</span></div>
              <form class="line-filter-bar" @submit.prevent="applyLineFilters"><label><span>到期范围</span><select v-model="lineFilters.expiry"><option value="all">全部期限</option><option value="7d">7 天内到期</option><option value="30d">30 天内到期</option><option value="90d">90 天内到期</option><option value="expired">已到期</option></select></label><label class="line-search"><span>产品 / 地区 / IP</span><input v-model="lineFilters.keyword" maxlength="120" placeholder="输入产品名、国家、节点或 IP" /></label><button class="secondary-control" type="submit" :disabled="recordLoading.lines"><Search :size="14" />筛选</button></form>
              <div v-if="detail.lines.length" class="detail-table-wrap"><table><thead><tr><th>产品</th><th>地区 / 节点</th><th>IP</th><th>状态</th><th>开通时间</th><th>到期时间</th></tr></thead><tbody><tr v-for="line in detail.lines" :key="line.id"><td><b>{{ line.product_name }}</b><small>{{ line.product_type }}</small></td><td>{{ line.region }}<small>{{ line.node_name }}</small></td><td>{{ line.static_ip || '—' }}</td><td><span class="customer-status" :data-status="lineTone(line)">{{ lineStatus(line.status, line.expires_at) }}</span></td><td>{{ dateLabel(line.started_at) }}</td><td>{{ dateLabel(line.expires_at) }}</td></tr></tbody></table></div>
              <button v-if="detail.lines.length < recordTotal('lines')" class="load-more" :disabled="recordLoading.lines" @click="loadMoreRecords('lines')">{{ recordLoading.lines ? '加载中…' : `加载更多线路（还有 ${recordTotal('lines') - detail.lines.length} 条）` }}</button>
              <div v-else-if="!detail.lines.length" class="detail-empty"><Package :size="22" /><span>{{ lineFilters.keyword || lineFilters.expiry !== 'all' ? '当前筛选下没有线路' : '暂无已开通线路' }}</span></div>
            </section>
            <section v-else-if="activeTab === 'transactions'" class="detail-panel">
              <div class="detail-section-head"><div><h3>交易流水</h3><p>钱包账本、收款和退款记录</p></div><span>共 {{ recordTotal('transactions') }} 条</span></div>
              <div v-if="detail.transactions.length" class="detail-table-wrap"><table><thead><tr><th>时间</th><th>类型</th><th>渠道 / 关联单号</th><th>状态</th><th>金额</th><th>余额变化</th><th>备注</th></tr></thead><tbody><tr v-for="item in detail.transactions" :key="item.id"><td>{{ dateLabel(item.created_at) }}</td><td>{{ transactionType(item.type) }}</td><td>{{ channelLabel(item.channel) }}<small>{{ item.reference || item.out_trade_no || '—' }}</small></td><td><span class="customer-status" :data-status="item.status">{{ paymentStatus(item.status) }}</span></td><td class="money-cell" :class="{ negative: item.amount_cents < 0 }">{{ item.amount_cents < 0 ? '−' : '+' }}{{ balanceLabel(Math.abs(item.amount_cents)) }}</td><td>{{ item.balance_before_cents == null ? '—' : `${balanceLabel(item.balance_before_cents)} → ${balanceLabel(item.balance_after_cents)}` }}</td><td class="remark-cell">{{ item.remark || '—' }}</td></tr></tbody></table></div>
              <button v-if="detail.transactions.length < recordTotal('transactions')" class="load-more" :disabled="recordLoading.transactions" @click="loadMoreRecords('transactions')">{{ recordLoading.transactions ? '加载中…' : `加载更多流水（还有 ${recordTotal('transactions') - detail.transactions.length} 条）` }}</button>
              <div v-else class="detail-empty"><CreditCard :size="22" /><span>暂无交易记录</span></div>
            </section>
            <section v-else class="detail-panel">
              <div class="detail-section-head"><div><h3>操作日志</h3><p>资料、归属、充值及关联订单的管理记录</p></div><span>共 {{ recordTotal('audit_logs') }} 条</span></div>
              <ol v-if="detail.audit_logs.length" class="audit-list"><li v-for="item in detail.audit_logs" :key="item.id"><span class="audit-dot"><FileClock :size="14" /></span><div><div class="audit-title"><b>{{ actionLabel(item.action) }}</b><span>{{ item.actor }}</span></div><p>{{ item.detail || '已执行管理操作' }}</p><time>{{ dateLabel(item.created_at) }}</time></div></li></ol>
              <button v-if="detail.audit_logs.length < recordTotal('audit_logs')" class="load-more" :disabled="recordLoading.audit_logs" @click="loadMoreRecords('audit_logs')">{{ recordLoading.audit_logs ? '加载中…' : `加载更多日志（还有 ${recordTotal('audit_logs') - detail.audit_logs.length} 条）` }}</button>
              <div v-else class="detail-empty"><FileClock :size="22" /><span>暂无操作日志</span></div>
            </section>
          </template>
          <div v-else class="customer-empty"><UserRound :size="24" /><b>无法找到客户</b><button class="secondary-control" @click="router.push('/admin/customers')">返回客户列表</button></div>
        </template>
      </main>
    </div>

    <div v-if="rechargeTarget" class="recharge-backdrop" @click.self="cancelRecharge">
      <section class="recharge-dialog" role="dialog" aria-modal="true" aria-labelledby="customer-recharge-title">
        <button class="dialog-close" aria-label="关闭" :disabled="saving" @click="cancelRecharge"><X :size="18" /></button>
        <div class="dialog-icon"><ArrowDownToLine :size="20" /></div><h2 id="customer-recharge-title">线下充值</h2><p class="dialog-subtitle">确认款项已到账后，为客户钱包登记充值。</p>
        <div class="recharge-account"><span class="profile-avatar small"><UserRound :size="17" /></span><div><b>{{ rechargeTarget.name }}</b><small>{{ rechargeTarget.username }} · {{ rechargeTarget.phone || rechargeTarget.email }}</small></div><strong>{{ balanceLabel(rechargeTarget.balance_cents ?? detail?.customer?.balance_cents ?? 0) }}</strong></div>
        <label class="dialog-field"><span>充值金额（元）</span><div class="amount-input"><i>¥</i><input v-model="rechargeAmount" type="number" inputmode="decimal" min="0.01" max="100000" step="0.01" placeholder="0.00" /></div></label>
        <label class="dialog-field"><span>到账凭证 / 经办说明</span><textarea v-model="rechargeRemark" rows="3" maxlength="500" placeholder="填写转账凭证号或经办说明" /></label>
        <p class="recharge-warning"><ShieldCheck :size="15" />提交后会立即记入钱包账本。请先核实实际收款，不能用此入口登记未到账资金。</p>
        <div v-if="rechargeError" class="dialog-error" role="alert">{{ rechargeError }}</div>
        <div class="dialog-actions"><button class="secondary-control" :disabled="saving" @click="cancelRecharge">返回</button><button class="primary-control" :disabled="saving" @click="submitRecharge">{{ saving ? '入账中…' : '确认入账' }}</button></div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.customer-admin-shell{min-height:100vh;display:grid;grid-template-columns:236px minmax(0,1fr);background:#f1f5f3;color:#20332d;font:14px/1.5 Inter,"Segoe UI","Microsoft YaHei",sans-serif}
.customer-admin-sidebar{position:sticky;top:0;height:100vh;padding:22px 14px;background:#fff;border-right:1px solid #dce6e1;display:flex;flex-direction:column;z-index:20}
.customer-admin-brand{height:42px;padding:0 9px;display:flex;align-items:center}.customer-admin-brand>button{display:none;margin-left:auto;color:#546d63}
.customer-admin-role{margin:8px 10px 24px;color:#71877d;font-size:12px}
.customer-admin-sidebar nav{display:grid;gap:4px}.nav-link{height:42px;padding:0 11px;display:flex;align-items:center;gap:10px;border-radius:6px;color:#52685f;text-align:left;font-size:13px;transition:background .18s,color .18s}.nav-link:hover,.nav-link.active{background:#e7f3ee;color:#176c55}.nav-link.active svg{color:#168064}
.customer-admin-user{margin-top:auto;padding:16px 10px 0;display:grid;border-top:1px solid #e2e9e5}.customer-admin-user>span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:650}.customer-admin-user small{color:#73877f;font-size:11px}.customer-admin-user button{display:flex;align-items:center;gap:7px;margin-top:13px;color:#637970;font-size:12px}
.customer-admin-main{min-width:0}.customer-admin-header{height:86px;padding:0 32px;display:flex;align-items:center;gap:14px;background:#fff;border-bottom:1px solid #dde6e1}.customer-admin-header h1{margin:0;font-size:21px;font-weight:680}.customer-admin-header p{margin:3px 0 0;color:#72867d;font-size:12px}.icon-refresh{margin-left:auto;width:36px;height:36px;display:grid;place-items:center;border:1px solid #dce5e0;border-radius:6px;background:#fff;color:#466359}.icon-refresh:hover,.refresh-text:hover{background:#edf5f1}.icon-refresh:disabled{opacity:.55}.customer-admin-menu{display:none}
.customer-admin-content{max-width:1580px;margin:auto;padding:25px 32px 48px}.customer-notice,.customer-error{position:fixed;right:22px;bottom:22px;z-index:50;display:flex;align-items:center;gap:9px;padding:11px 15px;border-radius:6px;background:#eaf6ee;border:1px solid #bfdfc9;color:#276745;box-shadow:0 8px 22px #173c2e1c;font-size:12px}.customer-error{top:101px;bottom:auto;left:calc(236px + 32px);right:32px;justify-content:space-between;background:#fff3ef;border-color:#e8c5bb;color:#8d4235}.customer-error button{display:grid;place-items:center}
.customer-toolbar{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin-bottom:20px}.customer-heading-row{display:flex;align-items:center;gap:9px}.customer-heading-row>svg{color:#16745a}.customer-heading-row h2{margin:0;font-size:18px}.customer-toolbar p{margin:7px 0 0;color:#74877f;font-size:12px}.count-label{padding:3px 8px;border-radius:4px;background:#e7f2ed;color:#3e6c59;font-size:11px}.refresh-text,.text-control{display:inline-flex;align-items:center;gap:7px;color:#40675a;font-size:12px}.refresh-text:disabled{opacity:.55}
.customer-filters{display:grid;grid-template-columns:minmax(250px,1fr) minmax(180px,250px) minmax(160px,210px) auto;align-items:end;gap:13px;margin-bottom:16px;padding:16px 18px;background:#fff;border:1px solid #dfe7e2;border-radius:7px}.customer-filters label,.edit-customer-form label,.dialog-field{display:grid;gap:6px;min-width:0}.customer-filters label>span,.edit-customer-form label>span,.dialog-field>span{font-size:11px;font-weight:650;color:#52685f}.customer-filters input,.customer-filters select,.edit-customer-form input,.edit-customer-form select,.dialog-field input,.dialog-field textarea{width:100%;height:38px;padding:0 10px;border:1px solid #d8e2dc;border-radius:5px;background:#fff;color:#20332d;font:inherit;font-size:12px;outline:none}.customer-filters input:focus,.customer-filters select:focus,.edit-customer-form input:focus,.edit-customer-form select:focus,.dialog-field input:focus,.dialog-field textarea:focus{border-color:#449377;box-shadow:0 0 0 3px #44937720}.keyword-filter>div{height:38px;display:flex;align-items:center;gap:8px;padding-left:10px;border:1px solid #d8e2dc;border-radius:5px;color:#71877d}.keyword-filter>div:focus-within{border-color:#449377;box-shadow:0 0 0 3px #44937720}.keyword-filter input{height:34px;padding:0;border:0;box-shadow:none!important}.keyword-filter button{align-self:stretch;padding:0 14px;background:#176e55;color:white;font-size:12px}.filter-reset{height:38px;padding:0 9px;color:#5d756b;font-size:12px}
.customer-table-section,.detail-panel{background:#fff;border:1px solid #dfe7e2;border-radius:7px;overflow:hidden}.customer-table-wrap,.detail-table-wrap{width:100%;overflow:auto}.customer-table-wrap table,.detail-table-wrap table{width:100%;border-collapse:collapse;white-space:nowrap;text-align:left}.customer-table-wrap th,.detail-table-wrap th{height:43px;padding:0 15px;background:#f7f9f8;border-bottom:1px solid #e5ebe7;color:#657b71;font-size:10px;font-weight:650}.customer-table-wrap td,.detail-table-wrap td{padding:12px 15px;border-bottom:1px solid #eef1ef;font-size:12px}.customer-table-wrap tbody tr:last-child td,.detail-table-wrap tbody tr:last-child td{border-bottom:0}.customer-table-wrap tbody tr:hover,.detail-table-wrap tbody tr:hover{background:#fbfcfb}.customer-table-wrap td:first-child{min-width:210px}.customer-name{display:block;padding:0;color:#243b32;text-align:left;font-weight:650}.customer-name:hover{color:#147357;text-decoration:underline;text-underline-offset:3px}.customer-table-wrap td small,.detail-table-wrap td small{display:block;margin-top:3px;color:#7a8b83;font-size:10px}.money-cell{color:#24674f;font-weight:650;font-variant-numeric:tabular-nums}.money-cell.negative{color:#a34c40}.action-column{text-align:right}.row-actions{display:flex;justify-content:flex-end;gap:4px}.row-actions button{width:30px;height:30px;display:grid;place-items:center;border-radius:5px;color:#527064}.row-actions button:hover{background:#eaf4ef;color:#176e55}.row-actions button:disabled{opacity:.35;cursor:not-allowed}.customer-status{display:inline-flex;align-items:center;min-height:23px;padding:2px 7px;border-radius:4px;background:#edf2ef;color:#536b60;font-size:10px;white-space:nowrap}.customer-status[data-status="成交客户"],.customer-status[data-status="active"],.customer-status[data-status="paid"],.customer-status[data-status="completed"]{background:#e7f5eb;color:#297349}.customer-status[data-status="潜在客户"],.customer-status[data-status="pending"],.customer-status[data-status="expired"],.customer-status[data-status="refunded"]{background:#f2f0e7;color:#806a34}.customer-status[data-status="已停用"],.customer-status[data-status="disabled"],.customer-status[data-status="failed"]{background:#f9ece9;color:#97483b}.table-footer{height:48px;padding:0 15px;display:flex;align-items:center;justify-content:space-between;border-top:1px solid #e8eeea;color:#71847b;font-size:11px}.table-footer>div{display:flex;gap:5px}.table-footer button{width:29px;height:29px;display:grid;place-items:center;border:1px solid #dae4de;border-radius:5px;color:#466357}.table-footer button:disabled{opacity:.35;cursor:not-allowed}
.customer-loading{min-height:250px;padding:38px;display:grid;grid-template-columns:repeat(3,1fr);gap:10px;align-content:center}.customer-loading>span{height:44px;border-radius:5px;background:linear-gradient(90deg,#edf2ef,#f7f9f8,#edf2ef);background-size:200% 100%;animation:loading-sheen 1.4s ease-in-out infinite}.customer-loading p{grid-column:1/-1;margin:8px 0 0;text-align:center;color:#74877e;font-size:12px}.detail-loading{margin-top:16px;background:#fff;border:1px solid #dfe7e2;border-radius:7px}.customer-empty,.detail-empty{min-height:210px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:9px;color:#788a81}.customer-empty svg,.detail-empty svg{color:#7e9a8c}.customer-empty b{color:#425b50;font-size:13px}.customer-empty span,.detail-empty span{font-size:11px}.customer-empty .secondary-control{margin-top:8px}
.back-link{display:inline-flex;align-items:center;gap:7px;margin:0 0 16px;color:#4b6c5e;font-size:12px}.back-link:hover{color:#167456}.customer-profile-head{display:flex;align-items:center;gap:14px;padding:19px 20px;background:#fff;border:1px solid #dfe7e2;border-radius:7px}.profile-avatar{width:48px;height:48px;display:grid;place-items:center;border-radius:50%;background:#e7f2ec;color:#276e54;flex:none}.profile-avatar.small{width:32px;height:32px}.profile-identity{min-width:0}.profile-identity h2{margin:0;font-size:18px}.profile-identity p{display:flex;align-items:center;gap:8px;margin:4px 0 0;color:#75877f;font-size:11px}.profile-actions{margin-left:auto;display:flex;gap:8px}.primary-control,.secondary-control{min-height:35px;padding:0 12px;display:inline-flex;align-items:center;justify-content:center;gap:7px;border:1px solid #d6e1db;border-radius:5px;background:#fff;color:#476258;font-size:11px;font-weight:650;transition:background .16s,border-color .16s}.secondary-control:hover{background:#f1f6f3;border-color:#b7d0c2}.primary-control{border-color:#176e55;background:#176e55;color:#fff}.primary-control:hover{background:#125b46}.primary-control:disabled,.secondary-control:disabled{opacity:.45;cursor:not-allowed}.large-balance{color:#176e55!important;font-size:20px!important;font-variant-numeric:tabular-nums}.customer-facts{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));margin:13px 0;background:#fff;border:1px solid #dfe7e2;border-radius:7px}.customer-facts>div{min-height:93px;padding:14px 17px;display:grid;align-content:center;gap:4px;border-right:1px solid #e6ece8}.customer-facts>div:last-child{border:0}.customer-facts small{color:#71847b;font-size:10px}.customer-facts strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#2c4338;font-size:14px}.customer-facts span{color:#809087;font-size:10px}.fact-link{justify-self:start;display:inline-flex;align-items:center;gap:5px;color:#32745b;font-size:10px}.customer-tabs{display:flex;gap:3px;margin:18px 0 12px;border-bottom:1px solid #d7e1dc;overflow:auto}.customer-tabs button{position:relative;min-height:42px;padding:0 14px;display:flex;align-items:center;gap:7px;color:#657a70;font-size:11px;white-space:nowrap}.customer-tabs button:hover{color:#176e55}.customer-tabs button.active{color:#176e55;font-weight:650}.customer-tabs button.active:after{position:absolute;right:10px;bottom:-1px;left:10px;height:2px;background:#168061;content:""}.customer-tabs button span{min-width:17px;padding:1px 5px;border-radius:8px;background:#eef3f0;color:#64786e;font-size:9px;text-align:center}.detail-panel{padding:0}.detail-section-head{min-height:68px;padding:14px 19px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #e7ece9}.detail-section-head h3{margin:0;color:#2c4338;font-size:14px}.detail-section-head p{margin:3px 0 0;color:#7a8b82;font-size:10px}.detail-section-head>span{color:#73867c;font-size:10px}.detail-section-head .text-control{margin-left:auto}.customer-data-grid{margin:0;padding:8px 19px;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));column-gap:25px}.customer-data-grid>div{min-height:66px;padding:13px 0;border-bottom:1px solid #edf1ee}.customer-data-grid dt{margin-bottom:5px;color:#7a8b82;font-size:10px}.customer-data-grid dd{margin:0;color:#354d42;font-size:12px;overflow-wrap:anywhere}.edit-customer-form{padding:18px 19px;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:15px}.edit-customer-form label>span{font-size:10px}.edit-customer-form input,.edit-customer-form select{height:37px}.form-note{grid-column:1/-1;margin:0;display:flex;align-items:center;gap:7px;color:#71857b;font-size:10px}.form-actions{grid-column:1/-1;display:flex;justify-content:flex-end;gap:8px;padding-top:5px}.detail-table-wrap td{padding-top:11px;padding-bottom:11px}.remark-cell{max-width:230px;overflow:hidden;text-overflow:ellipsis}.audit-list{margin:0;padding:9px 20px 12px;list-style:none}.audit-list li{display:grid;grid-template-columns:25px minmax(0,1fr);gap:11px;padding:14px 0;border-bottom:1px solid #edf1ee}.audit-list li:last-child{border-bottom:0}.audit-dot{width:25px;height:25px;display:grid;place-items:center;border-radius:50%;background:#eef5f0;color:#438166}.audit-title{display:flex;align-items:center;gap:9px}.audit-title b{color:#344b40;font-size:11px}.audit-title span{color:#72857b;font-size:10px}.audit-list p{margin:5px 0;color:#586f63;font-size:11px;overflow-wrap:anywhere}.audit-list time{color:#84938b;font-size:10px}
.recharge-backdrop{position:fixed;inset:0;z-index:60;padding:18px;display:grid;place-items:center;background:#10231d73}.recharge-dialog{position:relative;width:min(100%,470px);padding:25px;background:#fff;border:1px solid #dfe8e2;border-radius:8px;box-shadow:0 18px 55px #142d2233;animation:dialog-enter .2s cubic-bezier(.2,.8,.2,1)}.dialog-close{position:absolute;top:14px;right:14px;width:32px;height:32px;display:grid;place-items:center;border-radius:5px;color:#62766c}.dialog-close:hover{background:#eff4f1}.dialog-close:disabled{opacity:.5}.dialog-icon{width:38px;height:38px;margin-bottom:12px;display:grid;place-items:center;border-radius:7px;background:#e7f4ec;color:#26744f}.recharge-dialog h2{margin:0;font-size:18px}.dialog-subtitle{margin:5px 0 17px;color:#74867d;font-size:11px}.recharge-account{min-height:58px;margin-bottom:17px;padding:9px 10px;display:flex;align-items:center;gap:9px;background:#f6f9f7;border:1px solid #e5ece7;border-radius:6px}.recharge-account>div{min-width:0;display:grid;gap:3px}.recharge-account b{font-size:11px}.recharge-account small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#778980;font-size:9px}.recharge-account strong{margin-left:auto;color:#25704f;font-size:12px;white-space:nowrap}.dialog-field{margin-top:13px}.dialog-field input{height:40px}.dialog-field textarea{height:auto;min-height:73px;padding:9px 10px;resize:vertical}.amount-input{height:40px;display:flex;align-items:center;gap:7px;padding-left:11px;border:1px solid #d8e2dc;border-radius:5px;color:#537365}.amount-input:focus-within{border-color:#449377;box-shadow:0 0 0 3px #44937720}.amount-input i{font-style:normal;font-weight:650}.amount-input input{height:37px;padding:0;border:0;box-shadow:none!important;font-size:16px;font-variant-numeric:tabular-nums}.recharge-warning{margin:14px 0 0;padding:10px;display:flex;align-items:flex-start;gap:7px;background:#f5f7ee;border-radius:5px;color:#716942;font-size:10px;line-height:1.55}.recharge-warning svg{flex:none}.dialog-error{margin-top:12px;color:#9a4538;font-size:11px}.dialog-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:20px}.customer-admin-shell :focus-visible{outline:2px solid #218163;outline-offset:2px}.customer-admin-shell ::selection{background:#c8e6d6;color:#1c513e}.customer-admin-shell input::placeholder,.customer-admin-shell textarea::placeholder{color:#87968e;opacity:1}.customer-admin-scrim{display:none}
.line-filter-bar{padding:13px 18px;display:flex;align-items:end;gap:10px;border-bottom:1px solid #e9eeeb}.line-filter-bar label{display:grid;gap:5px}.line-filter-bar label>span{color:#687e73;font-size:10px}.line-filter-bar select,.line-filter-bar input{height:34px;min-width:155px;padding:0 9px;border:1px solid #d8e2dc;border-radius:5px;background:#fff;color:#294237;font:inherit;font-size:11px}.line-filter-bar input{min-width:230px}.line-filter-bar .line-search{margin-left:auto}
.load-more{min-height:36px;margin:10px auto 14px;padding:0 12px;display:flex;align-items:center;justify-content:center;border:1px solid #d9e4dd;border-radius:5px;color:#356b53;font-size:11px;transition:background .16s,border-color .16s}.load-more:hover{background:#eef6f1;border-color:#b8d2c2}.load-more:disabled{opacity:.55;cursor:wait}
.customer-admin-sidebar{overflow-y:auto;scrollbar-color:#c5d8cd transparent;scrollbar-width:thin}
@keyframes loading-sheen{to{background-position:-200% 0}}@keyframes dialog-enter{from{opacity:0;transform:translateY(8px) scale(.985)}to{opacity:1;transform:translateY(0) scale(1)}}
.keyword-filter button{flex:none;min-width:62px;white-space:nowrap}
@media(max-width:1120px){.customer-filters{grid-template-columns:minmax(220px,1fr) 1fr 1fr auto}.customer-admin-content{padding-right:22px;padding-left:22px}.customer-table-wrap table{min-width:880px}}
@media(max-width:760px){.customer-admin-shell{grid-template-columns:minmax(0,1fr)}.customer-admin-sidebar{position:fixed;left:0;top:0;width:250px;transform:translateX(-102%);transition:transform .2s ease;box-shadow:8px 0 26px #10251c1c}.customer-admin-sidebar.open{transform:translateX(0)}.customer-admin-brand>button,.customer-admin-menu{display:grid;place-items:center}.customer-admin-menu{width:36px;height:36px;border:1px solid #dce5e0;border-radius:5px;color:#4b685a}.customer-admin-scrim{position:fixed;inset:0;z-index:19;display:block;background:#10231d66}.customer-admin-header{height:74px;padding:0 15px;gap:10px}.customer-admin-header h1{font-size:18px}.customer-admin-header p{font-size:10px}.customer-admin-content{padding:18px 14px 34px}.customer-toolbar{align-items:flex-start}.customer-toolbar p{max-width:44ch;line-height:1.6}.refresh-text{font-size:0}.refresh-text svg{width:18px;height:18px}.customer-filters{grid-template-columns:1fr 1fr;gap:10px;padding:13px}.keyword-filter{grid-column:1/-1}.filter-reset{grid-column:2;justify-self:end}.customer-table-wrap{overflow:visible}.customer-table-wrap table,.customer-table-wrap tbody,.customer-table-wrap tr,.customer-table-wrap td{display:block;width:100%}.customer-table-wrap thead{display:none}.customer-table-wrap tbody{display:grid;gap:0}.customer-table-wrap tr{position:relative;padding:13px 12px 12px;border-bottom:1px solid #e8eeea}.customer-table-wrap td{min-width:0!important;padding:4px 0;border:0}.customer-table-wrap td:first-child{padding-right:100px}.customer-table-wrap td:nth-child(2),.customer-table-wrap td:nth-child(3),.customer-table-wrap td:nth-child(4),.customer-table-wrap td:nth-child(5),.customer-table-wrap td:nth-child(6){display:inline-block;width:auto;margin:6px 12px 0 0;color:#63776d;font-size:10px}.customer-table-wrap td:nth-child(6){display:none}.customer-table-wrap .row-actions{position:absolute;right:9px;top:9px;display:flex;width:auto}.customer-name{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.customer-profile-head{padding:14px;gap:10px;flex-wrap:wrap}.profile-avatar{width:41px;height:41px}.profile-identity{max-width:calc(100% - 60px)}.profile-identity h2{font-size:16px}.profile-identity p{font-size:9px;flex-wrap:wrap}.customer-profile-head>.customer-status{margin-left:auto}.profile-actions{width:100%;margin-left:0}.profile-actions button{flex:1}.customer-facts{grid-template-columns:1fr 1fr}.customer-facts>div{min-height:82px;padding:12px;border-bottom:1px solid #e6ece8}.customer-facts>div:nth-child(2){border-right:0}.customer-facts>div:nth-child(3),.customer-facts>div:nth-child(4){border-bottom:0}.customer-facts>div:nth-child(4){border-left:1px solid #e6ece8}.customer-tabs{margin-top:14px}.customer-tabs button{padding:0 10px;font-size:10px}.detail-section-head{padding:12px 14px}.customer-data-grid{grid-template-columns:1fr 1fr;padding:5px 14px;gap:0 14px}.customer-data-grid>div{min-height:61px}.edit-customer-form{grid-template-columns:1fr 1fr;padding:15px 14px;gap:12px}.form-note{align-items:flex-start;line-height:1.5}.detail-table-wrap table{min-width:760px}.detail-table-wrap{max-height:70vh}.audit-list{padding-right:14px;padding-left:14px}.customer-error{top:82px;left:12px;right:12px}.recharge-dialog{padding:21px 18px}.customer-admin-user{padding-top:14px}}
@media(max-width:760px){.line-filter-bar{align-items:stretch;flex-wrap:wrap;padding:12px}.line-filter-bar label:first-child{flex:1}.line-filter-bar label:first-child select{min-width:0;width:100%}.line-filter-bar .line-search{flex-basis:100%;margin-left:0}.line-filter-bar input{min-width:0;width:100%}.line-filter-bar button{flex:none}}
@media(max-width:420px){.customer-filters{grid-template-columns:1fr}.keyword-filter,.filter-reset{grid-column:auto}.filter-reset{justify-self:end}.customer-heading-row h2{font-size:16px}.count-label{font-size:9px}.customer-toolbar{gap:8px}.customer-toolbar p{font-size:10px}.customer-data-grid{grid-template-columns:1fr 1fr;column-gap:10px}.customer-data-grid dd{font-size:11px}.customer-facts strong{font-size:12px}.large-balance{font-size:17px!important}.edit-customer-form{grid-template-columns:1fr}.form-note,.form-actions{grid-column:auto}.profile-identity p{gap:4px}.customer-tabs button{gap:5px;padding:0 8px}.customer-tabs button svg{width:14px}}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{scroll-behavior:auto!important;animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important}}
</style>
