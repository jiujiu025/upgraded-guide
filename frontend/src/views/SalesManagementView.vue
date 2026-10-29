<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Activity, Check, ChevronLeft, ChevronRight, CircleAlert, KeyRound, Pencil, Plus, RefreshCw, Search, ShieldCheck, UserRound, Users, X } from 'lucide-vue-next'
import {
  createSalesUser, getAdminSalesCustomers, getAdminSalesLoginLogs, getAdminSalesUsers,
  resetAdminUserPassword, updateAdminSalesUser, updateAdminUserAccess,
} from '../api'

const router = useRouter()
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const users = ref([])
const total = ref(0)
const selectedId = ref(null)
const activeTab = ref('profile')
const creating = ref(false)
const editing = ref(false)
const accessConfirmation = ref(false)
const logs = ref([])
const customers = ref([])
const customerTotal = ref(0)
const customerPage = ref(1)
const page = ref(1)
const filters = reactive({ keyword: '', status: '' })
const createForm = reactive({ username: '', display_name: '', phone: '', password: '', confirmPassword: '' })
const editForm = reactive({ display_name: '', phone: '' })
const passwordForm = reactive({ new_password: '', confirm_password: '' })
let listRequest = 0
let detailRequest = 0
let searchTimer

const selected = computed(() => users.value.find((item) => item.id === selectedId.value) || null)
const pages = computed(() => Math.max(1, Math.ceil(total.value / 25)))
const customerPages = computed(() => Math.max(1, Math.ceil(customerTotal.value / 25)))
const dateLabel = (value) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '暂无记录'
const outcomeLabel = (item) => item.outcome === 'success' ? '登录成功' : ({ invalid_credentials: '凭据错误', account_disabled: '账号已停用', account_locked: '账号已锁定' })[item.failure_reason] || '登录失败'

function showNotice(message) {
  notice.value = message
  window.clearTimeout(showNotice.timer)
  showNotice.timer = window.setTimeout(() => { notice.value = '' }, 3200)
}

async function loadUsers() {
  const request = ++listRequest
  loading.value = true
  error.value = ''
  try {
    const { data } = await getAdminSalesUsers({ keyword: filters.keyword.trim() || undefined, status: filters.status || undefined, page: page.value, page_size: 25 })
    if (request !== listRequest) return
    users.value = data.items
    total.value = data.total
    if (!users.value.some((item) => item.id === selectedId.value)) selectedId.value = users.value[0]?.id || null
  } catch (requestError) {
    if (request === listRequest) error.value = requestError.userMessage || '销售列表加载失败'
  } finally {
    if (request === listRequest) loading.value = false
  }
}

async function loadDetail() {
  const request = ++detailRequest
  logs.value = []
  customers.value = []
  customerTotal.value = 0
  if (!selectedId.value) return
  try {
    const [logResponse, customerResponse] = await Promise.all([
      getAdminSalesLoginLogs(selectedId.value, 50),
      getAdminSalesCustomers(selectedId.value, { page: customerPage.value, page_size: 25 }),
    ])
    if (request !== detailRequest) return
    logs.value = logResponse.data
    customers.value = customerResponse.data.items
    customerTotal.value = customerResponse.data.total
  } catch (requestError) {
    if (request === detailRequest) error.value = requestError.userMessage || '销售详情加载失败'
  }
}

watch(selectedId, (id) => {
  const item = users.value.find((row) => row.id === id)
  Object.assign(editForm, { display_name: item?.display_name || '', phone: item?.phone || '' })
  editing.value = false
  accessConfirmation.value = false
  activeTab.value = 'profile'
  customerPage.value = 1
  loadDetail()
})
watch(() => filters.status, () => { if (page.value !== 1) page.value = 1; else loadUsers() })
watch(() => filters.keyword, () => {
  window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => { if (page.value !== 1) page.value = 1; else loadUsers() }, 250)
})
watch(page, loadUsers)
watch(customerPage, loadDetail)
onMounted(loadUsers)

async function createUser() {
  if (busy.value) return
  error.value = ''
  if (createForm.password !== createForm.confirmPassword) {
    error.value = '两次输入的初始密码不一致'
    return
  }
  busy.value = true
  try {
    const { data } = await createSalesUser({
      username: createForm.username.trim(), display_name: createForm.display_name.trim(),
      phone: createForm.phone.trim() || null, password: createForm.password,
    })
    Object.assign(createForm, { username: '', display_name: '', phone: '', password: '', confirmPassword: '' })
    creating.value = false
    filters.keyword = ''
    filters.status = ''
    await loadUsers()
    selectedId.value = data.id
    showNotice(`销售账号 ${data.username} 已创建`)
  } catch (requestError) {
    error.value = requestError.userMessage || '创建销售账号失败'
  } finally {
    busy.value = false
  }
}

async function saveProfile() {
  if (!selected.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await updateAdminSalesUser(selected.value.id, { display_name: editForm.display_name.trim(), phone: editForm.phone.trim() || null })
    editing.value = false
    await loadUsers()
    showNotice('销售资料已保存')
  } catch (requestError) {
    error.value = requestError.userMessage || '保存销售资料失败'
  } finally {
    busy.value = false
  }
}

async function toggleAccess() {
  if (!selected.value || busy.value) return
  if (!accessConfirmation.value) {
    accessConfirmation.value = true
    return
  }
  const next = !selected.value.is_active
  busy.value = true
  error.value = ''
  try {
    await updateAdminUserAccess(selected.value.id, { is_active: next })
    accessConfirmation.value = false
    await loadUsers()
    showNotice(next ? '销售账号已启用' : '销售账号已停用，现有会话已撤销')
  } catch (requestError) {
    error.value = requestError.userMessage || '账号状态更新失败'
  } finally {
    busy.value = false
  }
}

async function resetPassword() {
  if (!selected.value || busy.value) return
  if (passwordForm.new_password !== passwordForm.confirm_password) {
    error.value = '两次输入的新密码不一致'
    return
  }
  busy.value = true
  error.value = ''
  try {
    await resetAdminUserPassword(selected.value.id, { new_password: passwordForm.new_password, confirm: true })
    Object.assign(passwordForm, { new_password: '', confirm_password: '' })
    await loadDetail()
    showNotice('密码已重置，原有登录会话已全部撤销')
  } catch (requestError) {
    error.value = requestError.userMessage || '密码重置失败'
  } finally {
    busy.value = false
  }
}

function refresh() {
  loadUsers()
  loadDetail()
}
</script>

<template>
  <main class="sales-console">
    <header class="sales-console-head">
      <div><h1>销售管理</h1><p>销售账号、访问记录与客户归属统一管理</p></div>
      <div class="head-actions"><button class="refresh-button" type="button" aria-label="刷新数据" title="刷新数据" :disabled="loading" @click="refresh"><RefreshCw :size="16" /></button><button class="create-button" type="button" @click="creating = !creating"><Plus :size="16" />新建销售</button></div>
    </header>

    <div v-if="notice" class="console-notice" role="status"><Check :size="16" />{{ notice }}</div>
    <div v-if="error" class="console-error" role="alert"><CircleAlert :size="16" />{{ error }}<button type="button" aria-label="关闭提示" @click="error = ''"><X :size="15" /></button></div>

    <section v-if="creating" class="create-section">
      <div class="section-title"><div><h2>新建销售账号</h2><p>账号创建后可从销售登录入口登录；初始密码请通过独立安全渠道交付。</p></div><button class="icon-button" aria-label="关闭新建表单" @click="creating = false"><X :size="17" /></button></div>
      <form class="create-form" @submit.prevent="createUser">
        <label>登录账号<input v-model="createForm.username" autocomplete="off" minlength="3" maxlength="80" pattern="[A-Za-z0-9][A-Za-z0-9_.-]*" required /><small>创建后不可修改</small></label>
        <label>销售姓名<input v-model="createForm.display_name" autocomplete="name" maxlength="120" required /></label>
        <label>联系电话<input v-model="createForm.phone" type="tel" autocomplete="tel" maxlength="40" /></label>
        <label>初始密码<input v-model="createForm.password" type="password" autocomplete="new-password" minlength="10" maxlength="128" required /><small>至少 10 位，需含大小写字母、数字和特殊字符</small></label>
        <label>确认初始密码<input v-model="createForm.confirmPassword" type="password" autocomplete="new-password" minlength="10" maxlength="128" required /></label>
        <button class="create-button" type="submit" :disabled="busy"><UserRound :size="15" />{{ busy ? '正在创建' : '创建账号' }}</button>
      </form>
    </section>

    <div class="sales-workspace">
      <section class="sales-list-panel" aria-label="销售账号列表">
        <div class="list-toolbar">
          <label class="search-field"><Search :size="16" /><input v-model="filters.keyword" type="search" placeholder="搜索姓名、账号或手机号" aria-label="搜索销售" /></label>
          <select v-model="filters.status" aria-label="筛选账号状态"><option value="">全部状态</option><option value="active">正常</option><option value="disabled">已停用</option></select>
        </div>
        <div class="list-caption"><span>销售账号</span><b>{{ total }}</b></div>
        <div v-if="loading" class="list-state"><RefreshCw :size="17" class="spin" />正在加载销售列表</div>
        <div v-else-if="!users.length" class="list-state"><Users :size="21" />没有匹配的销售账号</div>
        <div v-else class="sales-user-list">
          <button v-for="item in users" :key="item.id" type="button" class="sales-user-row" :class="{ selected: selectedId === item.id }" :aria-current="selectedId === item.id ? 'true' : undefined" @click="selectedId = item.id">
            <span class="avatar">{{ (item.display_name || item.username).slice(0, 1) }}</span>
            <span class="user-main"><b>{{ item.display_name }}</b><small>{{ item.username }}<template v-if="item.phone"> · {{ item.phone }}</template></small></span>
            <span class="user-side"><i :data-state="item.is_active ? 'active' : 'disabled'"></i><small>{{ item.is_active ? '正常' : '停用' }}</small><em>{{ item.customer_count }} 客户</em></span>
          </button>
        </div>
        <div class="list-pagination"><span>第 {{ page }} / {{ pages }} 页</span><button type="button" aria-label="上一页" :disabled="page <= 1" @click="page--"><ChevronLeft :size="16" /></button><button type="button" aria-label="下一页" :disabled="page >= pages" @click="page++"><ChevronRight :size="16" /></button></div>
      </section>

      <section v-if="selected" class="sales-detail-panel">
        <div class="detail-heading">
          <div class="detail-identity"><span class="avatar large">{{ selected.display_name.slice(0, 1) }}</span><div><h2>{{ selected.display_name }}</h2><p>{{ selected.username }} · 销售账号</p></div></div>
          <span class="account-state" :data-state="selected.is_active ? 'active' : 'disabled'">{{ selected.is_active ? '正常' : '已停用' }}</span>
        </div>
        <div class="detail-meta"><span><Users :size="14" />{{ selected.customer_count }} 位名下客户</span><span><Activity :size="14" />最近登录 {{ dateLabel(selected.last_login_at) }}</span><span v-if="selected.locked_until"><ShieldCheck :size="14" />锁定至 {{ dateLabel(selected.locked_until) }}</span></div>
        <nav class="detail-tabs" aria-label="销售详情"><button type="button" :class="{ active: activeTab === 'profile' }" @click="activeTab = 'profile'">账号资料</button><button type="button" :class="{ active: activeTab === 'logs' }" @click="activeTab = 'logs'">登录记录 <span>{{ logs.length }}</span></button><button type="button" :class="{ active: activeTab === 'customers' }" @click="activeTab = 'customers'">名下客户 <span>{{ customerTotal }}</span></button></nav>

        <section v-if="activeTab === 'profile'" class="detail-content">
          <div class="section-title"><div><h3>基本资料</h3><p>账号名固定，姓名及联系电话可编辑</p></div><button v-if="!editing" class="outline-button" type="button" @click="editing = true"><Pencil :size="14" />编辑资料</button></div>
          <form v-if="editing" class="edit-form" @submit.prevent="saveProfile"><label>销售姓名<input v-model="editForm.display_name" maxlength="120" required /></label><label>联系电话<input v-model="editForm.phone" type="tel" maxlength="40" /></label><div class="form-actions"><button class="create-button" type="submit" :disabled="busy">保存修改</button><button class="outline-button" type="button" @click="editing = false">取消</button></div></form>
          <dl v-else class="profile-grid"><div><dt>登录账号</dt><dd>{{ selected.username }}</dd></div><div><dt>销售姓名</dt><dd>{{ selected.display_name }}</dd></div><div><dt>联系电话</dt><dd>{{ selected.phone || '未填写' }}</dd></div><div><dt>创建时间</dt><dd>{{ dateLabel(selected.created_at) }}</dd></div><div><dt>名下客户</dt><dd>{{ selected.customer_count }} 位</dd></div><div><dt>最近成功登录</dt><dd>{{ dateLabel(selected.last_login_at) }}</dd></div></dl>
          <div class="password-reset"><div class="section-title"><div><h3>重置登录密码</h3><p>重置后该销售的所有已登录会话将立即失效</p></div><KeyRound :size="17" /></div><form class="reset-form" @submit.prevent="resetPassword"><label>新密码<input v-model="passwordForm.new_password" type="password" autocomplete="new-password" minlength="10" maxlength="128" required placeholder="至少 10 位且符合密码规则" /></label><label>确认新密码<input v-model="passwordForm.confirm_password" type="password" autocomplete="new-password" minlength="10" maxlength="128" required /></label><button class="outline-button" type="submit" :disabled="busy">确认重置</button></form></div>
          <div class="access-row"><span><b>{{ selected.is_active ? '停用销售账号' : '启用销售账号' }}</b><small>{{ selected.is_active ? '停用将撤销全部会话，客户归属和历史记录保留' : '启用后销售可以重新登录' }}</small></span><button type="button" :class="selected.is_active ? 'danger-button' : 'outline-button'" :disabled="busy" @click="toggleAccess">{{ accessConfirmation ? (selected.is_active ? '确认停用' : '确认启用') : (selected.is_active ? '停用账号' : '启用账号') }}</button><button v-if="accessConfirmation" type="button" class="outline-button" @click="accessConfirmation = false">取消</button></div>
        </section>

        <section v-else-if="activeTab === 'logs'" class="detail-content">
          <div class="section-title"><div><h3>登录记录</h3><p>记录成功登录与失败尝试，最多显示最近 50 条</p></div><button class="icon-button" type="button" aria-label="刷新登录记录" @click="loadDetail"><RefreshCw :size="15" /></button></div>
          <div v-if="!logs.length" class="empty-state">暂无登录记录；新记录会从本次更新后开始保存。</div>
          <div v-else class="event-list"><div v-for="item in logs" :key="item.id" class="event-row"><span class="event-mark" :data-outcome="item.outcome"><Check v-if="item.outcome === 'success'" :size="14" /><CircleAlert v-else :size="14" /></span><span class="event-main"><b>{{ outcomeLabel(item) }}</b><small>{{ item.source_ip }}<template v-if="item.user_agent"> · {{ item.user_agent }}</template></small></span><time>{{ dateLabel(item.created_at) }}</time></div></div>
        </section>

        <section v-else class="detail-content">
          <div class="section-title"><div><h3>名下客户</h3><p>此列表展示当前绑定给该销售的客户资料</p></div><span class="count-label">{{ customerTotal }} 位</span></div>
          <div v-if="!customers.length" class="empty-state">该销售目前没有绑定客户。</div>
          <div v-else class="customer-table"><div class="customer-head"><span>客户</span><span>联系方式</span><span>账号状态</span><span>创建时间</span><span></span></div><div v-for="item in customers" :key="item.id" class="customer-row"><span><b>{{ item.name }}</b><small>{{ item.username || '未绑定登录账号' }}</small></span><span>{{ item.phone || item.email || '未填写' }}</span><span>{{ item.status }}</span><span>{{ dateLabel(item.created_at) }}</span><button type="button" @click="router.push(`/admin/customers/${item.id}`)">客户资料 <ChevronRight :size="14" /></button></div></div>
          <div class="customer-pagination"><span>{{ customerTotal ? (customerPage - 1) * 25 + 1 : 0 }}–{{ Math.min(customerPage * 25, customerTotal) }} / {{ customerTotal }}</span><button type="button" aria-label="上一页客户" :disabled="customerPage <= 1" @click="customerPage--"><ChevronLeft :size="16" /></button><button type="button" aria-label="下一页客户" :disabled="customerPage >= customerPages" @click="customerPage++"><ChevronRight :size="16" /></button></div>
        </section>
      </section>
      <section v-else class="sales-detail-empty"><Users :size="28" /><h2>选择一个销售账号</h2><p>查看账号资料、登录记录和名下客户</p></section>
    </div>
  </main>
</template>

<style scoped>
.sales-console{min-height:100vh;padding:30px clamp(18px,3vw,42px) 40px;background:#f2f5f3;color:#253b33}
.sales-console-head{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-bottom:20px}.sales-console-head h1{margin:0;font-size:24px;line-height:1.2}.sales-console-head p{margin:6px 0 0;color:#71827b;font-size:12px}.head-actions{display:flex;gap:8px}.create-button,.outline-button,.danger-button,.refresh-button,.icon-button{min-height:36px;display:inline-flex;align-items:center;justify-content:center;gap:7px;padding:0 11px;border:1px solid #cbd8d2;border-radius:5px;background:#fff;color:#35594c;font-size:11px;font-weight:700}.create-button{border-color:#277d68;background:#277d68;color:#fff}.create-button:hover{background:#1d6b58}.outline-button:hover,.refresh-button:hover,.icon-button:hover{border-color:#75a996;background:#f6faf8}.danger-button{border-color:#dbb8b3;color:#a54b42}.danger-button:hover{background:#fff3f1}.create-button:disabled,.outline-button:disabled,.danger-button:disabled,.refresh-button:disabled{opacity:.5;cursor:not-allowed}.refresh-button,.icon-button{width:36px;padding:0}.console-notice,.console-error{display:flex;align-items:center;gap:8px;margin:0 0 12px;padding:10px 12px;border:1px solid #b9dbcb;border-radius:5px;background:#edf8f1;color:#286c50;font-size:11px}.console-error{border-color:#e3bdb7;background:#fff2ef;color:#9a493f}.console-error button{margin-left:auto;color:inherit}
.create-section,.sales-list-panel,.sales-detail-panel,.sales-detail-empty{border:1px solid #d9e3de;border-radius:6px;background:#fff}.create-section{margin-bottom:14px;padding:16px}.section-title{display:flex;align-items:center;justify-content:space-between;gap:14px}.section-title h2,.section-title h3{margin:0;font-size:14px}.section-title p{margin:4px 0 0;color:#778780;font-size:10px}.create-form{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr)) auto;align-items:end;gap:10px;margin-top:15px}.create-form label,.edit-form label,.reset-form label{display:grid;gap:5px;color:#5c7068;font-size:10px}.create-form input,.edit-form input,.reset-form input{width:100%;height:35px;min-width:0;padding:0 9px;border:1px solid #cbd8d2;border-radius:4px;background:#fff;color:#2b4138;font-size:11px}.create-form input:focus,.edit-form input:focus,.reset-form input:focus,.search-field:focus-within{outline:2px solid #7db7a3;outline-offset:1px}.create-form small{color:#82928a;font-size:9px;line-height:1.3}
.sales-workspace{display:grid;grid-template-columns:minmax(280px,360px) minmax(0,1fr);align-items:start;gap:14px}.sales-list-panel{overflow:hidden}.list-toolbar{display:grid;grid-template-columns:minmax(0,1fr) 104px;gap:8px;padding:12px;border-bottom:1px solid #e4ebe7}.search-field{height:35px;display:flex;align-items:center;gap:7px;padding:0 9px;border:1px solid #d2ddd7;border-radius:4px;color:#788982}.search-field input{width:100%;min-width:0;border:0;outline:0;background:none;color:#344b41;font-size:10px}.list-toolbar select{min-width:0;height:35px;padding:0 7px;border:1px solid #d2ddd7;border-radius:4px;background:#fff;color:#536860;font-size:10px}.list-caption{height:37px;display:flex;align-items:center;gap:7px;padding:0 13px;color:#6c7e76;font-size:10px}.list-caption b,.count-label{color:#27755e;font-size:10px;font-variant-numeric:tabular-nums}.sales-user-list{max-height:calc(100vh - 270px);min-height:220px;overflow:auto}.sales-user-row{width:100%;min-height:68px;display:flex;align-items:center;gap:10px;padding:10px 12px;border-top:1px solid #edf1ef;text-align:left;transition:background .16s}.sales-user-row:hover{background:#f7faf8}.sales-user-row.selected{background:#edf6f1}.sales-user-row:focus-visible,.detail-tabs button:focus-visible,button:focus-visible{outline:2px solid #438a74;outline-offset:-2px}.avatar{width:34px;height:34px;flex:0 0 34px;display:grid;place-items:center;border-radius:50%;background:#e6f1eb;color:#2b765c;font-size:13px;font-weight:750}.avatar.large{width:42px;height:42px;flex-basis:42px;font-size:16px}.user-main{min-width:0;display:grid;gap:4px}.user-main b{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px}.user-main small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#788980;font-size:9px}.user-side{display:grid;grid-template-columns:7px auto;align-items:center;column-gap:5px;margin-left:auto;color:#71827a}.user-side i,.account-state:before{width:7px;height:7px;border-radius:50%;background:#388866}.user-side i[data-state="disabled"],.account-state[data-state="disabled"]:before{background:#b65b50}.user-side small{font-size:9px}.user-side em{grid-column:1/-1;margin-top:3px;color:#7e8e86;font-size:9px;font-style:normal;white-space:nowrap}.list-pagination,.customer-pagination{min-height:42px;display:flex;align-items:center;justify-content:flex-end;gap:6px;padding:6px 11px;border-top:1px solid #e3eae6;color:#788981;font-size:9px}.list-pagination span,.customer-pagination span{margin-right:auto}.list-pagination button,.customer-pagination button{width:28px;height:28px;display:grid;place-items:center;border:1px solid #d3ded8;border-radius:4px;background:#fff;color:#48645a}.list-pagination button:disabled,.customer-pagination button:disabled{opacity:.35;cursor:not-allowed}.list-state{min-height:200px;display:flex;align-items:center;justify-content:center;gap:8px;color:#7a8b83;font-size:11px}.spin{animation:spin .8s linear infinite}
.sales-detail-panel{min-width:0}.detail-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:17px 18px 13px}.detail-identity{display:flex;align-items:center;gap:11px;min-width:0}.detail-identity h2{margin:0;font-size:17px}.detail-identity p{margin:4px 0 0;color:#71827a;font-size:10px}.account-state{display:inline-flex;align-items:center;gap:6px;color:#317455;font-size:10px}.account-state[data-state="disabled"]{color:#a64f44}.account-state:before{content:""}.account-state[data-state="disabled"]:before{background:#b65b50}.detail-meta{display:flex;flex-wrap:wrap;gap:9px 18px;padding:0 18px 14px;color:#75867e;font-size:9px}.detail-meta span{display:inline-flex;align-items:center;gap:5px}.detail-tabs{display:flex;gap:4px;padding:0 16px;border-bottom:1px solid #e1e9e5}.detail-tabs button{min-height:40px;padding:0 10px;border-bottom:2px solid transparent;color:#71827a;font-size:10px}.detail-tabs button.active{border-color:#2b8268;color:#216b55;font-weight:750}.detail-tabs span{margin-left:4px;color:#7a8a83;font-variant-numeric:tabular-nums}.detail-content{padding:17px 18px}.section-title h3{font-size:13px}.edit-form{display:grid;grid-template-columns:1fr 1fr;gap:11px;margin-top:16px}.form-actions{grid-column:1/-1;display:flex;gap:7px}.profile-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:15px 12px;margin:17px 0 19px}.profile-grid div{min-width:0}.profile-grid dt{margin-bottom:5px;color:#829189;font-size:9px}.profile-grid dd{margin:0;overflow-wrap:anywhere;color:#324a3f;font-size:11px;font-weight:600}.password-reset{padding:15px 0;border-top:1px solid #e6ece9;border-bottom:1px solid #e6ece9}.password-reset .section-title>svg{color:#568b76}.reset-form{display:grid;grid-template-columns:1fr 1fr auto;align-items:end;gap:8px;margin-top:12px}.reset-form .outline-button{white-space:nowrap}.access-row{display:flex;align-items:center;justify-content:space-between;gap:15px;padding-top:15px}.access-row span{display:grid;gap:4px}.access-row b{font-size:10px}.access-row small{color:#7c8c84;font-size:9px}.empty-state{min-height:150px;display:grid;place-items:center;align-content:center;color:#7d8c85;font-size:11px}.event-list{margin-top:13px}.event-row{min-height:62px;display:flex;align-items:center;gap:10px;border-top:1px solid #e8eeeb}.event-mark{width:27px;height:27px;flex:0 0 27px;display:grid;place-items:center;border-radius:50%;background:#e7f4ec;color:#32805e}.event-mark[data-outcome="failed"]{background:#fbebe8;color:#b75a4e}.event-main{min-width:0;display:grid;gap:4px}.event-main b{font-size:10px}.event-main small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#7a8a83;font-size:9px}.event-row time{margin-left:auto;color:#71827b;font-size:9px;white-space:nowrap;font-variant-numeric:tabular-nums}.customer-table{margin-top:13px;overflow-x:auto}.customer-head,.customer-row{min-width:650px;display:grid;grid-template-columns:1.05fr 1fr .8fr 1.05fr auto;align-items:center;gap:10px;padding:0 9px}.customer-head{min-height:34px;background:#f1f5f3;color:#77877f;font-size:9px}.customer-row{min-height:51px;border-bottom:1px solid #e7edea;color:#52665d;font-size:9px}.customer-row>span:first-child{display:grid;gap:3px}.customer-row b{color:#324b3f;font-size:10px}.customer-row small{color:#839189;font-size:8px}.customer-row button{display:flex;align-items:center;gap:2px;color:#267359;font-size:9px;white-space:nowrap}.sales-detail-empty{min-height:380px;display:grid;place-content:center;justify-items:center;color:#8a9891}.sales-detail-empty h2{margin:12px 0 4px;color:#435b50;font-size:14px}.sales-detail-empty p{margin:0;font-size:10px}
@keyframes spin{to{transform:rotate(360deg)}}
@media(max-width:1000px){.sales-workspace{grid-template-columns:minmax(250px,310px) minmax(0,1fr)}.create-form{grid-template-columns:repeat(3,minmax(120px,1fr))}.reset-form{grid-template-columns:1fr 1fr}.reset-form .outline-button{grid-column:1/-1;justify-self:start}}
@media(max-width:760px){.sales-console{padding:68px 14px 24px}.sales-workspace{grid-template-columns:1fr}.sales-user-list{max-height:360px}.sales-detail-empty{min-height:210px}}
@media(max-width:520px){.sales-console-head h1{font-size:21px}.sales-console-head p{max-width:240px;line-height:1.5}.create-form{grid-template-columns:1fr 1fr}.create-form>label:nth-child(4),.create-form>label:nth-child(5),.create-form>button{grid-column:1/-1}.profile-grid{grid-template-columns:1fr 1fr}.reset-form{grid-template-columns:1fr}.reset-form .outline-button{grid-column:auto}.detail-content{padding:14px}.detail-heading{padding:14px}.detail-meta{padding:0 14px 12px}.detail-tabs{padding:0 8px}.detail-tabs button{padding:0 7px;font-size:9px}.event-row{align-items:flex-start;padding:10px 0}.event-row time{max-width:104px;text-align:right;white-space:normal;line-height:1.4}.access-row{align-items:flex-start}.customer-row{min-width:590px}}
@media(prefers-reduced-motion:reduce){.spin{animation:none}.sales-user-row{transition:none}}
</style>
