<script setup>
// 全局操作审计：管理员按人 / 动作 / 资源 / 时间回溯「谁改了什么」。
// 只读页面——审计日志能改就失去意义，所以这里没有任何编辑入口。
import { computed, onMounted, ref } from 'vue'
import { ChevronLeft, ChevronRight, RefreshCw, Search, ScrollText } from 'lucide-vue-next'
import { getAuditLogs, getAuditSummary } from '../api'

const loading = ref(true)
const error = ref('')
const items = ref([])
const total = ref(0)
const pages = ref(1)
const page = ref(1)
const pageSize = 50
const facets = ref({ actions: [], resource_types: [] })
const summary = ref([])

const filters = ref({ actor: '', action: '', resource_type: '', keyword: '', days: 7 })

// 常见动作给一句人话，没登记的显示原值——不猜业务含义
const ACTION_LABELS = {
  confirm: '确认订单',
  deliver: '交付线路',
  cancel: '取消订单',
  complete_renewal: '人工完成续费',
  wallet_order_payment: '余额支付订单',
  offline_recharge: '线下充值',
  create_customer_account: '创建客户账号',
  toggle_product_active: '商品上下架',
  runtime_setting: '运行开关变更',
  retry_procurement: '重新发起开通',
  sync: '同步供应商数据',
  sync_start: '开始同步',
  sync_finish: '同步完成',
  bootstrap_super_admin: '初始化超管',
  change_password: '修改密码',
  super_admin_password_recovery: '超管密码重置',
  update_supplier_credential: '更新供应商凭据',
  credential_update: '更新凭据',
  create: '创建',
  update: '修改',
}

const pageList = computed(() => {
  const list = []
  const start = Math.max(1, page.value - 2)
  const end = Math.min(pages.value, start + 4)
  for (let index = start; index <= end; index += 1) list.push(index)
  return list
})

function actionLabel(action) {
  return ACTION_LABELS[action] || action
}

function formatTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (number) => String(number).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

function rangeParams() {
  if (!filters.value.days) return {}
  const start = new Date(Date.now() - filters.value.days * 86400000)
  return { start: start.toISOString() }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const params = { page: page.value, page_size: pageSize, ...rangeParams() }
    for (const key of ['actor', 'action', 'resource_type', 'keyword']) {
      if (filters.value[key]) params[key] = filters.value[key]
    }
    const { data } = await getAuditLogs(params)
    items.value = data.items || []
    total.value = data.total || 0
    pages.value = data.pages || 1
    if (data.facets) facets.value = data.facets
  } catch (requestError) {
    error.value = requestError.userMessage || '审计日志加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

async function loadSummary() {
  try {
    const { data } = await getAuditSummary(7)
    summary.value = data.items || []
  } catch {
    summary.value = []
  }
}

function search() {
  page.value = 1
  load()
}

function reset() {
  filters.value = { actor: '', action: '', resource_type: '', keyword: '', days: 7 }
  search()
}

function goPage(next) {
  if (next < 1 || next > pages.value || loading.value) return
  page.value = next
  load()
}

onMounted(() => {
  load()
  loadSummary()
})
</script>

<template>
  <section class="audit-page">
    <header class="audit-head">
      <div>
        <p class="audit-eyebrow"><ScrollText :size="13" /> 操作审计</p>
        <h1>谁在什么时候改了什么</h1>
        <p class="audit-desc">
          后台的收款确认、线路交付、改价、开关切换、自动采购都记在这里。只读，不可删除。
        </p>
      </div>
      <button class="audit-refresh" type="button" :disabled="loading" @click="load">
        <RefreshCw :size="14" />{{ loading ? '加载中…' : '刷新' }}
      </button>
    </header>

    <div class="audit-summary">
      <span class="audit-summary-label">近 7 天</span>
      <span v-if="!summary.length" class="audit-summary-empty">还没有记录</span>
      <span v-for="item in summary" :key="item.action" class="audit-chip">
        {{ actionLabel(item.action) }}<b>{{ item.count }}</b>
      </span>
    </div>

    <div class="audit-filters">
      <label>时间
        <select v-model.number="filters.days" @change="search">
          <option :value="1">最近 24 小时</option>
          <option :value="7">最近 7 天</option>
          <option :value="30">最近 30 天</option>
          <option :value="0">全部</option>
        </select>
      </label>
      <label>操作人
        <input v-model.trim="filters.actor" placeholder="用户名或姓名" @keyup.enter="search" />
      </label>
      <label>动作
        <select v-model="filters.action" @change="search">
          <option value="">全部</option>
          <option v-for="action in facets.actions" :key="action" :value="action">
            {{ actionLabel(action) }}（{{ action }}）
          </option>
        </select>
      </label>
      <label>资源
        <select v-model="filters.resource_type" @change="search">
          <option value="">全部</option>
          <option v-for="type in facets.resource_types" :key="type" :value="type">{{ type }}</option>
        </select>
      </label>
      <label class="wide">关键词
        <input v-model.trim="filters.keyword" placeholder="在详情里搜，如订单号、IP、邮箱" @keyup.enter="search" />
      </label>
      <div class="audit-actions">
        <button class="audit-primary" type="button" :disabled="loading" @click="search">
          <Search :size="14" />查询
        </button>
        <button class="audit-ghost" type="button" :disabled="loading" @click="reset">重置</button>
      </div>
    </div>

    <p v-if="error" class="audit-error">{{ error }}</p>

    <div class="audit-table">
      <div class="audit-row audit-row-head">
        <span>时间</span><span>操作人</span><span>动作</span><span>资源</span><span>详情</span>
      </div>
      <div v-if="loading && !items.length" class="audit-empty">正在读取…</div>
      <div v-else-if="!items.length" class="audit-empty">这个条件下没有记录</div>
      <div v-for="row in items" :key="row.id" class="audit-row">
        <span class="audit-time">{{ formatTime(row.created_at) }}</span>
        <span class="audit-actor">{{ row.actor }}</span>
        <span><em class="audit-tag">{{ actionLabel(row.action) }}</em></span>
        <span class="audit-resource">{{ row.resource_type }}{{ row.resource_id ? `#${row.resource_id}` : '' }}</span>
        <span class="audit-detail">{{ row.detail || '—' }}</span>
      </div>
    </div>

    <footer class="audit-pager">
      <span>共 {{ total }} 条 · 第 {{ page }} / {{ pages }} 页</span>
      <div>
        <button type="button" :disabled="page <= 1 || loading" @click="goPage(page - 1)">
          <ChevronLeft :size="15" />
        </button>
        <button
          v-for="index in pageList"
          :key="index"
          type="button"
          :class="{ active: index === page }"
          :disabled="loading"
          @click="goPage(index)"
        >
          {{ index }}
        </button>
        <button type="button" :disabled="page >= pages || loading" @click="goPage(page + 1)">
          <ChevronRight :size="15" />
        </button>
      </div>
    </footer>
  </section>
</template>

<style scoped>
.audit-page { display: grid; gap: 14px; }
.audit-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.audit-eyebrow { display: inline-flex; align-items: center; gap: 6px; margin: 0 0 4px; font-size: 10px; letter-spacing: .12em; text-transform: uppercase; color: #6f817b; }
.audit-head h1 { margin: 0; font-size: 21px; letter-spacing: -.01em; }
.audit-desc { margin: 6px 0 0; font-size: 12px; color: #71837d; }
.audit-refresh { display: inline-flex; align-items: center; gap: 6px; height: 34px; padding: 0 12px; border: 1px solid #d5e0da; border-radius: 6px; background: #fff; color: #4f6559; font-size: 12px; cursor: pointer; }
.audit-refresh:disabled { opacity: .6; cursor: progress; }

.audit-summary { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; padding: 10px 12px; border: 1px solid #dbe4df; border-radius: 6px; background: #f7faf8; }
.audit-summary-label { font-size: 10px; color: #7b8d87; }
.audit-summary-empty { font-size: 11px; color: #90a09a; }
.audit-chip { display: inline-flex; align-items: center; gap: 5px; padding: 3px 8px; border-radius: 999px; background: #e8f2ed; color: #2c6b58; font-size: 11px; }
.audit-chip b { font-weight: 700; }

.audit-filters { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 9px; padding: 13px; border: 1px solid #dbe4df; border-radius: 6px; background: #fff; }
.audit-filters label { display: grid; gap: 5px; font-size: 10px; color: #6b7d75; }
.audit-filters label.wide { grid-column: span 2; }
.audit-filters input, .audit-filters select { height: 32px; padding: 0 9px; border: 1px solid #cfdad4; border-radius: 5px; background: #fff; color: #2d4439; font: inherit; font-size: 12px; min-width: 0; }
.audit-actions { grid-column: 1 / -1; display: flex; gap: 8px; }
.audit-primary { display: inline-flex; align-items: center; gap: 6px; height: 32px; padding: 0 14px; border-radius: 5px; background: #2f9c85; color: #fff; font-size: 12px; font-weight: 600; cursor: pointer; }
.audit-ghost { height: 32px; padding: 0 12px; border: 1px solid #d5e0da; border-radius: 5px; background: #fff; color: #4f6559; font-size: 12px; cursor: pointer; }
.audit-primary:disabled, .audit-ghost:disabled { opacity: .6; cursor: progress; }

.audit-error { margin: 0; padding: 9px 12px; border-radius: 6px; background: #fdf0ec; color: #9a4a39; font-size: 12px; }

.audit-table { border: 1px solid #dbe4df; border-radius: 6px; overflow: hidden; background: #fff; }
.audit-row { display: grid; grid-template-columns: 148px 110px 130px 130px minmax(0, 1fr); gap: 10px; align-items: center; padding: 9px 14px; border-top: 1px solid #eef3f0; font-size: 12px; }
.audit-row-head { border-top: 0; background: #eef3f1; color: #6f817c; font-size: 11px; }
.audit-time { color: #5d7169; font-variant-numeric: tabular-nums; font-size: 11px; }
.audit-actor { color: #243b33; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.audit-tag { display: inline-block; padding: 2px 7px; border-radius: 4px; background: #e8f2ed; color: #2c6b58; font-size: 11px; font-style: normal; white-space: nowrap; }
.audit-resource { color: #6b7d75; font-size: 11px; }
.audit-detail { color: #4d6259; overflow-wrap: anywhere; }
.audit-empty { padding: 26px 14px; text-align: center; color: #90a09a; font-size: 12px; }

.audit-pager { display: flex; align-items: center; justify-content: space-between; gap: 12px; font-size: 11px; color: #7b8d87; }
.audit-pager div { display: flex; gap: 4px; }
.audit-pager button { min-width: 30px; height: 30px; padding: 0 8px; display: inline-grid; place-items: center; border: 1px solid #d5e0da; border-radius: 5px; background: #fff; color: #4f6559; font-size: 12px; cursor: pointer; }
.audit-pager button.active { background: #2f9c85; border-color: #2f9c85; color: #fff; }
.audit-pager button:disabled { opacity: .5; cursor: not-allowed; }

@media (max-width: 900px) {
  .audit-filters { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .audit-filters label.wide { grid-column: 1 / -1; }
  .audit-row { grid-template-columns: 1fr; gap: 4px; padding: 12px; }
  .audit-row-head { display: none; }
  .audit-row span::before { content: attr(data-label); }
}
</style>
