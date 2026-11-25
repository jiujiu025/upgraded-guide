<script setup>
// 管理员的线路健康：左边是「所有客户线路」，右边是「面板上的所有入站」。
//
// 两个视图解决的是两个不同问题：
// - 客户线路：这条线是谁的、哪个销售在跟、连的哪个入口、出口 IP 是多少；
// - 面板入站：面板上还有哪些入站，包括人工在 3x-ui 上直接建的（系统里没有记录）。
// 只看 Node 是看不出这些的——同一台出口可能卖给多个客户。
import { computed, onMounted, reactive, ref } from 'vue'
import {
  Activity, ChevronLeft, ChevronRight, CircleAlert, RefreshCw, Search, Server, Users,
} from 'lucide-vue-next'
import {
  checkAdminCustomerLines, checkTransitInbounds, getAdminCustomerLines, getHealthOverview,
  getTransitInbounds,
} from '../api'

const HEALTH_META = {
  healthy: { label: '正常', tone: 'ok' },
  offline: { label: '离线', tone: 'bad' },
  degraded: { label: '延迟升高', tone: 'warn' },
  unknown: { label: '待检测', tone: 'wait' },
}
const LINE_STATUS = { active: '使用中', expired: '已到期', disabled: '已停用', deleted: '已删除' }

const tab = ref('lines')
const loading = ref(true)
const error = ref('')
const notice = ref('')
const overview = ref(null)

const lines = ref([])
const total = ref(0)
const page = ref(1)
const pages = ref(1)
const pageSize = 25
const counts = ref({ healthy: 0, offline: 0, unknown: 0 })
const options = ref({ sales: [], lines: [], countries: [] })
const filters = reactive({ sales_id: '', line_code: '', country: '', health: '', keyword: '' })
const checking = ref(false)

const inbounds = ref([])
const panels = ref([])
const panelErrors = ref([])
const inboundLoading = ref(false)
const inboundVpsId = ref('')
const inboundChecked = ref(false)
const inboundBusy = ref(false)
const inboundChecks = ref({})

const hasFilters = computed(() => Object.values(filters).some((value) => value !== ''))
const totalCount = computed(() => counts.value.healthy + counts.value.offline + counts.value.unknown)

function flash(message) {
  notice.value = message
  window.setTimeout(() => { if (notice.value === message) notice.value = '' }, 5000)
}

function healthMeta(value) {
  return HEALTH_META[value] || HEALTH_META.unknown
}

function formatTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (number) => String(number).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function formatDate(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (number) => String(number).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

async function loadOverview() {
  try {
    const { data } = await getHealthOverview()
    overview.value = data
  } catch (err) {
    error.value = err.userMessage || '概览加载失败'
  }
}

async function loadLines() {
  loading.value = true
  error.value = ''
  try {
    const params = { page: page.value, page_size: pageSize }
    Object.entries(filters).forEach(([key, value]) => { if (value) params[key] = value })
    const { data } = await getAdminCustomerLines(params)
    lines.value = data.items || []
    total.value = data.total || 0
    pages.value = Math.max(1, Math.ceil((data.total || 0) / pageSize))
    counts.value = data.counts || counts.value
    if (data.options) options.value = data.options
  } catch (err) {
    error.value = err.userMessage || '客户线路加载失败'
  } finally {
    loading.value = false
  }
}

function search() {
  page.value = 1
  loadLines()
}

function reset() {
  Object.keys(filters).forEach((key) => { filters[key] = '' })
  page.value = 1
  loadLines()
}

function pickStatus(value) {
  filters.health = filters.health === value ? '' : value
  search()
}

function turnPage(next) {
  const target = Math.min(Math.max(1, next), pages.value)
  if (target === page.value) return
  page.value = target
  loadLines()
}

async function runLineCheck(scope) {
  checking.value = true
  error.value = ''
  try {
    const payload = scope === 'all'
      ? { all: true }
      : { line_ids: lines.value.map((item) => item.line_id) }
    const { data } = await checkAdminCustomerLines(payload)
    flash(`检测完成：${data.checked} 台节点，${data.healthy} 正常、${data.offline} 不通`)
    await Promise.all([loadLines(), loadOverview()])
  } catch (err) {
    error.value = err.userMessage || '检测失败'
  } finally {
    checking.value = false
  }
}

async function checkOne(line) {
  if (!line.line_id) return
  checking.value = true
  try {
    const { data } = await checkAdminCustomerLines({ line_ids: [line.line_id] })
    const node = data.nodes?.[0]
    flash(node
      ? `${line.customer_name}：${node.status === 'healthy' ? '连通' : '不通'}${node.latency_ms ? `，${node.latency_ms} ms` : ''}`
      : '没有可检测的节点')
    await loadLines()
  } catch (err) {
    error.value = err.userMessage || '检测失败'
  } finally {
    checking.value = false
  }
}

async function loadInbounds() {
  inboundLoading.value = true
  error.value = ''
  try {
    const params = {}
    if (inboundVpsId.value) params.vps_id = inboundVpsId.value
    const { data } = await getTransitInbounds(params)
    inbounds.value = data.items || []
    panels.value = data.panels || []
    panelErrors.value = data.errors || []
    inboundChecks.value = {}
    inboundChecked.value = false
  } catch (err) {
    error.value = err.userMessage || '面板入站加载失败'
  } finally {
    inboundLoading.value = false
  }
}

async function runInboundCheck() {
  inboundBusy.value = true
  error.value = ''
  try {
    const payload = { include_outbound: false }
    if (inboundVpsId.value) payload.vps_id = Number(inboundVpsId.value)
    const { data } = await checkTransitInbounds(payload)
    const map = {}
    for (const item of data.checks || []) map[`${item.vps_name}:${item.port}:${item.kind}`] = item
    inboundChecks.value = map
    inboundChecked.value = true
    flash(`入站检测完成：${data.healthy} 通、${data.offline} 不通`)
  } catch (err) {
    error.value = err.userMessage || '入站检测失败'
  } finally {
    inboundBusy.value = false
  }
}

function inboundResult(item) {
  return inboundChecks.value[`${item.vps_name}:${item.port}:inbound`] || null
}

function pickTab(key) {
  tab.value = key
  error.value = ''
  if (key === 'inbounds' && !inbounds.value.length && !inboundLoading.value) loadInbounds()
}

onMounted(() => {
  loadOverview()
  loadLines()
})
</script>

<template>
  <section class="hl-page">
    <header class="hl-head">
      <div>
        <p class="hl-eyebrow"><Activity :size="13" /> 线路健康</p>
        <h1>所有客户线路与面板入站</h1>
        <p class="hl-desc">
          按销售、客户、产品、国家筛选每一条线路，也能看到面板上所有入站（含人工直接建的）。
          检测是 TCP 连通性探测，不改动任何配置。
        </p>
      </div>
      <div class="hl-head-actions">
        <button class="hl-ghost" type="button" :disabled="loading" @click="() => { loadOverview(); loadLines() }">
          <RefreshCw :size="14" />刷新
        </button>
        <button class="hl-primary" type="button" :disabled="checking" @click="runLineCheck('all')">
          <RefreshCw :size="14" :class="{ spin: checking }" />{{ checking ? '检测中…' : '全量检测' }}
        </button>
      </div>
    </header>

    <p v-if="notice" class="hl-notice">{{ notice }}</p>
    <p v-if="error" class="hl-error"><CircleAlert :size="14" />{{ error }}</p>

    <div class="hl-cards">
      <article><span>客户线路</span><b>{{ overview?.lines.total ?? '—' }}</b><small>{{ overview?.customers ?? 0 }} 个客户</small></article>
      <article><span>正常</span><b class="ok">{{ overview?.lines.healthy ?? '—' }}</b><small>平均 {{ overview?.average_latency_ms ?? 0 }} ms</small></article>
      <article><span>离线</span><b class="bad">{{ overview?.lines.offline ?? '—' }}</b><small>需要现场确认</small></article>
      <article><span>待检测</span><b class="wait">{{ overview?.lines.unknown ?? '—' }}</b><small>从未探测过</small></article>
      <article><span>面板</span><b>{{ overview?.panels.enabled ?? '—' }}</b><small>共 {{ overview?.panels.total ?? 0 }} 台</small></article>
    </div>

    <nav class="hl-tabs">
      <button type="button" :class="{ active: tab === 'lines' }" @click="pickTab('lines')"><Users :size="14" />客户线路</button>
      <button type="button" :class="{ active: tab === 'inbounds' }" @click="pickTab('inbounds')"><Server :size="14" />面板入站</button>
      <span v-if="tab === 'lines'" class="hl-tabs-meta">当前条件 {{ total }} 条 / 共 {{ totalCount }} 条</span>
      <span v-else class="hl-tabs-meta">{{ inbounds.length }} 个入站 · {{ panels.length }} 台面板</span>
    </nav>

    <template v-if="tab === 'lines'">
      <div class="hl-chips">
        <button
          v-for="key in ['healthy', 'offline', 'unknown']" :key="key" type="button"
          class="hl-chip" :data-tone="HEALTH_META[key].tone"
          :class="{ active: filters.health === key }" @click="pickStatus(key)"
        >{{ HEALTH_META[key].label }}<b>{{ counts[key] || 0 }}</b></button>
      </div>

      <div class="hl-filters">
        <label>销售
          <select v-model="filters.sales_id" @change="search">
            <option value="">全部销售</option>
            <option v-for="item in options.sales" :key="item.id" :value="String(item.id)">{{ item.name }}</option>
          </select>
        </label>
        <label>产品线
          <select v-model="filters.line_code" @change="search">
            <option value="">全部产品</option>
            <option v-for="item in options.lines" :key="item.code" :value="item.code">{{ item.label }}</option>
          </select>
        </label>
        <label>国家
          <select v-model="filters.country" @change="search">
            <option value="">全部国家</option>
            <option v-for="code in options.countries" :key="code" :value="code">{{ code }}</option>
          </select>
        </label>
        <label>状态
          <select v-model="filters.health" @change="search">
            <option value="">全部状态</option>
            <option v-for="(meta, key) in HEALTH_META" :key="key" :value="key">{{ meta.label }}</option>
          </select>
        </label>
        <label class="wide">搜索
          <input
            v-model.trim="filters.keyword" placeholder="客户 / 订单号 / IP / 出站名 / 面板，回车搜索"
            @keyup.enter="search"
          />
        </label>
        <div class="hl-filter-actions">
          <button class="hl-primary" type="button" :disabled="loading" @click="search"><Search :size="14" />查询</button>
          <button class="hl-ghost" type="button" :disabled="loading || !hasFilters" @click="reset">重置</button>
          <button class="hl-ghost" type="button" :disabled="checking || !lines.length" @click="runLineCheck('page')">
            检测当页
          </button>
        </div>
      </div>

      <p v-if="loading" class="hl-empty">正在读取客户线路…</p>
      <p v-else-if="!lines.length" class="hl-empty">没有符合条件的线路{{ hasFilters ? '，换个条件或清除筛选试试。' : '。' }}</p>

      <div v-else class="hl-table">
        <div class="hl-tr hl-th">
          <span>客户 / 销售</span><span>产品 / 订单</span><span>入口 → 出口</span>
          <span>中转</span><span>健康</span><span>延迟</span><span>到期</span><span></span>
        </div>
        <div v-for="line in lines" :key="line.line_id" class="hl-tr">
          <span>
            <b>{{ line.customer_name }}</b>
            <small>{{ line.sales_name || '未分配销售' }}</small>
          </span>
          <span>
            <b>{{ line.line_label }}</b>
            <small>{{ line.order_no || '手动建线' }}{{ line.region_name ? ` · ${line.region_name}` : '' }}</small>
          </span>
          <span>
            <b class="mono">{{ line.entry }}</b>
            <small class="mono">{{ line.exit_ip || '—' }}</small>
          </span>
          <span>
            <template v-if="line.vps_name">
              <b>{{ line.vps_name }} · {{ line.inbound_port }}</b>
              <small>{{ line.outbound_tag || '未绑定出站' }}</small>
            </template>
            <template v-else>
              <b class="muted">直连</b>
              <small>{{ line.protocol || '—' }}</small>
            </template>
          </span>
          <span>
            <em class="hl-badge" :data-tone="healthMeta(line.health_status).tone">
              {{ healthMeta(line.health_status).label }}
            </em>
            <small>{{ LINE_STATUS[line.line_status] || line.line_status }}</small>
          </span>
          <span>{{ line.health_latency_ms ? `${line.health_latency_ms} ms` : '—' }}<small>{{ formatTime(line.health_checked_at) }}</small></span>
          <span>{{ formatDate(line.expires_at) }}</span>
          <span><button class="hl-mini" type="button" :disabled="checking" @click="checkOne(line)">检测</button></span>
        </div>
      </div>

      <div v-if="pages > 1" class="hl-pager">
        <button type="button" :disabled="page <= 1" @click="turnPage(page - 1)"><ChevronLeft :size="14" />上一页</button>
        <span>第 {{ page }} / {{ pages }} 页</span>
        <button type="button" :disabled="page >= pages" @click="turnPage(page + 1)">下一页<ChevronRight :size="14" /></button>
      </div>
    </template>

    <template v-else>
      <div class="hl-filters">
        <label>面板
          <select v-model="inboundVpsId" @change="loadInbounds">
            <option value="">全部启用中的面板</option>
            <option v-for="panel in panels" :key="panel.id" :value="String(panel.id)">
              {{ panel.name }}（{{ panel.group_code }}）
            </option>
          </select>
        </label>
        <div class="hl-filter-actions">
          <button class="hl-ghost" type="button" :disabled="inboundLoading" @click="loadInbounds">
            <RefreshCw :size="14" />{{ inboundLoading ? '读取中…' : '重新读取' }}
          </button>
          <button class="hl-primary" type="button" :disabled="inboundBusy || !inbounds.length" @click="runInboundCheck">
            {{ inboundBusy ? '检测中…' : '检测入站端口' }}
          </button>
        </div>
      </div>

      <p v-if="panelErrors.length" class="hl-error">
        <CircleAlert :size="14" />
        {{ panelErrors.map((item) => `${item.vps_name}：${item.error}`).join('；') }}
      </p>

      <p v-if="inboundLoading" class="hl-empty">正在从面板读取入站…</p>
      <p v-else-if="!inbounds.length" class="hl-empty">没有读到入站。先确认面板凭据和地址是否可用。</p>

      <div v-else class="hl-table wide">
        <div class="hl-tr hl-th">
          <span>面板</span><span>入站</span><span>出站</span><span>客户 / 销售</span><span>检测</span>
        </div>
        <div v-for="item in inbounds" :key="`${item.vps_name}-${item.inbound_id}`" class="hl-tr">
          <span><b>{{ item.vps_name }}</b><small>{{ item.group_code }} · {{ item.connect_host }}</small></span>
          <span>
            <b>#{{ item.inbound_id }} · {{ item.port }}</b>
            <small>{{ item.remark || '未命名' }}{{ item.enable ? '' : '（已停用）' }}</small>
          </span>
          <span>
            <b>{{ item.outbound_tag || '未绑定' }}</b>
            <small class="mono">{{ item.exit_host ? `${item.exit_host}:${item.exit_port}` : '没有对应路由规则' }}</small>
          </span>
          <span>
            <template v-if="item.matched">
              <b>{{ item.matched.customer_name }}</b>
              <small>{{ item.matched.sales_name || '未分配销售' }}</small>
            </template>
            <template v-else>
              <b class="muted">未关联客户</b>
              <small>人工建的或已换绑</small>
            </template>
          </span>
          <span>
            <template v-if="inboundChecked">
              <em class="hl-badge" :data-tone="inboundResult(item)?.ok ? 'ok' : 'bad'">
                {{ inboundResult(item)?.ok ? `通 ${inboundResult(item).latency_ms} ms` : '不通' }}
              </em>
            </template>
            <span v-else class="muted">未检测</span>
          </span>
        </div>
      </div>
      <p v-if="inbounds.length" class="hl-hint">
        出站是代理网关时，裸 TCP 探测不一定能测出真实可用性；这里只判断入站端口是否可达。
      </p>
    </template>
  </section>
</template>

<style scoped>
.hl-page{min-height:100vh;padding:28px 32px 48px;background:#f2f5f3;color:#20342d}
.hl-head{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;margin-bottom:16px}
.hl-eyebrow{margin:0 0 6px;display:flex;align-items:center;gap:6px;color:#17856b;font-size:11px;font-weight:650;letter-spacing:.04em}
.hl-head h1{margin:0;font-size:22px;font-weight:680}
.hl-desc{max-width:680px;margin:8px 0 0;color:#5f7469;font-size:12px;line-height:1.7}
.hl-head-actions{display:flex;gap:8px;flex:none}
.hl-primary{display:inline-flex;align-items:center;gap:6px;min-height:34px;padding:0 13px;border-radius:5px;background:#17795f;color:#fff;font-size:12px;font-weight:650}
.hl-ghost{display:inline-flex;align-items:center;gap:6px;min-height:34px;padding:0 12px;border:1px solid #d3e0d9;border-radius:5px;background:#fff;color:#3f6353;font-size:12px;font-weight:600}
.hl-primary:disabled,.hl-ghost:disabled{opacity:.6;cursor:wait}
.hl-notice{margin:10px 0;padding:9px 12px;border:1px solid #c6e4d5;border-radius:6px;background:#e8f5ee;color:#176b58;font-size:12px}
.hl-error{display:flex;align-items:center;gap:7px;margin:10px 0;padding:9px 12px;border:1px solid #f2cfcf;border-radius:6px;background:#fcecec;color:#a23232;font-size:12px}
.hl-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:16px 0}
.hl-cards article{padding:14px 16px;border:1px solid #dfe8e3;border-radius:8px;background:#fff}
.hl-cards span{display:block;color:#7c8f86;font-size:11px}
.hl-cards b{display:block;margin:4px 0 2px;font-size:24px;font-weight:700;line-height:1.1}
.hl-cards b.ok{color:#176b58}.hl-cards b.bad{color:#b03a35}.hl-cards b.wait{color:#8a6318}
.hl-cards small{color:#8a9b93;font-size:10px}
.hl-tabs{display:flex;align-items:center;gap:6px;margin:6px 0 14px;border-bottom:1px solid #dbe5e0}
.hl-tabs button{min-height:40px;padding:0 14px;display:flex;align-items:center;gap:6px;border-bottom:2px solid transparent;color:#5f7469;font-size:12px;font-weight:650}
.hl-tabs button.active{color:#176b58;border-bottom-color:#17856b}
.hl-tabs-meta{margin-left:auto;color:#7c8f86;font-size:11px}
.hl-chips{display:flex;gap:8px;margin-bottom:12px}
.hl-chip{display:inline-flex;align-items:center;gap:6px;min-height:30px;padding:0 11px;border:1px solid #dfe8e3;border-radius:99px;background:#fff;color:#5f7469;font-size:11px;font-weight:600}
.hl-chip b{font-size:11px}
.hl-chip[data-tone="ok"].active{border-color:#9fd3ba;background:#eaf7f0;color:#176b58}
.hl-chip[data-tone="bad"].active{border-color:#eec4c2;background:#fcedec;color:#a23232}
.hl-chip[data-tone="wait"].active{border-color:#e8d9b4;background:#fbf4e6;color:#8a6318}
.hl-filters{display:flex;flex-wrap:wrap;align-items:flex-end;gap:10px 12px;margin-bottom:14px}
.hl-filters label{display:grid;gap:4px;color:#6c8077;font-size:10px;font-weight:600}
.hl-filters select,.hl-filters input{min-height:32px;min-width:132px;padding:0 9px;border:1px solid #d3e0d9;border-radius:5px;background:#fff;color:#20342d;font-size:12px;font-weight:400}
.hl-filters .wide input{min-width:280px}
.hl-filter-actions{display:flex;gap:8px;margin-left:auto}
.hl-empty{margin:26px 0;color:#7c8f86;font-size:12px}
.hl-table{border:1px solid #dbe5e0;border-radius:8px;background:#fff;overflow:hidden}
.hl-tr{display:grid;grid-template-columns:minmax(120px,1.2fr) minmax(130px,1.2fr) minmax(150px,1.3fr) minmax(110px,1fr) 86px 86px 92px 60px;gap:10px;align-items:center;padding:10px 14px;border-top:1px solid #eef3f0;font-size:12px}
.hl-table.wide .hl-tr{grid-template-columns:140px minmax(150px,1.2fr) minmax(160px,1.4fr) minmax(140px,1.2fr) 110px}
.hl-tr:first-child{border-top:0}
.hl-th{background:#f7faf8;color:#7c8f86;font-size:10px;font-weight:650;letter-spacing:.03em}
.hl-tr b{display:block;font-size:12px;font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hl-tr small{display:block;color:#83958c;font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hl-tr .mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px}
.hl-tr .muted{color:#93a49c}
.hl-badge{display:inline-block;padding:2px 7px;border-radius:99px;font-size:10px;font-style:normal;font-weight:650;white-space:nowrap}
.hl-badge[data-tone="ok"]{background:#e7f6ee;color:#176b58}
.hl-badge[data-tone="bad"]{background:#fcecec;color:#a23232}
.hl-badge[data-tone="warn"]{background:#fdf2e2;color:#8a6318}
.hl-badge[data-tone="wait"]{background:#eef1ef;color:#6d8077}
.hl-mini{min-height:26px;padding:0 9px;border:1px solid #d3e0d9;border-radius:5px;background:#fff;color:#3f6353;font-size:11px;font-weight:600}
.hl-mini:disabled{opacity:.6;cursor:wait}
.hl-pager{display:flex;align-items:center;justify-content:center;gap:12px;margin:16px 0;color:#6c8077;font-size:11px}
.hl-pager button{display:inline-flex;align-items:center;gap:4px;min-height:30px;padding:0 10px;border:1px solid #d3e0d9;border-radius:5px;background:#fff;color:#3f6353;font-size:11px;font-weight:600}
.hl-pager button:disabled{opacity:.5;cursor:not-allowed}
.hl-hint{margin:10px 0 0;color:#83958c;font-size:11px}
.spin{animation:hl-spin 1s linear infinite}
@keyframes hl-spin{to{transform:rotate(360deg)}}
@media(max-width:1100px){
  .hl-page{padding:22px 16px 40px}
  .hl-tr,.hl-table.wide .hl-tr{grid-template-columns:1fr 1fr}
  .hl-th{display:none}
  .hl-head{flex-direction:column}
  .hl-filter-actions{margin-left:0}
}
@media(prefers-reduced-motion:reduce){.spin{animation:none}}
</style>
