<script setup>
import BrandLogo from '../components/BrandLogo.vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { Activity, AlertTriangle, BellRing, Box, ChevronRight, CircleAlert, Clock3, Database, Gauge, LogOut, Mail, Menu, MessageSquare, RefreshCw, Search, Server, ShieldCheck, SlidersHorizontal, UserRound, X } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import { loadHealthFixture } from '../mocks/health'
import { session } from '../api/session'
import { logout as endSession } from '../api'

const route = useRoute()
const router = useRouter()
const loading = ref(true)
const error = ref('')
const dataset = ref(null)
const selectedLine = ref(null)
const mobileNav = ref(false)
const notice = ref('')
const filters = reactive({ status: '', region: '', product: '', owner: '', keyword: '' })
const storedUser = computed(() => session.user || {})
const isSales = computed(() => route.meta.workspace === 'sales')
const nav = computed(() => isSales.value
  ? [['销售概览', '/sales', Gauge], ['我的客户影响', '/sales/health', Activity], ['客户会话', '/sales/inbox', MessageSquare]]
  : [['订单处理', '/admin', Box], ['线路健康', '/admin/health', Activity], ['全量会话', '/admin/inbox', MessageSquare], ['资源池', '/admin/resource-pool', Database]])
const statusMeta = {
  offline: { label: '离线', tone: 'critical' },
  abnormal: { label: '异常', tone: 'danger' },
  degraded: { label: '延迟升高', tone: 'warning' },
  pending: { label: '待检测', tone: 'muted' },
  normal: { label: '正常', tone: 'healthy' },
}
const regions = computed(() => [...new Set((dataset.value?.lines || []).map((item) => item.region))])
const products = computed(() => [...new Set((dataset.value?.lines || []).map((item) => item.product))])
const owners = computed(() => [...new Set((dataset.value?.lines || []).map((item) => item.owner))])
const filteredLines = computed(() => (dataset.value?.lines || []).filter((item) =>
  (!filters.status || item.status === filters.status) &&
  (!filters.region || item.region === filters.region) &&
  (!filters.product || item.product === filters.product) &&
  (!filters.owner || item.owner === filters.owner) &&
  (!filters.keyword || `${item.name} ${item.ip} ${item.region} ${item.product}`.toLowerCase().includes(filters.keyword.toLowerCase())),
))
const affectedCustomerRows = computed(() => {
  const rows = []
  for (const line of dataset.value?.lines || []) {
    for (const customer of line.customers) rows.push({ customer, line: line.name, lineId: line.id, status: line.status, expiresAt: line.expiresAt })
  }
  return rows
})

async function logout() { await endSession(); router.replace('/login') }
function formatTime(value) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—' }
function duration(value) {
  const minutes = Math.max(1, Math.floor((new Date('2026-10-23T09:45:00+08:00') - new Date(value)) / 60000))
  return minutes < 60 ? `${minutes} 分钟` : `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟`
}
function percentage(item) { return item.total ? Math.round((item.normal / item.total) * 100) : 0 }
function prototypeAction(label) {
  notice.value = `${label}仅为交互演示，未修改任何后端状态`
  window.clearTimeout(prototypeAction.timer)
  prototypeAction.timer = window.setTimeout(() => { notice.value = '' }, 3200)
}
function resetFilters() { Object.assign(filters, { status: '', region: '', product: '', owner: '', keyword: '' }) }

async function load() {
  loading.value = true
  error.value = ''
  selectedLine.value = null
  try {
    dataset.value = await loadHealthFixture({ role: isSales.value ? 'sales' : 'admin', username: storedUser.value.username, state: route.query.demo || '' })
  } catch (loadError) { error.value = loadError.message || '演示数据加载失败' } finally { loading.value = false }
}
watch(() => route.fullPath, load)
onMounted(load)
</script>

<template>
  <div class="workspace-shell health-shell">
    <aside class="workspace-sidebar" :class="{ open: mobileNav }">
      <div class="workspace-brand"><BrandLogo eager /><button aria-label="关闭导航" @click="mobileNav = false"><X :size="20" /></button></div>
      <p class="workspace-role">{{ isSales ? '销售工作台' : '管理后台' }}</p>
      <nav><button v-for="[label, path, icon] in nav" :key="path" :class="{ active: route.path === path }" @click="router.push(path); mobileNav = false"><component :is="icon" :size="17" />{{ label }}</button></nav>
      <div class="workspace-user"><span>{{ storedUser.display_name || storedUser.username }}</span><small>{{ storedUser.role }}</small><button @click="logout"><LogOut :size="15" />退出登录</button></div>
    </aside>
    <div class="workspace-main">
      <header class="workspace-header health-header"><button class="workspace-menu" aria-label="打开导航" @click="mobileNav = true"><Menu :size="21" /></button><div><h1>{{ isSales ? '我的客户线路影响' : '线路健康工作台' }}</h1><p>{{ isSales ? '只展示当前销售负责客户的演示影响数据' : '线路状态、故障影响与处理进度统一视图' }}</p></div><span class="demo-badge"><CircleAlert :size="14" />演示数据</span></header>
      <main class="workspace-content health-content">
        <div v-if="notice" class="workspace-toast">{{ notice }}</div>
        <section v-if="loading" class="health-loading" aria-label="正在加载演示数据"><span v-for="item in 8" :key="item"></span></section>
        <section v-else-if="error" class="workspace-state error-state"><AlertTriangle :size="30" /><h2>健康数据暂时无法显示</h2><p>{{ error }}</p><button class="outline-action" @click="load"><RefreshCw :size="15" />重新加载</button></section>
        <template v-else-if="dataset">
          <div class="health-demo-note"><span><CircleAlert :size="15" />本页为健康工作台交互原型，数据来自独立 fixture，不代表真实线路状态。</span><small>演示生成时间：{{ formatTime(dataset.generatedAt) }}</small></div>

          <template v-if="!isSales">
            <section class="health-summary" aria-label="线路健康概览">
              <div><span>线路总数</span><strong>{{ dataset.stats.total }}</strong></div><div><span>正常</span><strong>{{ dataset.stats.normal }}</strong></div><div><span>异常</span><strong>{{ dataset.stats.abnormal }}</strong></div><div><span>离线</span><strong>{{ dataset.stats.offline }}</strong></div><div><span>平均延迟</span><strong>{{ dataset.stats.averageLatency ?? '—' }}<small v-if="dataset.stats.averageLatency">ms</small></strong></div><div><span>受影响客户</span><strong>{{ dataset.stats.affectedCustomers }}</strong></div><div><span>未处理故障</span><strong>{{ dataset.stats.unresolved }}</strong></div>
            </section>

            <section class="health-section">
              <div class="health-section-head"><div><h2>故障事件</h2><p>按影响等级与持续时间跟进当前事件</p></div><span>{{ dataset.incidents.length }} 个进行中</span></div>
              <div v-if="!dataset.incidents.length" class="health-empty"><ShieldCheck :size="28" /><b>当前没有故障事件</b><span>演示数据中没有待处理事项</span></div>
              <div v-else class="health-event-list"><button v-for="incident in dataset.incidents" :key="incident.id" @click="selectedLine = dataset.lines.find((line) => line.id === incident.lineId)"><i :data-tone="statusMeta[incident.severity].tone"></i><span><b>{{ incident.line }}</b><small>{{ incident.region }} · {{ incident.product }}</small></span><span><b>{{ duration(incident.startedAt) }}</b><small>持续时间</small></span><span><b>{{ incident.affectedCustomers }} 位</b><small>影响客户</small></span><span><b>{{ incident.owner }}</b><small>负责人</small></span><em>{{ incident.status }}</em><ChevronRight :size="17" /></button></div>
            </section>

            <div class="health-breakdown">
              <section class="health-section"><div class="health-section-head"><div><h2>地区健康统计</h2><p>按地区查看正常线路占比</p></div></div><div class="health-bars"><div v-for="item in dataset.regions" :key="item.name"><span><b>{{ item.name }}</b><small>{{ item.normal }}/{{ item.total }} 正常</small></span><i><b :style="{ width: percentage(item) + '%' }"></b></i><em>{{ percentage(item) }}%</em></div></div></section>
              <section class="health-section"><div class="health-section-head"><div><h2>产品类型健康统计</h2><p>覆盖四类线路产品</p></div></div><div class="health-bars"><div v-for="item in dataset.products" :key="item.name"><span><b>{{ item.name }}</b><small>{{ item.issues }} 条需关注</small></span><i><b :style="{ width: percentage(item) + '%' }"></b></i><em>{{ percentage(item) }}%</em></div></div></section>
            </div>

            <section class="health-section health-lines-section">
              <div class="health-section-head"><div><h2>线路列表</h2><p>异常线路已按离线、异常、延迟升高、待检测、正常置顶排序</p></div><button class="outline-action" @click="prototypeAction('发起全量检测')"><RefreshCw :size="14" />发起检测</button></div>
              <div class="health-filters"><label><Search :size="15" /><input v-model="filters.keyword" placeholder="搜索线路、IP、地区或产品" /></label><select v-model="filters.status"><option value="">全部状态</option><option v-for="(meta, key) in statusMeta" :key="key" :value="key">{{ meta.label }}</option></select><select v-model="filters.region"><option value="">全部地区</option><option v-for="value in regions" :key="value">{{ value }}</option></select><select v-model="filters.product"><option value="">全部产品</option><option v-for="value in products" :key="value">{{ value }}</option></select><select v-model="filters.owner"><option value="">全部负责人</option><option v-for="value in owners" :key="value">{{ value }}</option></select></div>
              <div v-if="!dataset.lines.length" class="health-empty"><Server :size="28" /><b>暂无线路数据</b><span>接入健康 API 后将在此展示线路</span></div>
              <div v-else-if="!filteredLines.length" class="health-empty"><SlidersHorizontal :size="28" /><b>没有符合筛选条件的线路</b><button class="text-action" @click="resetFilters">清除筛选</button></div>
              <div v-else class="health-table"><div class="health-table-head"><span>线路 / IP</span><span>地区 / 产品</span><span>状态</span><span>延迟</span><span>最近检测</span><span>影响客户</span><span>负责人</span><span></span></div><button v-for="line in filteredLines" :key="line.id" class="health-table-row" @click="selectedLine = line"><span data-label="线路 / IP"><b>{{ line.name }}</b><small>{{ line.ip }}</small></span><span data-label="地区 / 产品"><b>{{ line.region }}</b><small>{{ line.product }}</small></span><span data-label="状态"><em class="health-status" :data-tone="statusMeta[line.status].tone"><i></i>{{ statusMeta[line.status].label }}</em></span><span data-label="延迟"><b>{{ line.latency ?? '—' }}<small v-if="line.latency"> ms</small></b></span><span data-label="最近检测">{{ formatTime(line.lastChecked) }}</span><span data-label="影响客户">{{ line.affectedCustomers }} 位</span><span data-label="负责人">{{ line.owner }}</span><ChevronRight :size="17" /></button></div>
            </section>
          </template>

          <template v-else>
            <section class="sales-health-summary"><div><span>受影响线路</span><strong>{{ dataset.lines.filter((item) => item.status !== 'normal').length }}</strong><small>仅我的客户线路</small></div><div><span>受影响客户</span><strong>{{ dataset.stats.affectedCustomers }}</strong><small>当前销售负责范围</small></div><div><span>故障事件</span><strong>{{ dataset.stats.unresolved }}</strong><small>不含其他销售数据</small></div><div><span>最近到期</span><strong>{{ affectedCustomerRows.length ? Math.min(...affectedCustomerRows.map((item) => Math.ceil((new Date(item.expiresAt) - new Date('2026-10-23T09:45:00+08:00')) / 86400000))) : '—' }}<small>天</small></strong></div></section>
            <section class="health-section"><div class="health-section-head"><div><h2>我的客户故障事件</h2><p>仅展示 {{ storedUser.display_name || storedUser.username }} 负责范围内的演示事件</p></div><span>{{ dataset.incidents.length }} 个进行中</span></div><div v-if="!dataset.incidents.length" class="health-empty"><ShieldCheck :size="28" /><b>当前没有客户故障事件</b><span>这里不会展示其他销售的线路信息</span></div><div v-else class="health-event-list sales-events"><button v-for="incident in dataset.incidents" :key="incident.id" @click="selectedLine = dataset.lines.find((line) => line.id === incident.lineId)"><i :data-tone="statusMeta[incident.severity].tone"></i><span><b>{{ incident.line }}</b><small>{{ incident.region }} · {{ incident.product }}</small></span><span><b>{{ incident.affectedCustomers }} 位</b><small>影响客户</small></span><span><b>{{ duration(incident.startedAt) }}</b><small>持续时间</small></span><em>{{ incident.status }}</em><ChevronRight :size="17" /></button></div></section>
            <section class="health-section"><div class="health-section-head"><div><h2>受影响客户与线路</h2><p>联系入口均为前端原型，不会发送消息</p></div></div><div v-if="!affectedCustomerRows.length" class="health-empty"><UserRound :size="28" /><b>暂无受影响客户</b><span>当前销售范围内没有受影响线路</span></div><div v-else class="sales-impact-list"><article v-for="item in affectedCustomerRows" :key="item.customer + item.lineId"><div><span class="health-status" :data-tone="statusMeta[item.status].tone"><i></i>{{ statusMeta[item.status].label }}</span><h3>{{ item.customer }}</h3><p>{{ item.line }} · 到期 {{ formatTime(item.expiresAt) }}</p></div><div><button title="联系客户" @click="prototypeAction(`联系 ${item.customer}`)"><Mail :size="15" />联系客户</button><button @click="prototypeAction(`查看 ${item.customer} 客户档案`)">查看客户</button><button @click="prototypeAction(`查看 ${item.customer} 相关订单`)">查看订单</button></div></article></div></section>
          </template>
        </template>
      </main>
    </div>

    <div v-if="mobileNav" class="workspace-overlay" @click="mobileNav = false"></div>
    <div v-if="selectedLine" class="health-drawer-backdrop" @click="selectedLine = null"></div>
    <aside v-if="selectedLine" class="health-drawer" aria-label="线路健康详情">
      <header><div><span class="health-status" :data-tone="statusMeta[selectedLine.status].tone"><i></i>{{ statusMeta[selectedLine.status].label }}</span><h2>{{ selectedLine.name }}</h2><p>{{ selectedLine.region }} · {{ selectedLine.product }} · {{ selectedLine.ip }}</p></div><button aria-label="关闭详情" @click="selectedLine = null"><X :size="20" /></button></header>
      <div class="health-drawer-body"><dl><div><dt>当前延迟</dt><dd>{{ selectedLine.latency ?? '—' }}<small v-if="selectedLine.latency"> ms</small></dd></div><div><dt>最近检测</dt><dd>{{ formatTime(selectedLine.lastChecked) }}</dd></div><div><dt>影响客户</dt><dd>{{ selectedLine.affectedCustomers }} 位</dd></div><div><dt>负责人</dt><dd>{{ selectedLine.owner }}</dd></div></dl><section><h3>检测时间线</h3><div class="health-timeline"><div v-for="item in selectedLine.timeline" :key="item.time + item.label"><i :data-tone="statusMeta[item.state].tone"></i><span><b>{{ item.time }}</b><p>{{ item.label }}</p></span></div></div></section><section><h3>受影响客户</h3><div v-if="!selectedLine.customers.length" class="drawer-empty">当前没有受影响客户</div><div v-else class="drawer-customers"><span v-for="customer in selectedLine.customers" :key="customer"><UserRound :size="15" />{{ customer }}</span></div></section><section><h3>处理记录</h3><div v-if="!selectedLine.records.length" class="drawer-empty">暂无处理记录</div><div v-for="record in selectedLine.records" :key="record.time" class="drawer-record"><Clock3 :size="15" /><span><b>{{ record.time }} · {{ record.owner }}</b><p>{{ record.action }}</p></span></div></section></div>
      <footer v-if="!isSales"><button @click="prototypeAction('确认故障')"><BellRing :size="15" />确认故障</button><button @click="prototypeAction('进入维护')">进入维护</button><button @click="prototypeAction('静默 2 小时')">静默 2 小时</button><small>以上操作均为演示，不会写入后端</small></footer>
    </aside>
  </div>
</template>
