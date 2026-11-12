<script setup>
// 采购记录：客户付款之后，上游到底买了没、买到了什么、错在哪。
// 这是「自动采购」唯一的可视化出口——没有它，出问题只能连生产库查表。
// 只读；重试走订单侧唯一实现（POST /admin/orders/{id}/retry-procurement），避免两套扣款逻辑。
import { computed, onMounted, ref } from 'vue'
import { AlertTriangle, ChevronLeft, ChevronRight, PackageSearch, RefreshCw, Search } from 'lucide-vue-next'
import { getProcurement, getProcurements, retryAdminProcurement } from '../api'

const STATUS_LABELS = {
  pending: '采购中',
  succeeded: '已开通',
  failed: '失败',
  manual: '转人工',
}
const STATUS_TONE = {
  pending: 'wait',
  succeeded: 'ok',
  failed: 'bad',
  manual: 'warn',
}

const loading = ref(true)
const error = ref('')
const items = ref([])
const total = ref(0)
const pages = ref(1)
const page = ref(1)
const pageSize = 25
const counts = ref({ pending: 0, succeeded: 0, failed: 0, manual: 0 })
const suppliers = ref([])
const filters = ref({ status: '', supplier_id: '', keyword: '', days: 30 })

const detail = ref(null)
const detailLoading = ref(false)
const detailError = ref('')
const feedback = ref('')
const retrying = ref(false)

const pageList = computed(() => {
  const list = []
  const start = Math.max(1, page.value - 2)
  const end = Math.min(pages.value, start + 4)
  for (let index = start; index <= end; index += 1) list.push(index)
  return list
})

const totalCount = computed(() => Object.values(counts.value).reduce((sum, value) => sum + value, 0))

function statusLabel(value) {
  return STATUS_LABELS[value] || value || '—'
}

function formatTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (number) => String(number).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
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
    if (filters.value.status) params.status = filters.value.status
    if (filters.value.supplier_id) params.supplier_id = Number(filters.value.supplier_id)
    if (filters.value.keyword) params.keyword = filters.value.keyword
    const { data } = await getProcurements(params)
    items.value = data.items || []
    total.value = data.total || 0
    pages.value = data.pages || 1
    counts.value = data.counts || counts.value
    suppliers.value = data.suppliers || []
  } catch (requestError) {
    error.value = requestError.userMessage || '采购记录加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

function search() {
  page.value = 1
  detail.value = null
  load()
}

function reset() {
  filters.value = { status: '', supplier_id: '', keyword: '', days: 30 }
  search()
}

function pickStatus(value) {
  filters.value.status = filters.value.status === value ? '' : value
  search()
}

function goPage(next) {
  if (next < 1 || next > pages.value || loading.value) return
  page.value = next
  load()
}

async function openDetail(row) {
  detail.value = { id: row.id, loading: true }
  detailLoading.value = true
  detailError.value = ''
  feedback.value = ''
  try {
    const { data } = await getProcurement(row.id)
    detail.value = data
  } catch (requestError) {
    detailError.value = requestError.userMessage || '详情加载失败'
    detail.value = row
  } finally {
    detailLoading.value = false
  }
}

function closeDetail() {
  detail.value = null
  detailError.value = ''
  feedback.value = ''
}

// 只有「失败了」才值得重试：manual 表示上游根本没有这个档位，重试也买不到
function canRetry(row) {
  return row.status === 'failed' && row.order_id
}

async function retry(row) {
  if (!canRetry(row) || retrying.value) return
  const label = row.order_no || `订单 #${row.order_id}`
  if (!window.confirm(`重新发起 ${label} 的自动开通？只有「已收款且没有成功采购记录」时才真的向上游下单。`)) return
  retrying.value = true
  feedback.value = ''
  try {
    const { data } = await retryAdminProcurement(row.order_id)
    feedback.value = data.status === 'already_delivered'
      ? '该订单已有成功采购记录，未重复下单。'
      : '已加入后台任务，几秒后刷新看结果。'
    await load()
  } catch (requestError) {
    feedback.value = requestError.userMessage || '重试失败，请稍后再试'
  } finally {
    retrying.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="proc-page">
    <header class="proc-head">
      <div>
        <p class="proc-eyebrow"><PackageSearch :size="13" /> 采购记录</p>
        <h1>每笔自动开通，都能对上单</h1>
        <p class="proc-desc">
          这里记录每张订单向上游发起的采购：状态、上游单号、失败原因和交付的线路。只读，不含上游账号密码。
        </p>
      </div>
      <button class="proc-refresh" type="button" :disabled="loading" @click="load">
        <RefreshCw :size="14" />{{ loading ? '加载中…' : '刷新' }}
      </button>
    </header>

    <div class="proc-counts">
      <span class="proc-counts-label">当前条件下</span>
      <button
        v-for="key in ['succeeded', 'failed', 'manual', 'pending']"
        :key="key"
        type="button"
        class="proc-chip"
        :data-tone="STATUS_TONE[key]"
        :class="{ active: filters.status === key }"
        @click="pickStatus(key)"
      >
        {{ statusLabel(key) }}<b>{{ counts[key] || 0 }}</b>
      </button>
      <span class="proc-counts-total">共 {{ totalCount }} 条{{ filters.status ? `，筛选后 ${total} 条` : '' }}</span>
    </div>

    <div class="proc-filters">
      <label>时间
        <select v-model.number="filters.days" @change="search">
          <option :value="1">最近 24 小时</option>
          <option :value="7">最近 7 天</option>
          <option :value="30">最近 30 天</option>
          <option :value="0">全部</option>
        </select>
      </label>
      <label>状态
        <select v-model="filters.status" @change="search">
          <option value="">全部</option>
          <option v-for="key in ['pending', 'succeeded', 'failed', 'manual']" :key="key" :value="key">
            {{ statusLabel(key) }}
          </option>
        </select>
      </label>
      <label>供应商
        <select v-model="filters.supplier_id" @change="search">
          <option value="">全部</option>
          <option v-for="supplier in suppliers" :key="supplier.id" :value="supplier.id">
            {{ supplier.name }}（{{ supplier.code }}）
          </option>
        </select>
      </label>
      <label class="wide">关键词
        <input
          v-model.trim="filters.keyword"
          placeholder="订单号 / 上游单号 / 错误码，如 BALANCE_LOW"
          @keyup.enter="search"
        />
      </label>
      <div class="proc-actions">
        <button class="proc-primary" type="button" :disabled="loading" @click="search">
          <Search :size="14" />查询
        </button>
        <button class="proc-ghost" type="button" :disabled="loading" @click="reset">重置</button>
      </div>
    </div>

    <p v-if="error" class="proc-error">{{ error }}</p>
    <p v-if="feedback" class="proc-feedback" role="status">{{ feedback }}</p>

    <div class="proc-table">
      <div class="proc-row proc-row-head">
        <span>时间</span><span>订单 / 客户</span><span>供应商</span><span>状态</span>
        <span>上游单号</span><span>失败原因</span><span>操作</span>
      </div>
      <div v-if="loading && !items.length" class="proc-empty">正在读取…</div>
      <div v-else-if="!items.length" class="proc-empty">这个条件下没有采购记录</div>
      <div v-for="row in items" :key="row.id" class="proc-row">
        <span class="proc-time">{{ formatTime(row.created_at) }}</span>
        <span class="proc-order">
          <b>{{ row.order_no || `#${row.order_id}` }}</b>
          <small>{{ row.customer_name || '—' }} · {{ row.product_name || '—' }}</small>
        </span>
        <span class="proc-supplier">{{ row.supplier_name || row.supplier_code || '—' }}</span>
        <span><em class="proc-tag" :data-tone="STATUS_TONE[row.status]">{{ statusLabel(row.status) }}</em></span>
        <span class="proc-external">{{ row.external_order_no || '—' }}</span>
        <span class="proc-fail">
          <template v-if="row.error_message">
            <b>{{ row.error_code || 'ERROR' }}</b> {{ row.error_message }}
          </template>
          <template v-else>—</template>
        </span>
        <span class="proc-ops">
          <button type="button" class="proc-link" @click="openDetail(row)">详情</button>
          <button v-if="canRetry(row)" type="button" class="proc-link" :disabled="retrying" @click="retry(row)">
            重试
          </button>
        </span>
      </div>
    </div>

    <footer class="proc-pager">
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

    <aside v-if="detail" class="proc-detail">
      <header>
        <div>
          <h2>采购详情 #{{ detail.id }}</h2>
          <p>{{ detail.order_no || '订单已删除' }} · {{ statusLabel(detail.status) }}</p>
        </div>
        <button type="button" class="proc-ghost" @click="closeDetail">关闭</button>
      </header>

      <p v-if="detailLoading" class="proc-detail-hint">正在读取…</p>
      <p v-else-if="detailError" class="proc-error">{{ detailError }}</p>

      <template v-else>
        <dl>
          <div><dt>上游单号</dt><dd>{{ detail.external_order_no || '—' }}</dd></div>
          <div><dt>交付线路</dt><dd>{{ detail.line_id ? `#${detail.line_id}` : '—' }}</dd></div>
          <div><dt>交付账号数</dt><dd>{{ detail.delivered_accounts ?? 0 }}</dd></div>
          <div><dt>供应商</dt><dd>{{ detail.supplier_name || detail.supplier_code || '—' }}</dd></div>
        </dl>

        <p v-if="detail.error_message" class="proc-detail-error">
          <AlertTriangle :size="14" />
          <span><b>{{ detail.error_code || 'ERROR' }}</b>{{ detail.error_message }}</span>
        </p>

        <div v-if="detail.request_summary" class="proc-detail-request">
          <h3>下单参数</h3>
          <dl>
            <div><dt>数量</dt><dd>{{ detail.request_summary.quantity ?? '—' }}</dd></div>
            <div><dt>周期</dt><dd>{{ detail.request_summary.billing_days ? `${detail.request_summary.billing_days} 天` : '—' }}</dd></div>
            <div><dt>国家</dt><dd>{{ detail.request_summary.country || '—' }}</dd></div>
            <div><dt>城市</dt><dd>{{ detail.request_summary.city || '—' }}</dd></div>
            <div><dt>号段</dt><dd>{{ detail.request_summary.segment || '—' }}</dd></div>
            <div><dt>触发</dt><dd>{{ detail.request_summary.trigger || '—' }}</dd></div>
          </dl>
        </div>

        <p class="proc-detail-note">
          上游返回的 IP、账号和密码不在此展示：它们已经写进客户线路，请到「客户线路」查看。
        </p>
      </template>
    </aside>
  </section>
</template>

<style scoped>
.proc-page { display: grid; gap: 14px; }
.proc-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.proc-eyebrow { display: inline-flex; align-items: center; gap: 6px; margin: 0 0 4px; font-size: 10px; letter-spacing: .12em; text-transform: uppercase; color: #6f817b; }
.proc-head h1 { margin: 0; font-size: 21px; letter-spacing: -.01em; }
.proc-desc { margin: 6px 0 0; font-size: 12px; color: #71837d; }
.proc-refresh { display: inline-flex; align-items: center; gap: 6px; height: 34px; padding: 0 12px; border: 1px solid #d5e0da; border-radius: 6px; background: #fff; color: #4f6559; font-size: 12px; cursor: pointer; }
.proc-refresh:disabled { opacity: .6; cursor: progress; }

.proc-counts { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; padding: 10px 12px; border: 1px solid #dbe4df; border-radius: 6px; background: #f7faf8; }
.proc-counts-label, .proc-counts-total { font-size: 10px; color: #7b8d87; }
.proc-counts-total { margin-left: auto; }
.proc-chip { display: inline-flex; align-items: center; gap: 5px; padding: 3px 9px; border: 1px solid transparent; border-radius: 999px; background: #eef3f0; color: #4f6559; font-size: 11px; cursor: pointer; }
.proc-chip b { font-weight: 700; }
.proc-chip[data-tone="ok"] { background: #e6f4ee; color: #1f6b52; }
.proc-chip[data-tone="bad"] { background: #fdecea; color: #98402f; }
.proc-chip[data-tone="warn"] { background: #fdf3e2; color: #86601c; }
.proc-chip.active { border-color: currentColor; }

.proc-filters { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 9px; padding: 13px; border: 1px solid #dbe4df; border-radius: 6px; background: #fff; }
.proc-filters label { display: grid; gap: 5px; font-size: 10px; color: #6b7d75; }
.proc-filters label.wide { grid-column: span 2; }
.proc-filters input, .proc-filters select { height: 32px; padding: 0 9px; border: 1px solid #cfdad4; border-radius: 5px; background: #fff; color: #2d4439; font: inherit; font-size: 12px; min-width: 0; }
.proc-actions { grid-column: 1 / -1; display: flex; gap: 8px; }
.proc-primary { display: inline-flex; align-items: center; gap: 6px; height: 32px; padding: 0 14px; border-radius: 5px; background: #2f9c85; color: #fff; font-size: 12px; font-weight: 600; cursor: pointer; }
.proc-ghost { height: 32px; padding: 0 12px; border: 1px solid #d5e0da; border-radius: 5px; background: #fff; color: #4f6559; font-size: 12px; cursor: pointer; }
.proc-primary:disabled, .proc-ghost:disabled { opacity: .6; cursor: progress; }

.proc-error { margin: 0; padding: 9px 12px; border-radius: 6px; background: #fdf0ec; color: #9a4a39; font-size: 12px; }
.proc-feedback { margin: 0; padding: 9px 12px; border-radius: 6px; background: #eaf4f0; color: #2c6b58; font-size: 12px; }

.proc-table { border: 1px solid #dbe4df; border-radius: 6px; overflow: hidden; background: #fff; }
.proc-row { display: grid; grid-template-columns: 116px minmax(0, 1.4fr) 110px 76px 120px minmax(0, 1.2fr) 100px; gap: 10px; align-items: center; padding: 9px 14px; border-top: 1px solid #eef3f0; font-size: 12px; }
.proc-row-head { border-top: 0; background: #eef3f1; color: #6f817c; font-size: 11px; }
.proc-time { color: #5d7169; font-variant-numeric: tabular-nums; font-size: 11px; }
.proc-order { display: grid; gap: 2px; min-width: 0; }
.proc-order b { color: #243b33; font-weight: 600; }
.proc-order small { color: #839691; font-size: 10px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.proc-supplier { color: #5d7169; font-size: 11px; }
.proc-tag { display: inline-block; padding: 2px 7px; border-radius: 4px; background: #eef3f0; color: #4f6559; font-size: 11px; font-style: normal; white-space: nowrap; }
.proc-tag[data-tone="ok"] { background: #e6f4ee; color: #1f6b52; }
.proc-tag[data-tone="bad"] { background: #fdecea; color: #98402f; }
.proc-tag[data-tone="warn"] { background: #fdf3e2; color: #86601c; }
.proc-external { color: #4d6259; font-size: 11px; overflow-wrap: anywhere; }
.proc-fail { color: #7b8d87; font-size: 11px; overflow-wrap: anywhere; }
.proc-fail b { color: #98402f; font-weight: 600; }
.proc-ops { display: flex; gap: 8px; }
.proc-link { padding: 0; border: 0; background: none; color: #17856b; font-size: 11px; cursor: pointer; }
.proc-link:disabled { opacity: .5; cursor: progress; }
.proc-empty { padding: 26px 14px; text-align: center; color: #90a09a; font-size: 12px; }

.proc-pager { display: flex; align-items: center; justify-content: space-between; gap: 12px; font-size: 11px; color: #7b8d87; }
.proc-pager div { display: flex; gap: 4px; }
.proc-pager button { min-width: 30px; height: 30px; padding: 0 8px; display: inline-grid; place-items: center; border: 1px solid #d5e0da; border-radius: 5px; background: #fff; color: #4f6559; font-size: 12px; cursor: pointer; }
.proc-pager button.active { background: #2f9c85; border-color: #2f9c85; color: #fff; }
.proc-pager button:disabled { opacity: .5; cursor: not-allowed; }

.proc-detail { border: 1px solid #cfe0d8; border-radius: 8px; background: #fff; padding: 16px; display: grid; gap: 12px; }
.proc-detail > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.proc-detail h2 { margin: 0; font-size: 15px; }
.proc-detail header p { margin: 3px 0 0; font-size: 11px; color: #7b8d87; }
.proc-detail dl { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin: 0; }
.proc-detail dl div { display: grid; gap: 3px; }
.proc-detail dt { font-size: 10px; color: #7b8d87; }
.proc-detail dd { margin: 0; font-size: 12px; color: #243b33; overflow-wrap: anywhere; }
.proc-detail-request h3 { margin: 0 0 8px; font-size: 12px; color: #4f6559; }
.proc-detail-error { display: flex; align-items: flex-start; gap: 7px; margin: 0; padding: 9px 11px; border-radius: 6px; background: #fdf0ec; color: #9a4a39; font-size: 12px; }
.proc-detail-error b { margin-right: 6px; }
.proc-detail-note { margin: 0; font-size: 11px; color: #839691; }
.proc-detail-hint { margin: 0; font-size: 12px; color: #90a09a; }

@media (max-width: 980px) {
  .proc-filters { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .proc-filters label.wide { grid-column: 1 / -1; }
  .proc-row { grid-template-columns: 1fr; gap: 4px; padding: 12px; }
  .proc-row-head { display: none; }
  .proc-detail dl { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
