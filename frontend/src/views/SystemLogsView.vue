<script setup>
// 系统日志：排障用。回答"系统在哪一步失败了"——上游扣款后中转失败、面板连不上、
// 密钥解不开这类。和「操作审计」不同，那里记的是人做了什么，这里记的是机器做了什么。
import { computed, onMounted, reactive, ref } from 'vue'
import { ChevronLeft, ChevronRight, CircleAlert, RefreshCw, Search, Terminal } from 'lucide-vue-next'
import { getSystemLogs } from '../api'

const LEVEL_META = {
  error: { label: '错误', tone: 'bad' },
  warning: { label: '警告', tone: 'warn' },
  info: { label: '信息', tone: 'wait' },
}
const CATEGORY_LABELS = {
  procurement: '采购',
  transit: '中转',
  impersonation: '模拟登录',
  health: '健康检测',
  panel: '面板',
  order: '订单',
  system: '系统',
}

const loading = ref(true)
const error = ref('')
const items = ref([])
const total = ref(0)
const page = ref(1)
const pages = ref(1)
const pageSize = 25
const counts = ref({ error: 0, warning: 0, info: 0 })
const options = ref({ categories: [], levels: [] })
const filters = reactive({ level: '', category: '', keyword: '', days: 7 })

const hasFilters = computed(() => Boolean(filters.level || filters.category || filters.keyword))

function formatTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (number) => String(number).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

function levelMeta(value) {
  return LEVEL_META[value] || LEVEL_META.info
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const params = { page: page.value, page_size: pageSize, days: filters.days }
    if (filters.level) params.level = filters.level
    if (filters.category) params.category = filters.category
    if (filters.keyword) params.keyword = filters.keyword
    const { data } = await getSystemLogs(params)
    items.value = data.items || []
    total.value = data.total || 0
    pages.value = Math.max(1, Math.ceil((data.total || 0) / pageSize))
    counts.value = data.counts || counts.value
    if (data.options) options.value = data.options
  } catch (err) {
    error.value = err.userMessage || '日志加载失败'
  } finally {
    loading.value = false
  }
}

function search() {
  page.value = 1
  load()
}

function pickLevel(value) {
  filters.level = filters.level === value ? '' : value
  search()
}

function reset() {
  Object.assign(filters, { level: '', category: '', keyword: '', days: 7 })
  search()
}

function turnPage(next) {
  const target = Math.min(Math.max(1, next), pages.value)
  if (target === page.value) return
  page.value = target
  load()
}

onMounted(load)
</script>

<template>
  <section class="sl-page">
    <header class="sl-head">
      <div>
        <p class="sl-eyebrow"><Terminal :size="13" /> 系统日志</p>
        <h1>采购、中转、模拟登录的运行记录</h1>
        <p class="sl-desc">
          记录系统在关键链路上做了什么、哪一步失败。只看不改，保留 30 天。
          需要查"谁改了客户资料"请去「操作审计」。
        </p>
      </div>
      <button class="sl-ghost" type="button" :disabled="loading" @click="load">
        <RefreshCw :size="14" />{{ loading ? '加载中…' : '刷新' }}
      </button>
    </header>

    <p v-if="error" class="sl-error"><CircleAlert :size="14" />{{ error }}</p>

    <div class="sl-chips">
      <button
        v-for="level in ['error', 'warning', 'info']" :key="level" type="button"
        class="sl-chip" :data-tone="LEVEL_META[level].tone" :class="{ active: filters.level === level }"
        @click="pickLevel(level)"
      >{{ LEVEL_META[level].label }}<b>{{ counts[level] || 0 }}</b></button>
      <span class="sl-chips-meta">近 7 天 · 当前条件 {{ total }} 条</span>
    </div>

    <div class="sl-filters">
      <label>分类
        <select v-model="filters.category" @change="search">
          <option value="">全部分类</option>
          <option v-for="code in options.categories" :key="code" :value="code">
            {{ CATEGORY_LABELS[code] || code }}
          </option>
        </select>
      </label>
      <label>时间
        <select v-model.number="filters.days" @change="search">
          <option :value="1">最近 24 小时</option>
          <option :value="7">最近 7 天</option>
          <option :value="30">最近 30 天</option>
        </select>
      </label>
      <label class="wide">搜索
        <input
          v-model.trim="filters.keyword" placeholder="订单号 / 面板名 / 错误信息，回车搜索"
          @keyup.enter="search"
        />
      </label>
      <div class="sl-filter-actions">
        <button class="sl-primary" type="button" :disabled="loading" @click="search"><Search :size="14" />查询</button>
        <button class="sl-ghost" type="button" :disabled="loading || !hasFilters" @click="reset">重置</button>
      </div>
    </div>

    <p v-if="loading" class="sl-empty">正在读取日志…</p>
    <p v-else-if="!items.length" class="sl-empty">
      没有符合条件的日志{{ hasFilters ? '，换个条件或清除筛选试试。' : '。' }}
    </p>

    <div v-else class="sl-table">
      <div class="sl-tr sl-th"><span>时间</span><span>级别</span><span>分类</span><span>内容</span><span>关联</span></div>
      <details v-for="item in items" :key="item.id" class="sl-row">
        <summary class="sl-tr">
          <span class="sl-time">{{ formatTime(item.created_at) }}</span>
          <span><em class="sl-badge" :data-tone="levelMeta(item.level).tone">{{ levelMeta(item.level).label }}</em></span>
          <span>{{ CATEGORY_LABELS[item.category] || item.category }}</span>
          <span class="sl-message"><b>{{ item.message }}</b><small>{{ item.action }}</small></span>
          <span class="sl-link">
            <template v-if="item.order_id">订单 #{{ item.order_id }}</template>
            <template v-else-if="item.vps_id">面板 #{{ item.vps_id }}</template>
            <template v-else>—</template>
          </span>
        </summary>
        <div class="sl-detail">
          <p v-if="item.detail" class="sl-detail-body">{{ item.detail }}</p>
          <p v-else class="sl-detail-body muted">这条日志没有附带上下文。</p>
          <p class="sl-detail-meta">
            <span>动作：{{ item.action }}</span>
            <span v-if="item.actor_id">操作人：#{{ item.actor_id }}</span>
            <span v-if="item.order_id">订单：#{{ item.order_id }}</span>
            <span v-if="item.line_id">线路：#{{ item.line_id }}</span>
            <span v-if="item.vps_id">面板：#{{ item.vps_id }}</span>
          </p>
        </div>
      </details>
    </div>

    <div v-if="pages > 1" class="sl-pager">
      <button type="button" :disabled="page <= 1" @click="turnPage(page - 1)"><ChevronLeft :size="14" />上一页</button>
      <span>第 {{ page }} / {{ pages }} 页</span>
      <button type="button" :disabled="page >= pages" @click="turnPage(page + 1)">下一页<ChevronRight :size="14" /></button>
    </div>
  </section>
</template>

<style scoped>
.sl-page{min-height:100vh;padding:28px 32px 48px;background:#f2f5f3;color:#20342d}
.sl-head{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;margin-bottom:16px}
.sl-eyebrow{margin:0 0 6px;display:flex;align-items:center;gap:6px;color:#17856b;font-size:11px;font-weight:650;letter-spacing:.04em}
.sl-head h1{margin:0;font-size:22px;font-weight:680}
.sl-desc{max-width:660px;margin:8px 0 0;color:#5f7469;font-size:12px;line-height:1.7}
.sl-ghost{display:inline-flex;align-items:center;gap:6px;min-height:34px;padding:0 12px;border:1px solid #d3e0d9;border-radius:5px;background:#fff;color:#3f6353;font-size:12px;font-weight:600;flex:none}
.sl-primary{display:inline-flex;align-items:center;gap:6px;min-height:34px;padding:0 13px;border-radius:5px;background:#17795f;color:#fff;font-size:12px;font-weight:650}
.sl-primary:disabled,.sl-ghost:disabled{opacity:.6;cursor:wait}
.sl-error{display:flex;align-items:center;gap:7px;margin:10px 0;padding:9px 12px;border:1px solid #f2cfcf;border-radius:6px;background:#fcecec;color:#a23232;font-size:12px}
.sl-chips{display:flex;align-items:center;gap:8px;margin-bottom:12px}
.sl-chip{display:inline-flex;align-items:center;gap:6px;min-height:30px;padding:0 11px;border:1px solid #dfe8e3;border-radius:99px;background:#fff;color:#5f7469;font-size:11px;font-weight:600}
.sl-chip[data-tone="bad"].active{border-color:#eec4c2;background:#fcedec;color:#a23232}
.sl-chip[data-tone="warn"].active{border-color:#e8d9b4;background:#fbf4e6;color:#8a6318}
.sl-chip[data-tone="wait"].active{border-color:#cfdcd7;background:#eef3f1;color:#4d6459}
.sl-chips-meta{margin-left:auto;color:#7c8f86;font-size:11px}
.sl-filters{display:flex;flex-wrap:wrap;align-items:flex-end;gap:10px 12px;margin-bottom:14px}
.sl-filters label{display:grid;gap:4px;color:#6c8077;font-size:10px;font-weight:600}
.sl-filters select,.sl-filters input{min-height:32px;min-width:130px;padding:0 9px;border:1px solid #d3e0d9;border-radius:5px;background:#fff;color:#20342d;font-size:12px;font-weight:400}
.sl-filters .wide input{min-width:280px}
.sl-filter-actions{display:flex;gap:8px;margin-left:auto}
.sl-empty{margin:26px 0;color:#7c8f86;font-size:12px}
.sl-table{border:1px solid #dbe5e0;border-radius:8px;background:#fff;overflow:hidden}
.sl-row{border-top:1px solid #eef3f0}
.sl-row:first-child{border-top:0}
.sl-row summary{list-style:none;cursor:pointer}
.sl-row summary::-webkit-details-marker{display:none}
.sl-row[open]{background:#fafcfb}
.sl-tr{display:grid;grid-template-columns:150px 70px 90px minmax(0,1fr) 110px;gap:10px;align-items:center;padding:10px 14px;font-size:12px}
.sl-th{background:#f7faf8;color:#7c8f86;font-size:10px;font-weight:650;letter-spacing:.03em}
.sl-time{color:#5f7469;font-size:11px;font-variant-numeric:tabular-nums}
.sl-message{min-width:0}
.sl-message b{display:block;font-size:12px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sl-message small{display:block;color:#93a49c;font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sl-link{color:#5f7469;font-size:11px}
.sl-badge{display:inline-block;padding:2px 7px;border-radius:99px;font-size:10px;font-style:normal;font-weight:650}
.sl-badge[data-tone="bad"]{background:#fcecec;color:#a23232}
.sl-badge[data-tone="warn"]{background:#fdf2e2;color:#8a6318}
.sl-badge[data-tone="wait"]{background:#eef1ef;color:#6d8077}
.sl-detail{padding:0 14px 12px;border-top:1px dashed #e5eeea}
.sl-detail-body{margin:10px 0 0;padding:10px 12px;border-radius:6px;background:#f5f8f6;color:#3d5148;font-size:11px;line-height:1.7;white-space:pre-wrap;word-break:break-word}
.sl-detail-body.muted{color:#93a49c}
.sl-detail-meta{display:flex;flex-wrap:wrap;gap:12px;margin:8px 0 0;color:#83958c;font-size:10px}
.sl-pager{display:flex;align-items:center;justify-content:center;gap:12px;margin:16px 0;color:#6c8077;font-size:11px}
.sl-pager button{display:inline-flex;align-items:center;gap:4px;min-height:30px;padding:0 10px;border:1px solid #d3e0d9;border-radius:5px;background:#fff;color:#3f6353;font-size:11px;font-weight:600}
.sl-pager button:disabled{opacity:.5;cursor:not-allowed}
@media(max-width:900px){
  .sl-page{padding:22px 16px 40px}
  .sl-tr{grid-template-columns:1fr 1fr}
  .sl-th{display:none}
  .sl-filter-actions{margin-left:0}
  .sl-head{flex-direction:column}
}
</style>
