<script setup>
import BrandLogo from '../components/BrandLogo.vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { Activity, ArchiveRestore, ArrowLeft, Box, CheckCheck, ChevronLeft, ChevronRight, Database, Gauge, Inbox, LogOut, Menu, MessageSquare, PackageSearch, Paperclip, RefreshCw, RotateCw, Search, Send, Server, Settings, ShoppingCart, X } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import { createInboxThread, getCustomerLines, getCustomerOrders, getInboxMessages, getInboxSalesOptions, getInboxThread, getInboxThreads, getInboxUnreadCount, markInboxThreadRead, sendInboxMessage, updateInboxThread } from '../api'
import { session } from '../api/session'
import { logout as endSession } from '../api'

const route = useRoute()
const router = useRouter()
const loading = ref(true)
const error = ref('')
const feedback = ref('')
const actionBusy = ref(null)
const conversations = ref([])
const relatedOptions = ref([])
const salesOptions = ref([])
const activeId = ref('')
const detail = ref(null)
const messages = ref([])
const detailLoading = ref(false)
const detailError = ref('')
const mobileThread = ref(false)
const mobileNav = ref(false)
const composing = ref(false)
const reply = ref('')
const filters = reactive({ keyword: '', status: '', customerId: '', assignedSalesId: '', relatedType: '', relatedId: '' })
const draft = reactive({ subject: '', relatedKey: '', message: '' })
const ownerDraft = ref('')
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const unreadCount = ref(0)

const storedUser = computed(() => session.user || {})
const workspace = computed(() => route.meta.workspace)
const isCustomer = computed(() => workspace.value === 'client')
const isSales = computed(() => workspace.value === 'sales')
const active = computed(() => detail.value || conversations.value.find((item) => item.id === activeId.value) || null)
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))
const hasFilters = computed(() => Object.values(filters).some((value) => String(value).trim()))

const nav = computed(() => isCustomer.value
  ? [['首页', '/client', Gauge], ['购买线路', '/buy', ShoppingCart], ['我的订单', '/client/orders', PackageSearch], ['我的线路', '/client/lines', Server], ['续费管理', '/client/renewals', RotateCw], ['服务收件箱', '/client/inbox', MessageSquare], ['通知中心', '/client/notifications', Inbox], ['账户资料', '/client/profile', Settings]]
  : isSales.value
    ? [['销售概览', '/sales', Gauge], ['我的客户影响', '/sales/health', Activity], ['客户会话', '/sales/inbox', MessageSquare]]
    : [['订单处理', '/admin', Box], ['线路健康', '/admin/health', Activity], ['全量会话', '/admin/inbox', MessageSquare], ['资源池', '/admin/resource-pool', Database]])

const statusText = { open: '待回复', pending: '跟进中', closed: '已关闭' }
const dateTime = (value) => new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
const senderLabel = (senderRole) => senderRole === 'customer' ? '客户' : senderRole === 'sales' ? '销售' : '技术支持'
const userMessage = (requestError, fallback) => requestError?.userMessage || fallback

function mapThread(item) {
  const relatedType = item.related_order_id ? 'order' : item.related_line_id ? 'line' : null
  const relatedId = item.related_order_id || item.related_line_id || null
  return {
    ...item,
    customerId: item.customer_id,
    assignedSalesId: item.assigned_sales_id,
    customer: item.customer_name,
    owner: item.assigned_sales_name || '待分配',
    relatedType,
    relatedId,
    relatedLabel: relatedId ? `${relatedType === 'order' ? '订单' : '线路'} #${relatedId}` : '未关联订单或线路',
    unreadCustomer: item.customer_unread_count,
    unreadStaff: item.staff_unread_count,
    lastMessageAt: item.last_message_at,
  }
}

function mapMessage(item) {
  return { ...item, senderRole: item.sender_role, senderName: item.sender_display_name, createdAt: item.created_at }
}

function notify(message) {
  feedback.value = message
  window.setTimeout(() => { feedback.value = '' }, 3200)
}

function listParams() {
  const params = { page: page.value, page_size: pageSize.value }
  if (filters.status) params.status = filters.status
  if (filters.keyword.trim()) params.keyword = filters.keyword.trim()
  if (!isCustomer.value && filters.customerId) params.customer_id = Number(filters.customerId)
  if (workspace.value === 'admin' && filters.assignedSalesId) params.assigned_sales_id = Number(filters.assignedSalesId)
  if (filters.relatedId && filters.relatedType === 'order') params.order_id = Number(filters.relatedId)
  if (filters.relatedId && filters.relatedType === 'line') params.line_id = Number(filters.relatedId)
  return params
}

async function loadRelatedOptions() {
  if (!isCustomer.value) { relatedOptions.value = []; return }
  const [orderResponse, lineResponse] = await Promise.all([getCustomerOrders(), getCustomerLines()])
  relatedOptions.value = [
    ...orderResponse.data.map((item) => ({ type: 'order', id: item.id, label: `${item.product_name || '订单'} · ${item.order_no || `#${item.id}`}` })),
    ...lineResponse.data.map((item) => ({ type: 'line', id: item.id, label: item.line_name || item.name || `线路 #${item.id}` })),
  ]
}

async function loadUnreadCount() {
  const response = await getInboxUnreadCount()
  unreadCount.value = response.data.unread_count
}

async function loadThreads({ keepSelection = false } = {}) {
  loading.value = true
  error.value = ''
  try {
    const response = await getInboxThreads(listParams())
    conversations.value = response.data.items.map(mapThread)
    total.value = response.data.total
    const selected = keepSelection && conversations.value.some((item) => item.id === activeId.value) ? activeId.value : conversations.value[0]?.id
    activeId.value = selected || ''
    if (activeId.value) await loadThread(activeId.value)
    else { detail.value = null; messages.value = [] }
  } catch (requestError) {
    conversations.value = []
    total.value = 0
    detail.value = null
    messages.value = []
    error.value = userMessage(requestError, '会话列表加载失败，请重试')
  } finally {
    loading.value = false
  }
}

async function load() {
  error.value = ''
  try {
    await loadThreads()
    await Promise.all([loadRelatedOptions(), loadUnreadCount()])
  } catch (requestError) {
    error.value = userMessage(requestError, '收件箱加载失败，请重试')
  }
}

async function loadThread(id) {
  detailLoading.value = true
  detailError.value = ''
  try {
    const [threadResponse, messageResponse, salesResponse] = await Promise.all([
      getInboxThread(id),
      getInboxMessages(id, { page: 1, page_size: 100 }),
      workspace.value === 'admin' ? getInboxSalesOptions(id) : Promise.resolve({ data: [] }),
    ])
    if (activeId.value !== id) return
    detail.value = mapThread(threadResponse.data)
    messages.value = messageResponse.data.items.map(mapMessage)
    salesOptions.value = salesResponse.data
    ownerDraft.value = detail.value.assignedSalesId == null ? '' : String(detail.value.assignedSalesId)
    const unread = isCustomer.value ? detail.value.unreadCustomer : detail.value.unreadStaff
    if (unread) {
      try {
        await markInboxThreadRead(id)
        if (isCustomer.value) detail.value.unreadCustomer = 0
        else detail.value.unreadStaff = 0
        const listItem = conversations.value.find((item) => item.id === id)
        if (listItem) isCustomer.value ? listItem.unreadCustomer = 0 : listItem.unreadStaff = 0
        await loadUnreadCount()
      } catch (requestError) {
        error.value = userMessage(requestError, '消息已加载，但标记已读失败，请重试')
      }
    }
  } catch (requestError) {
    detail.value = null
    messages.value = []
    salesOptions.value = []
    detailError.value = userMessage(requestError, '会话详情加载失败，请重试')
  } finally {
    detailLoading.value = false
  }
}

function openConversation(item) {
  activeId.value = item.id
  mobileThread.value = true
  detail.value = null
  messages.value = []
  salesOptions.value = []
  loadThread(item.id)
}

async function sendReply() {
  const content = reply.value.trim()
  if (!content || !active.value) return
  if (actionBusy.value) return
  actionBusy.value = 'reply'
  error.value = ''
  try {
    const response = await sendInboxMessage(active.value.id, { content })
    messages.value.push(mapMessage(response.data))
    reply.value = ''
    notify('消息已发送')
    await loadThreads({ keepSelection: true })
    await loadUnreadCount()
  } catch (requestError) {
    error.value = userMessage(requestError, '消息发送失败，请检查内容后重试')
  } finally { actionBusy.value = null }
}

async function changeStatus(status) {
  if (!active.value) return
  if (actionBusy.value) return
  actionBusy.value = 'status'
  error.value = ''
  try {
    const response = await updateInboxThread(active.value.id, { status })
    detail.value = mapThread(response.data)
    const index = conversations.value.findIndex((item) => item.id === detail.value.id)
    if (index >= 0) conversations.value[index] = detail.value
    notify(`会话已更新为“${statusText[status]}”`)
  } catch (requestError) {
    error.value = userMessage(requestError, '会话状态更新失败，请重试')
  } finally { actionBusy.value = null }
}

async function changeOwner() {
  if (!active.value || workspace.value !== 'admin') return
  if (actionBusy.value) return
  actionBusy.value = 'owner'
  error.value = ''
  try {
    const response = await updateInboxThread(active.value.id, { assigned_sales_id: ownerDraft.value ? Number(ownerDraft.value) : null })
    detail.value = mapThread(response.data)
    const index = conversations.value.findIndex((item) => item.id === detail.value.id)
    if (index >= 0) conversations.value[index] = detail.value
    notify('会话负责人已更新')
  } catch (requestError) {
    error.value = userMessage(requestError, '负责人更新失败，请重新选择有效销售')
  } finally { actionBusy.value = null }
}

async function createConversation() {
  if (!draft.subject.trim() || !draft.message.trim()) {
    error.value = '请填写咨询主题和咨询内容'
    return
  }
  const related = relatedOptions.value.find((item) => `${item.type}:${item.id}` === draft.relatedKey)
  if (actionBusy.value) return
  actionBusy.value = 'create'
  error.value = ''
  let createdThread = null
  try {
    const payload = { subject: draft.subject.trim() }
    if (related?.type === 'order') payload.related_order_id = Number(related.id)
    if (related?.type === 'line') payload.related_line_id = Number(related.id)
    createdThread = (await createInboxThread(payload)).data
    await sendInboxMessage(createdThread.id, { content: draft.message.trim() })
    Object.assign(draft, { subject: '', relatedKey: '', message: '' })
    composing.value = false
    mobileThread.value = true
    page.value = 1
    await loadThreads()
    activeId.value = createdThread.id
    await loadThread(createdThread.id)
    notify('咨询已创建')
  } catch (requestError) {
    if (createdThread) {
      error.value = `会话 #${createdThread.id} 已创建，但首条消息发送失败：${userMessage(requestError, '请进入会话后重试')}`
      composing.value = false
      await loadThreads()
    } else {
      error.value = userMessage(requestError, '咨询创建失败，请检查内容后重试')
    }
  } finally { actionBusy.value = null }
}

function applyFilters() {
  if (filters.relatedId && !filters.relatedType) { error.value = '按关联对象筛选时，请先选择订单或线路'; return }
  page.value = 1
  loadThreads()
}
function clearFilters() {
  Object.assign(filters, { keyword: '', status: '', customerId: '', assignedSalesId: '', relatedType: '', relatedId: '' })
  page.value = 1
  loadThreads()
}
function goPage(nextPage) { page.value = nextPage; loadThreads() }

async function logout() {
  await endSession()
  router.replace('/login')
}

watch(() => route.fullPath, load)
onMounted(load)
</script>

<template>
  <div class="workspace-shell inbox-shell">
    <aside class="workspace-sidebar" :class="{ open: mobileNav }">
      <div class="workspace-brand"><BrandLogo eager /><button aria-label="关闭导航" @click="mobileNav = false"><X :size="20" /></button></div>
      <p class="workspace-role">{{ isCustomer ? '客户中心' : isSales ? '销售工作台' : '管理后台' }}</p>
      <nav><button v-for="[label, path, icon] in nav" :key="path" :class="{ active: route.path === path }" @click="router.push(path); mobileNav = false"><component :is="icon" :size="17" />{{ label }}</button></nav>
      <div class="workspace-user"><span>{{ storedUser.display_name || storedUser.username }}</span><small>{{ storedUser.role }}</small><button @click="logout"><LogOut :size="15" />退出登录</button></div>
    </aside>

    <div class="workspace-main">
      <header class="workspace-header inbox-header">
        <button class="workspace-menu" aria-label="打开导航" @click="mobileNav = true"><Menu :size="21" /></button>
        <div><p>{{ isCustomer ? 'SERVICE INBOX' : isSales ? 'CUSTOMER CONVERSATIONS' : 'CONVERSATION OPERATIONS' }}</p><h1>{{ isCustomer ? '服务收件箱' : isSales ? '客户会话' : '全量会话' }}</h1></div>
        <span class="inbox-demo-badge">本地 API</span>
      </header>

      <main class="inbox-content">
        <div class="inbox-notice"><MessageSquare :size="16" /><span>当前连接本地后端收件箱，数据和操作会写入本地测试数据库。</span><small>权限范围内 {{ unreadCount }} 条未读</small></div>
        <div v-if="feedback" class="workspace-toast">{{ feedback }}</div>
        <div v-if="error && conversations.length" class="workspace-alert">{{ error }}<button @click="error = ''">关闭</button></div>

        <section v-if="loading" class="inbox-loading" aria-label="正在加载会话">
          <div><i v-for="item in 6" :key="item"></i></div><span></span><span></span>
        </section>
        <section v-else-if="error && !conversations.length" class="inbox-state"><MessageSquare :size="28" /><b>无法加载会话</b><p>{{ error }}</p><button class="outline-action" @click="load"><RefreshCw :size="15" />重试</button></section>
        <section v-else-if="!conversations.length" class="inbox-state"><Inbox :size="30" /><b>{{ hasFilters ? '没有匹配会话' : '暂无会话' }}</b><p>{{ hasFilters ? '清除或调整筛选条件后重试。' : isCustomer ? '需要帮助时，可以新建一条咨询。' : '当前权限范围内没有客户会话。' }}</p><button v-if="hasFilters" class="outline-action" @click="clearFilters">清除筛选</button><button v-else-if="isCustomer" class="primary-action" @click="composing = true">新建咨询</button></section>

        <section v-else class="inbox-workbench" :class="{ 'show-thread': mobileThread }">
          <div class="inbox-list-pane">
            <div class="inbox-list-top">
              <div><b>会话</b><span>本页 {{ conversations.length }} / 共 {{ total }}</span></div>
              <button v-if="isCustomer" class="primary-action" @click="composing = true"><MessageSquare :size="15" />新建咨询</button>
            </div>
            <div class="inbox-filters">
              <label class="inbox-search"><Search :size="15" /><input v-model="filters.keyword" placeholder="搜索主题或消息正文" @keyup.enter="applyFilters" /></label>
              <select v-model="filters.status"><option value="">全部状态</option><option v-for="(label, value) in statusText" :key="value" :value="value">{{ label }}</option></select>
              <input v-if="!isCustomer" v-model="filters.customerId" class="inbox-filter-input" type="number" min="1" placeholder="客户 ID" />
              <input v-if="workspace === 'admin'" v-model="filters.assignedSalesId" class="inbox-filter-input" type="number" min="1" placeholder="负责人用户 ID" />
              <select v-model="filters.relatedType"><option value="">订单 / 线路</option><option value="order">关联订单</option><option value="line">关联线路</option></select>
              <input v-model="filters.relatedId" class="inbox-filter-input" type="number" min="1" placeholder="订单或线路 ID" />
              <div class="filter-actions"><button class="outline-action" @click="clearFilters">清除</button><button class="primary-action" @click="applyFilters">筛选</button></div>
            </div>
            <div class="conversation-list">
              <button v-for="item in conversations" :key="item.id" :class="{ active: item.id === activeId }" @click="openConversation(item)">
                <div class="conversation-line"><strong>{{ item.subject }}</strong><time>{{ dateTime(item.lastMessageAt) }}</time></div>
                <div class="conversation-meta"><span v-if="!isCustomer">{{ item.customer }}</span><span>{{ item.relatedId ? `${item.relatedType === 'order' ? '订单' : '线路'} · ${item.relatedId}` : '未关联业务对象' }}</span></div>
                <div class="conversation-foot"><em :data-status="item.status">{{ statusText[item.status] }}</em><span v-if="!isCustomer">{{ item.owner }}</span><b v-if="isCustomer ? item.unreadCustomer : item.unreadStaff">{{ isCustomer ? item.unreadCustomer : item.unreadStaff }}</b></div>
              </button>
            </div>
            <div class="inbox-pagination">
              <select v-model.number="pageSize" aria-label="每页会话数" @change="goPage(1)"><option :value="10">10 条/页</option><option :value="20">20 条/页</option><option :value="50">50 条/页</option></select>
              <span>{{ page }} / {{ totalPages }}</span>
              <button aria-label="上一页" :disabled="page === 1" @click="goPage(page - 1)"><ChevronLeft :size="15" /></button>
              <button aria-label="下一页" :disabled="page === totalPages" @click="goPage(page + 1)"><ChevronRight :size="15" /></button>
            </div>
          </div>

          <div v-if="active" class="inbox-thread-pane">
            <header>
              <button class="thread-back" aria-label="返回会话列表" @click="mobileThread = false"><ArrowLeft :size="19" /></button>
              <div><h2>{{ active.subject }}</h2><p>会话 #{{ active.id }} · {{ active.relatedLabel }}</p></div>
              <em :data-status="active.status">{{ statusText[active.status] }}</em>
            </header>
            <div class="thread-details">
              <span><small>客户</small>{{ active.customer }}</span><span><small>负责人</small>{{ active.owner }}</span><span><small>关联对象</small>{{ active.relatedLabel }}</span><span><small>最后消息</small>{{ dateTime(active.lastMessageAt) }}</span>
            </div>
            <div class="message-stream">
              <div v-if="detailLoading" class="thread-detail-state">正在加载消息…</div>
              <div v-else-if="detailError" class="thread-detail-state error-state"><span>{{ detailError }}</span><button class="outline-action" @click="loadThread(activeId)"><RefreshCw :size="14" />重试</button></div>
              <div v-else-if="!messages.length" class="thread-detail-state">暂无消息</div>
              <template v-else><article v-for="message in messages" :key="message.id" :class="{ mine: (isCustomer && message.senderRole === 'customer') || (isSales && message.senderRole === 'sales') || (workspace === 'admin' && ['admin', 'super_admin'].includes(message.senderRole)) }">
                <div><b>{{ message.senderName }}</b><span>{{ senderLabel(message.senderRole) }} · {{ dateTime(message.createdAt) }}</span></div>
                <p>{{ message.content }}</p>
              </article></template>
            </div>
            <div v-if="detail && !isCustomer" class="thread-staff-actions">
              <span>会话状态</span>
              <button v-if="active.status === 'open'" :disabled="actionBusy" @click="changeStatus('pending')"><CheckCheck :size="15" />跟进中</button>
              <button v-if="active.status !== 'closed'" :disabled="actionBusy" @click="changeStatus('closed')"><X :size="15" />关闭</button>
              <button v-else :disabled="actionBusy" @click="changeStatus('open')"><ArchiveRestore :size="15" />重新打开</button>
            </div>
            <div v-if="detail" class="reply-box">
              <textarea v-model="reply" :disabled="active.status === 'closed'" :placeholder="active.status === 'closed' ? '会话已关闭，重新打开后可回复' : '输入文字消息…'" @keydown.ctrl.enter="sendReply"></textarea>
              <div><button class="attachment-prototype" disabled title="附件 API 尚未接入"><Paperclip :size="16" />附件待接入</button><span>当前仅支持文字消息</span><button class="primary-action" :disabled="!reply.trim() || active.status === 'closed' || actionBusy" @click="sendReply"><Send :size="15" />{{ actionBusy === 'reply' ? '发送中' : '发送' }}</button></div>
            </div>
          </div>
          <div v-else class="inbox-thread-empty"><MessageSquare :size="28" /><p>选择一条会话查看消息</p></div>

          <aside v-if="detail && !isCustomer" class="inbox-actions-pane">
            <h2>会话处理</h2><p>操作将写入本地测试数据库。</p>
            <dl><div><dt>客户</dt><dd>{{ active.customer }}</dd></div><div><dt>负责人</dt><dd>{{ active.owner }}</dd></div><div><dt>关联类型</dt><dd>{{ active.relatedType ? (active.relatedType === 'order' ? '订单' : '线路') : '未关联' }}</dd></div><div><dt>关联编号</dt><dd>{{ active.relatedId || '—' }}</dd></div></dl>
            <label v-if="workspace === 'admin'" class="owner-prototype">负责人<select v-model="ownerDraft" :disabled="actionBusy"><option value="">取消分配</option><option v-if="!salesOptions.length" disabled>该客户暂无有效销售</option><option v-for="sales in salesOptions" :key="sales.id" :value="String(sales.id)">{{ sales.display_name }}</option></select><button :disabled="actionBusy" @click="changeOwner">更新负责人</button></label>
            <button v-if="active.status === 'open'" :disabled="actionBusy" @click="changeStatus('pending')"><CheckCheck :size="16" />转为跟进中</button>
            <button v-if="active.status !== 'closed'" :disabled="actionBusy" @click="changeStatus('closed')"><X :size="16" />关闭会话</button>
            <button v-else :disabled="actionBusy" @click="changeStatus('open')"><ArchiveRestore :size="16" />重新打开</button>
            <small>{{ workspace === 'admin' ? '负责人必须是该客户当前的有效销售。' : '销售只能处理本人负责客户的会话。' }}</small>
          </aside>
        </section>
      </main>

      <div v-if="composing" class="compose-backdrop" @click.self="composing = false">
        <section class="compose-panel" role="dialog" aria-modal="true" aria-labelledby="compose-title">
          <header><div><h2 id="compose-title">新建咨询</h2><p>选择订单或线路，让服务团队更快了解问题。</p></div><button aria-label="关闭" @click="composing = false"><X :size="19" /></button></header>
          <label>咨询主题<input v-model="draft.subject" maxlength="60" placeholder="简要说明需要协助的问题" /></label>
          <label>关联订单或线路（可选）<select v-model="draft.relatedKey"><option value="">不关联业务对象</option><option v-for="item in relatedOptions" :key="item.id" :value="`${item.type}:${item.id}`">{{ item.type === 'order' ? '订单' : '线路' }} · {{ item.label }}</option></select></label>
          <label>咨询内容<textarea v-model="draft.message" maxlength="500" placeholder="请描述现象、发生时间和期望处理方式"></textarea></label>
          <div class="compose-attachment"><Paperclip :size="17" /><span>附件上传尚未开放</span><button disabled>选择附件</button></div>
          <footer><button class="outline-action" :disabled="actionBusy" @click="composing = false">取消</button><button class="primary-action" :disabled="actionBusy" @click="createConversation">{{ actionBusy === 'create' ? '创建中…' : '创建咨询' }}</button></footer>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.inbox-shell{background:#eef3f1}.inbox-content{padding:18px 22px;min-width:0;position:relative}.inbox-header{border-bottom:1px solid #d8e2df}.inbox-demo-badge{margin-left:auto;padding:5px 9px;border-radius:5px;background:#fff0d8;color:#825718;font-size:11px;font-weight:700}.inbox-notice{min-height:38px;display:flex;align-items:center;gap:9px;padding:8px 12px;margin-bottom:14px;border:1px solid #d7e1de;border-radius:6px;background:#f8faf9;color:#526863;font-size:11px}.inbox-notice svg{color:#318173}.inbox-notice small{margin-left:auto;color:#6f807b;white-space:nowrap}.inbox-workbench{height:calc(100vh - 164px);min-height:590px;display:grid;grid-template-columns:minmax(270px,330px) minmax(420px,1fr) 224px;background:#f9fbfa;border:1px solid #d8e2df;border-radius:7px;overflow:hidden}.inbox-list-pane{min-width:0;border-right:1px solid #dce5e2;background:#fff;display:flex;flex-direction:column}.inbox-list-top{height:58px;display:flex;align-items:center;justify-content:space-between;gap:8px;padding:0 13px;border-bottom:1px solid #e1e8e5}.inbox-list-top>div{display:flex;align-items:center;gap:7px}.inbox-list-top b{font-size:14px}.inbox-list-top span{font-size:10px;color:#7d8c88}.primary-action,.outline-action{min-height:34px;display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:6px 10px;border-radius:5px;font-size:11px;font-weight:700}.primary-action{background:#18796b;color:#fff}.primary-action:hover{background:#12695d}.primary-action:disabled{background:#a8b9b4;cursor:not-allowed}.outline-action{border:1px solid #c8d6d2;background:#fff;color:#334d47}.inbox-filters{display:grid;grid-template-columns:1fr 1fr;gap:7px;padding:10px 12px;border-bottom:1px solid #e2e9e6}.inbox-search{grid-column:1/-1;height:36px;display:flex;align-items:center;gap:7px;padding:0 9px;border:1px solid #ccd8d4;border-radius:5px;color:#71847e}.inbox-search:focus-within{border-color:#408f82;box-shadow:0 0 0 2px #408f8224}.inbox-search input{width:100%;min-width:0;border:0;outline:0;background:transparent;font-size:11px}.inbox-filters select{height:34px;min-width:0;padding:0 24px 0 8px;border:1px solid #ccd8d4;border-radius:5px;background:#fff;color:#465d57;font-size:10px}.conversation-list{overflow:auto;scrollbar-color:#b8c8c3 transparent;scrollbar-width:thin}.conversation-list>button{width:100%;display:grid;gap:7px;padding:13px;border-bottom:1px solid #e5ebe9;text-align:left;transition:background .18s ease}.conversation-list>button:hover{background:#f4f8f6}.conversation-list>button.active{background:#e8f2ef;box-shadow:inset 3px 0 #248979}.conversation-line,.conversation-meta,.conversation-foot{display:flex;align-items:center;gap:8px;min-width:0}.conversation-line strong{font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.conversation-line time{margin-left:auto;font-size:9px;color:#869590;white-space:nowrap}.conversation-meta{font-size:9px;color:#71827d}.conversation-meta span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.conversation-foot em,.inbox-thread-pane header>em{padding:3px 6px;border-radius:4px;background:#e8efed;color:#526b65;font-size:9px;font-style:normal;white-space:nowrap}.conversation-foot em[data-status="open"],.inbox-thread-pane header>em[data-status="open"]{background:#fff0d8;color:#835817}.conversation-foot em[data-status="pending"],.inbox-thread-pane header>em[data-status="pending"]{background:#e5edf7;color:#3f6286}.conversation-foot em[data-status="handled"],.inbox-thread-pane header>em[data-status="handled"]{background:#e1f2eb;color:#26705e}.conversation-foot>span{font-size:9px;color:#72837e}.conversation-foot b{margin-left:auto;min-width:18px;height:18px;display:grid;place-items:center;border-radius:9px;background:#cf5848;color:#fff;font-size:9px}.inbox-no-result{padding:42px 12px;display:grid;justify-items:center;gap:6px;color:#7b8c87;text-align:center}.inbox-no-result b{font-size:12px}.inbox-no-result span{font-size:10px}.inbox-thread-pane{min-width:0;display:flex;flex-direction:column;background:#f6f8f7}.inbox-thread-pane>header{min-height:64px;display:flex;align-items:center;gap:11px;padding:10px 16px;border-bottom:1px solid #dce5e2;background:#fff}.inbox-thread-pane>header>div{min-width:0}.inbox-thread-pane h2{font-size:14px;line-height:1.3;margin:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.inbox-thread-pane h2 small{font-size:8px;padding:3px 5px;border-radius:3px;background:#fff0d8;color:#865b1c;vertical-align:2px}.inbox-thread-pane header p{font-size:9px;color:#758681;margin:4px 0 0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.inbox-thread-pane header>em{margin-left:auto}.thread-back{display:none;width:34px;height:34px;place-items:center;border:1px solid #d4dfdc;border-radius:5px}.thread-details{display:grid;grid-template-columns:.75fr .75fr 1.5fr 1fr;border-bottom:1px solid #dce5e2;background:#fbfcfc}.thread-details>span{min-width:0;padding:9px 12px;border-right:1px solid #e2e8e6;font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.thread-details>span:last-child{border:0}.thread-details small{display:block;color:#80908b;font-size:8px;margin-bottom:3px}.message-stream{flex:1;overflow:auto;padding:17px;scrollbar-color:#b8c8c3 transparent;scrollbar-width:thin}.message-stream article{width:min(76%,620px);margin-bottom:14px}.message-stream article.mine{margin-left:auto}.message-stream article>div{display:flex;align-items:center;gap:7px;margin-bottom:5px}.message-stream article>div b{font-size:10px}.message-stream article>div span{font-size:8px;color:#83928e}.message-stream article p{padding:10px 12px;margin:0;border:1px solid #dce4e1;border-radius:6px;background:#fff;color:#304641;font-size:11px;line-height:1.65;box-shadow:0 3px 10px rgba(20,48,43,.04)}.message-stream article.mine p{border-color:#bcd9d2;background:#e7f2ef}.message-stream article>small{display:block;margin-top:4px;color:#916421;font-size:8px}.reply-box{padding:11px 14px;border-top:1px solid #d8e2df;background:#fff}.reply-box textarea{width:100%;height:66px;resize:none;padding:9px 10px;border:1px solid #cbd7d3;border-radius:5px;font:inherit;font-size:11px;outline:0}.reply-box textarea:focus{border-color:#3e9183;box-shadow:0 0 0 2px #3e918324}.reply-box textarea:disabled{background:#eef2f0;color:#84928e}.reply-box>div{display:flex;align-items:center;gap:8px;margin-top:7px}.reply-box>div>span{font-size:9px;color:#80908b}.reply-box .primary-action{margin-left:auto}.attachment-prototype{display:inline-flex;align-items:center;gap:5px;padding:5px 7px;color:#8a9794;font-size:10px;cursor:not-allowed}.inbox-actions-pane{padding:17px 15px;border-left:1px solid #dce5e2;background:#fff}.inbox-actions-pane h2{font-size:13px;margin:0}.inbox-actions-pane>p{font-size:9px;color:#7a8b86;margin:4px 0 14px}.inbox-actions-pane dl{margin:0 0 16px}.inbox-actions-pane dl div{padding:9px 0;border-bottom:1px solid #e4eae8}.inbox-actions-pane dt{font-size:8px;color:#84938f}.inbox-actions-pane dd{margin:3px 0 0;font-size:10px;overflow-wrap:anywhere}.inbox-actions-pane>button{width:100%;min-height:35px;display:flex;align-items:center;gap:7px;padding:7px 9px;margin-bottom:7px;border:1px solid #cbd8d4;border-radius:5px;color:#3e5650;font-size:10px;font-weight:650}.inbox-actions-pane>button:hover{border-color:#3f9183;color:#217466}.inbox-actions-pane>small{display:block;margin-top:13px;color:#82918d;font-size:9px;line-height:1.55}.inbox-thread-empty,.inbox-state{display:grid;place-content:center;justify-items:center;gap:8px;color:#758681;text-align:center}.inbox-thread-empty p,.inbox-state p{font-size:11px;margin:0}.inbox-state{min-height:420px;border:1px solid #d8e2df;border-radius:7px;background:#f9fbfa}.inbox-state b{font-size:13px}.inbox-loading{height:calc(100vh - 164px);min-height:590px;display:grid;grid-template-columns:330px 1fr 224px;border:1px solid #d8e2df;border-radius:7px;overflow:hidden;background:#fff}.inbox-loading>div{padding:16px;border-right:1px solid #e0e8e5}.inbox-loading i,.inbox-loading>span{display:block;background:linear-gradient(90deg,#edf2f0,#f8faf9,#edf2f0);background-size:200% 100%;animation:inbox-shimmer 1.3s infinite}.inbox-loading i{height:72px;margin-bottom:10px;border-radius:5px}.inbox-loading>span{margin:18px;border-radius:6px}.inbox-loading>span:last-child{margin:18px 12px}.compose-backdrop{position:fixed;inset:0;z-index:50;display:grid;place-items:center;padding:18px;background:#07161466}.compose-panel{width:min(520px,100%);padding:20px;border-radius:8px;background:#fff;box-shadow:0 18px 52px rgba(10,30,27,.24)}.compose-panel>header{display:flex;justify-content:space-between;gap:16px;margin-bottom:18px}.compose-panel h2{font-size:18px;margin:0}.compose-panel header p{font-size:10px;color:#758681;margin:4px 0 0}.compose-panel header button{width:34px;height:34px;display:grid;place-items:center;border:1px solid #d2ded9;border-radius:5px}.compose-panel>label{display:grid;gap:6px;margin-bottom:13px;color:#455b55;font-size:10px;font-weight:650}.compose-panel input,.compose-panel select,.compose-panel textarea{width:100%;padding:9px 10px;border:1px solid #cbd7d3;border-radius:5px;background:#fff;font:inherit;font-size:11px;outline:0}.compose-panel textarea{height:110px;resize:vertical}.compose-panel input:focus,.compose-panel select:focus,.compose-panel textarea:focus{border-color:#3e9183;box-shadow:0 0 0 2px #3e918324}.compose-attachment{min-height:44px;display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px dashed #cbd7d3;border-radius:5px;color:#7e8e89}.compose-attachment span{font-size:10px}.compose-attachment button{margin-left:auto;color:#96a29f;font-size:9px}.compose-panel footer{display:flex;justify-content:flex-end;gap:8px;margin-top:18px}@keyframes inbox-shimmer{to{background-position:-200% 0}}
.inbox-filter-input{height:34px;min-width:0;padding:0 8px;border:1px solid #ccd8d4;border-radius:5px;background:#fff;color:#465d57;font-size:10px}.filter-actions{grid-column:1/-1;display:flex;justify-content:flex-end;gap:6px}.filter-actions button{min-height:30px}.conversation-list{flex:1}.inbox-pagination{min-height:43px;display:flex;align-items:center;justify-content:flex-end;gap:6px;padding:7px 11px;border-top:1px solid #e1e8e5;background:#fbfcfc}.inbox-pagination select{height:28px;margin-right:auto;border:1px solid #cbd7d3;border-radius:4px;background:#fff;color:#526963;font-size:9px}.inbox-pagination span{font-size:9px;color:#72837e;font-variant-numeric:tabular-nums}.inbox-pagination button{width:28px;height:28px;display:grid;place-items:center;border:1px solid #cbd7d3;border-radius:4px;background:#fff;color:#3f5a53}.inbox-pagination button:disabled{opacity:.35;cursor:not-allowed}.thread-detail-state{min-height:180px;display:grid;place-content:center;justify-items:center;gap:9px;color:#748681;font-size:11px}.owner-prototype{display:grid;gap:5px;margin-bottom:10px;color:#6f817c;font-size:9px}.owner-prototype select{height:34px;padding:0 8px;border:1px solid #cbd7d3;border-radius:5px;background:#fff;color:#3f5751;font-size:10px}.owner-prototype button{min-height:32px;border:1px solid #cbd7d3;border-radius:5px;background:#fff;color:#35514a;font-size:10px;font-weight:650}.thread-staff-actions{display:none;align-items:center;gap:7px;padding:8px 13px;border-top:1px solid #d8e2df;background:#f4f7f6}.thread-staff-actions>span{margin-right:auto;color:#805b20;font-size:9px}.thread-staff-actions button{min-height:31px;display:inline-flex;align-items:center;gap:5px;padding:5px 8px;border:1px solid #c8d6d2;border-radius:5px;background:#fff;font-size:9px}.thread-staff-actions button:disabled,.inbox-actions-pane>button:disabled{opacity:.45;cursor:not-allowed}
@media(max-width:1280px){.inbox-workbench,.inbox-loading{grid-template-columns:290px minmax(380px,1fr) 200px}.thread-details{grid-template-columns:1fr 1fr}.thread-details>span:nth-child(3),.thread-details>span:nth-child(4){border-top:1px solid #e2e8e6}}
@media(max-width:980px){.inbox-workbench,.inbox-loading{grid-template-columns:280px 1fr}.inbox-actions-pane{display:none}.thread-staff-actions{display:flex}.inbox-loading>span:last-child{display:none}}
@media(max-width:768px){.inbox-content{padding:13px}.inbox-header{height:82px}.inbox-notice{align-items:flex-start}.inbox-notice small{display:none}.inbox-workbench{height:calc(100vh - 150px);min-height:540px;display:block;position:relative}.inbox-list-pane,.inbox-thread-pane{height:100%}.inbox-thread-pane{display:none}.inbox-workbench.show-thread .inbox-list-pane{display:none}.inbox-workbench.show-thread .inbox-thread-pane{display:flex}.thread-back{display:grid}.inbox-loading{height:calc(100vh - 150px);min-height:540px;grid-template-columns:1fr}.inbox-loading>span{display:none}.message-stream article{width:88%}.inbox-demo-badge{font-size:9px}.compose-panel{padding:17px}}
@media(max-width:430px){.inbox-content{padding:10px}.inbox-notice{font-size:10px;margin-bottom:10px}.inbox-workbench{height:calc(100vh - 142px);border-radius:5px}.inbox-list-top{height:54px}.inbox-list-top .primary-action{font-size:0;width:34px;padding:0}.inbox-list-top .primary-action svg{width:15px}.inbox-filters{grid-template-columns:1fr 1fr}.conversation-list>button{padding:12px 11px}.thread-details{grid-template-columns:1fr 1fr}.thread-details>span:nth-child(3){grid-column:1/-1}.thread-details>span:nth-child(4){display:none}.message-stream{padding:13px 11px}.message-stream article{width:92%}.reply-box{padding:9px}.reply-box>div>span{display:none}.attachment-prototype{font-size:0}.attachment-prototype svg{width:16px}.compose-backdrop{padding:0;place-items:end center}.compose-panel{width:100%;border-radius:8px 8px 0 0}.compose-panel footer .primary-action{flex:1}.compose-attachment span{font-size:9px}}
@media(prefers-reduced-motion:reduce){.conversation-list>button{transition:none}.inbox-loading i,.inbox-loading>span{animation:none}}
</style>
