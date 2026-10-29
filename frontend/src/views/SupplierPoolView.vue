<script setup>
import BrandLogo from '../components/BrandLogo.vue'
import { computed, onMounted, reactive, ref } from 'vue'
import {
  Activity, AlertTriangle, Box, Check, CircleDollarSign, Database, History, KeyRound,
  LogOut, Menu, MessageSquare, PackageSearch, RefreshCw, Save, ShieldOff, X,
} from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import { session } from '../api/session'
import { logout as endSession } from '../api'
import {
  getAdminCatalogProducts, getAdminCatalogRegions, getCatalogPublications, getSupplierInventory, getSupplierOverview,
  getSupplierProducts, getSupplierQuotes, getSupplierRegions, getSupplierRules, getSupplierSyncRuns,
  mapSupplierProduct, mapSupplierRegion, publishCatalogSnapshot, saveSupplierRule, syncSupplierCities,
  syncSupplierCountries, syncSupplierInventory, syncSupplierProducts, syncSupplierQuotes,
  withdrawCatalogPublication,
} from '../api'

const route = useRoute()
const router = useRouter()
const loading = ref(true)
const error = ref('')
const feedback = ref('')
const activeTab = ref('overview')
const mobileNav = ref(false)
const busy = ref('')
const batchState = ref('idle')
const overview = ref(null)
const supplierProducts = ref([])
const supplierRegions = ref([])
const inventory = ref([])
const quotes = ref([])
const rules = ref([])
const syncRuns = ref([])
const localProducts = ref([])
const localRegions = ref([])
const publications = ref([])
const inventoryCountry = ref('US')
const ruleDraft = reactive({ product_id: '', region_id: '', supplier_product_id: '', supplier_region_id: '', is_enabled: true })
const publishDraft = reactive({ product_id: '', region_id: '', inventory_snapshot_id: '', quote_snapshot_id: '', price: '' })

const nav = [
  ['订单处理', '/admin', Box],
  ['线路健康', '/admin/health', Activity],
  ['全量会话', '/admin/inbox', MessageSquare],
  ['资源池', '/admin/resource-pool', Database],
]
const tabs = [
  ['overview', '状态概览'],
  ['mappings', '商品与地区映射'],
  ['snapshots', '库存与报价'],
  ['publish', '审核发布'],
  ['history', '同步历史'],
]
const stateMeta = {
  not_synced: { label: '未同步', tone: 'muted' },
  syncing: { label: '执行中', tone: 'info' },
  fresh: { label: '已同步', tone: 'healthy' },
  stale: { label: '数据过期', tone: 'warning' },
  out_of_stock: { label: '无库存', tone: 'critical' },
  query_failed: { label: '查询失败', tone: 'critical' },
  running: { label: '执行中', tone: 'info' },
  success: { label: '成功', tone: 'healthy' },
  partial_failed: { label: '部分失败', tone: 'warning' },
  failed: { label: '失败', tone: 'critical' },
}
const inventoryMeta = {
  available: '可用',
  tight: '库存紧缺',
  unavailable: '无库存',
  unknown: '未知',
}
const syncTypeText = {
  products: '供应商商品', countries: '国家', cities: '城市', inventory: '库存摘要',
  inventory_check: '库存检测', quotes: '报价',
}
const storedUser = computed(() => {
  return session.user || {}
})
const mappedProducts = computed(() => supplierProducts.value.filter((item) => item.local_product_id).length)
const mappedRegions = computed(() => supplierRegions.value.filter((item) => item.local_region_id).length)
const currentState = (value) => stateMeta[value] || { label: value || '未知', tone: 'muted' }
const dateTime = (value) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
const money = (value) => value == null ? '—' : `¥${Number(value).toFixed(4)}`
const localProductName = (id) => localProducts.value.find((item) => item.id === id)?.name || '未映射'
const localRegionName = (id) => localRegions.value.find((item) => item.id === id)?.name || '未映射'
const selectedProduct = computed(() => localProducts.value.find((item) => item.id === Number(publishDraft.product_id)))
const eligibleInventory = computed(() => inventory.value.filter((item) => item.supplier_region_id && supplierRegions.value.some((region) => region.id === item.supplier_region_id && region.local_region_id === Number(publishDraft.region_id)) && supplierProducts.value.some((product) => product.id === item.supplier_product_id && product.local_product_id === Number(publishDraft.product_id))))
const eligibleQuotes = computed(() => quotes.value.filter((item) => item.supplier_product_id && supplierProducts.value.some((product) => product.id === item.supplier_product_id && product.local_product_id === Number(publishDraft.product_id)) && (!item.country_code || item.country_code === localRegions.value.find((region) => region.id === Number(publishDraft.region_id))?.country_code) && (!item.duration_days || item.duration_days === selectedProduct.value?.billing_days)))
const publicationWarning = computed(() => Object.values(overview.value?.states || {}).some((state) => ['stale', 'query_failed', 'out_of_stock'].includes(state)))
const latestPublicationIds = computed(() => new Set(publications.value.filter((item) => item.version === Math.max(...publications.value.filter((row) => row.product_id === item.product_id).map((row) => row.version))).map((item) => item.id)))

async function logout() {
  await endSession()
  router.replace('/admin/login')
}
function notify(message) {
  feedback.value = message
  window.clearTimeout(notify.timer)
  notify.timer = window.setTimeout(() => { feedback.value = '' }, 3600)
}
function errorText(requestError, fallback) {
  return requestError?.userMessage || fallback
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const responses = await Promise.all([
      getSupplierOverview(), getSupplierProducts(), getSupplierRegions(), getSupplierInventory(),
      getSupplierQuotes(), getSupplierRules(), getSupplierSyncRuns(), getAdminCatalogProducts(),
      getAdminCatalogRegions(), getCatalogPublications(),
    ])
    ;[
      overview.value, supplierProducts.value, supplierRegions.value, inventory.value, quotes.value,
      rules.value, syncRuns.value, localProducts.value, localRegions.value, publications.value,
    ] = responses.map((response) => response.data)
  } catch (requestError) {
    error.value = errorText(requestError, '资源池数据加载失败，请重试')
  } finally {
    loading.value = false
  }
}

async function runDirectorySync() {
  busy.value = 'directory'
  batchState.value = 'running'
  error.value = ''
  const operations = [syncSupplierProducts, syncSupplierCountries, syncSupplierCities, syncSupplierQuotes]
  const results = []
  for (const operation of operations) {
    try { await operation(); results.push(true) } catch { results.push(false) }
  }
  const successCount = results.filter(Boolean).length
  batchState.value = successCount === results.length ? 'success' : successCount ? 'partial_failed' : 'failed'
  notify(successCount === results.length ? '目录与报价同步完成' : successCount ? `部分同步完成，${results.length - successCount} 项失败，可在同步历史中重试` : '同步失败，请查看同步历史')
  busy.value = ''
  await load()
}

async function runInventorySync() {
  const country = inventoryCountry.value.trim().toUpperCase()
  if (!/^[A-Z]{2}$/.test(country)) {
    error.value = '请输入两位国家代码，例如 US'
    return
  }
  busy.value = 'inventory'
  error.value = ''
  try {
    await syncSupplierInventory(country)
    notify(`${country} 库存同步完成`)
  } catch (requestError) {
    error.value = errorText(requestError, '库存同步失败，请重试')
  } finally {
    busy.value = ''
    await load()
  }
}

async function retryRun(run) {
  const actions = {
    products: syncSupplierProducts,
    countries: syncSupplierCountries,
    cities: syncSupplierCities,
    quotes: syncSupplierQuotes,
    inventory: () => syncSupplierInventory(inventoryCountry.value.trim().toUpperCase()),
  }
  const action = actions[run.sync_type]
  if (!action) {
    error.value = '该同步类型需要填写业务参数后重新执行'
    return
  }
  busy.value = `retry-${run.id}`
  error.value = ''
  try {
    await action()
    notify(`${syncTypeText[run.sync_type] || run.sync_type}重试成功`)
  } catch (requestError) {
    error.value = errorText(requestError, '重试失败，请检查配置')
  } finally {
    busy.value = ''
    await load()
  }
}

async function updateProductMapping(item, value) {
  busy.value = `product-${item.id}`
  error.value = ''
  try {
    await mapSupplierProduct(item.id, value ? Number(value) : null)
    notify('商品映射已保存并写入审计')
  } catch (requestError) {
    error.value = errorText(requestError, '商品映射保存失败')
  } finally {
    busy.value = ''
    await load()
  }
}

async function updateRegionMapping(item, value) {
  busy.value = `region-${item.id}`
  error.value = ''
  try {
    await mapSupplierRegion(item.id, value ? Number(value) : null)
    notify('地区映射已保存并写入审计')
  } catch (requestError) {
    error.value = errorText(requestError, '地区映射保存失败')
  } finally {
    busy.value = ''
    await load()
  }
}

async function submitRule() {
  const payload = Object.fromEntries(Object.entries(ruleDraft).map(([key, value]) => [key, key === 'is_enabled' ? value : Number(value)]))
  if (Object.entries(payload).some(([key, value]) => key !== 'is_enabled' && !value)) {
    error.value = '请选择完整的商品、地区和供应商映射'
    return
  }
  busy.value = 'rule'
  error.value = ''
  try {
    await saveSupplierRule(payload)
    notify('默认来源规则已保存，采购仍保持禁用')
  } catch (requestError) {
    error.value = errorText(requestError, '来源规则保存失败')
  } finally {
    busy.value = ''
    await load()
  }
}

async function submitPublication() {
  const payload = {
    product_id: Number(publishDraft.product_id), region_id: Number(publishDraft.region_id),
    inventory_snapshot_id: Number(publishDraft.inventory_snapshot_id),
    quote_snapshot_id: Number(publishDraft.quote_snapshot_id), price: Number(publishDraft.price),
  }
  if (Object.values(payload).some((value) => !Number.isFinite(value) || value <= 0)) {
    error.value = '请选择商品、地区及完整快照，并填写有效官网售价'
    return
  }
  busy.value = 'publish'
  error.value = ''
  try {
    await publishCatalogSnapshot(payload)
    notify('已发布到内部目录并写入审计')
    await load()
  } catch (requestError) {
    error.value = errorText(requestError, '发布失败')
  } finally { busy.value = '' }
}

async function withdrawPublication(item) {
  busy.value = `withdraw-${item.id}`
  error.value = ''
  try {
    await withdrawCatalogPublication(item.id)
    notify('已撤回到上一版本并写入审计')
    await load()
  } catch (requestError) {
    error.value = errorText(requestError, '撤回失败')
  } finally { busy.value = '' }
}

onMounted(load)
</script>

<template>
  <div class="workspace-shell supplier-shell">
    <aside class="workspace-sidebar" :class="{ open: mobileNav }">
      <div class="workspace-brand"><BrandLogo eager /><button aria-label="关闭导航" @click="mobileNav = false"><X :size="20" /></button></div>
      <p class="workspace-role">管理后台</p>
      <nav><button v-for="[label, path, icon] in nav" :key="path" :class="{ active: route.path === path }" @click="router.push(path); mobileNav = false"><component :is="icon" :size="17" />{{ label }}</button></nav>
      <div class="workspace-user"><span>{{ storedUser.display_name || storedUser.username }}</span><small>{{ storedUser.role }}</small><button @click="logout"><LogOut :size="15" />退出登录</button></div>
    </aside>

    <div class="workspace-main">
      <header class="workspace-header supplier-header">
        <button class="workspace-menu" aria-label="打开导航" @click="mobileNav = true"><Menu :size="21" /></button>
        <div><h1>资源池管理</h1><p>供应商只读目录、成本快照与内部来源映射</p></div>
        <span class="procurement-lock"><ShieldOff :size="14" />采购已禁用</span>
      </header>

      <main class="workspace-content supplier-content">
        <div v-if="feedback" class="workspace-toast">{{ feedback }}</div>
        <section v-if="loading" class="supplier-loading" aria-label="正在加载资源池"><span v-for="item in 7" :key="item"></span></section>
        <section v-else-if="error && !overview" class="workspace-state error-state"><AlertTriangle :size="30" /><h2>资源池数据暂时无法显示</h2><p>{{ error }}</p><button class="outline-action" @click="load"><RefreshCw :size="15" />重新加载</button></section>
        <template v-else-if="overview">
          <div v-if="error" class="workspace-alert"><span>{{ error }}</span><button @click="error = ''">关闭</button></div>
          <div v-if="!overview.supplier.api_key_configured" class="supplier-config-warning">
            <KeyRound :size="18" /><span><b>供应商 API Key 未配置</b><small>请由运维通过环境变量 GLOBAL_PROXY_API_KEY 注入。页面不会生成演示数据，同步操作将安全失败。</small></span>
          </div>
          <div v-if="publicationWarning" class="supplier-config-warning"><AlertTriangle :size="18" /><span><b>供应商快照过期或同步异常</b><small>已发布商品保持现状；禁止新增发布。下架须由管理员确认。</small></span></div>

          <section class="supplier-summary" aria-label="资源池概览">
            <div><span>资源池</span><strong>{{ overview.pool.name }}</strong><small>{{ overview.pool.strategy }}</small></div>
            <div><span>供应商状态</span><strong>{{ overview.supplier.status === 'active' ? '已启用' : '已停用' }}</strong><small>{{ overview.supplier.name }}</small></div>
            <div><span>商品映射</span><strong>{{ mappedProducts }} / {{ overview.counts.products }}</strong><small>已同步商品</small></div>
            <div><span>地区映射</span><strong>{{ mappedRegions }} / {{ overview.counts.regions }}</strong><small>国家与城市</small></div>
            <div><span>人工来源规则</span><strong>{{ overview.counts.enabled_manual_rules }}</strong><small>不会触发采购</small></div>
          </section>

          <nav class="supplier-tabs" aria-label="资源池视图">
            <button v-for="[id, label] in tabs" :key="id" :class="{ active: activeTab === id }" @click="activeTab = id">{{ label }}</button>
          </nav>

          <template v-if="activeTab === 'overview'">
            <section class="supplier-section sync-control">
              <div class="section-heading"><div><h2>手动同步</h2><p>所有查询由管理员明确触发，不存在后台自动采购或自动同步。</p></div><span class="status-chip" :data-tone="currentState(batchState).tone">{{ batchState === 'idle' ? '等待操作' : currentState(batchState).label }}</span></div>
              <div class="sync-actions">
                <button class="primary-action" :disabled="busy || !overview.supplier.api_key_configured" @click="runDirectorySync"><RefreshCw :size="15" :class="{ spinning: busy === 'directory' }" />{{ busy === 'directory' ? '同步执行中' : '同步目录与报价' }}</button>
                <label><span>库存国家</span><input v-model="inventoryCountry" maxlength="2" placeholder="US" /></label>
                <button class="secondary-action" :disabled="busy || !overview.supplier.api_key_configured" @click="runInventorySync"><PackageSearch :size="15" />{{ busy === 'inventory' ? '查询中' : '同步库存' }}</button>
              </div>
            </section>

            <section class="supplier-section">
              <div class="section-heading"><div><h2>数据可用性</h2><p>过期、失败和未同步数据不会标记为实时可售。</p></div><small>最近完成：{{ dateTime(overview.supplier.last_sync_at) }}</small></div>
              <div class="state-grid">
                <div v-for="(value, key) in overview.states" :key="key"><span>{{ { products: '商品', regions: '地区', inventory: '库存', quotes: '报价' }[key] }}</span><em class="status-chip" :data-tone="currentState(value).tone">{{ currentState(value).label }}</em></div>
              </div>
              <div v-if="overview.supplier.last_error_code" class="last-error"><AlertTriangle :size="16" /><span><b>{{ overview.supplier.last_error_code }}</b>{{ overview.supplier.last_error_message }}</span></div>
            </section>
          </template>

          <template v-else-if="activeTab === 'mappings'">
            <section class="supplier-section">
              <div class="section-heading"><div><h2>商品映射</h2><p>供应商商品只作为内部来源，不会创建或改价官网商品。</p></div><span>{{ mappedProducts }} 项已映射</span></div>
              <div v-if="!supplierProducts.length" class="supplier-empty">尚未同步供应商商品</div>
              <div v-else class="data-table product-mapping-table">
                <div class="data-head"><span>供应商商品</span><span>状态</span><span>玖玖IP商品</span><span>同步时间</span></div>
                <div v-for="item in supplierProducts" :key="item.id" class="data-row"><span><b>{{ item.name_snapshot }}</b><small>产品 {{ item.external_product_ref }} · {{ item.currency }}</small></span><span><em class="status-chip" :data-tone="currentState(item.data_state).tone">{{ currentState(item.data_state).label }}</em></span><span><select :value="item.local_product_id || ''" :disabled="busy === `product-${item.id}`" @change="updateProductMapping(item, $event.target.value)"><option value="">未映射</option><option v-for="product in localProducts" :key="product.id" :value="product.id">{{ product.name }} · 地区 #{{ product.region_id }}</option></select></span><span>{{ dateTime(item.last_synced_at) }}</span></div>
              </div>
            </section>

            <section class="supplier-section">
              <div class="section-heading"><div><h2>地区映射</h2><p>只允许显式选择，不自动按名称模糊匹配。</p></div><span>{{ mappedRegions }} 项已映射</span></div>
              <div v-if="!supplierRegions.length" class="supplier-empty">尚未同步供应商地区</div>
              <div v-else class="data-table region-mapping-table">
                <div class="data-head"><span>供应商地区</span><span>层级</span><span>玖玖IP地区</span><span>状态</span></div>
                <div v-for="item in supplierRegions" :key="item.id" class="data-row"><span><b>{{ item.city_name || item.country_name }}</b><small>{{ item.country_code }} · {{ item.country_name }}</small></span><span>{{ item.city_name ? '城市' : '国家' }}</span><span><select :value="item.local_region_id || ''" :disabled="busy === `region-${item.id}`" @change="updateRegionMapping(item, $event.target.value)"><option value="">未映射</option><option v-for="region in localRegions" :key="region.id" :value="region.id">{{ region.name }} · {{ region.country_code }}</option></select></span><span><em class="status-chip" :data-tone="currentState(item.data_state).tone">{{ currentState(item.data_state).label }}</em></span></div>
              </div>
            </section>

            <section class="supplier-section rule-editor">
              <div class="section-heading"><div><h2>默认来源规则</h2><p>规则仅用于内部人工选源，保存后采购开关仍保持关闭。</p></div></div>
              <div class="rule-fields">
                <label>玖玖IP商品<select v-model="ruleDraft.product_id"><option value="">请选择</option><option v-for="item in localProducts" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
                <label>玖玖IP地区<select v-model="ruleDraft.region_id"><option value="">请选择</option><option v-for="item in localRegions" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
                <label>供应商商品<select v-model="ruleDraft.supplier_product_id"><option value="">请选择</option><option v-for="item in supplierProducts.filter((row) => row.local_product_id)" :key="item.id" :value="item.id">{{ item.name_snapshot }}</option></select></label>
                <label>供应商地区<select v-model="ruleDraft.supplier_region_id"><option value="">请选择</option><option v-for="item in supplierRegions.filter((row) => row.local_region_id)" :key="item.id" :value="item.id">{{ item.city_name || item.country_name }}</option></select></label>
                <label class="rule-toggle"><input v-model="ruleDraft.is_enabled" type="checkbox" />启用人工来源规则</label>
                <button class="primary-action" :disabled="busy === 'rule'" @click="submitRule"><Save :size="15" />保存规则</button>
              </div>
              <div v-if="rules.length" class="rule-list"><div v-for="rule in rules" :key="rule.id"><span><b>{{ localProductName(rule.product_id) }}</b><small>{{ localRegionName(rule.region_id) }}</small></span><span>资源池 1</span><em>{{ rule.mode === 'manual' ? '人工' : rule.mode }}</em><span class="status-chip" :data-tone="rule.is_enabled ? 'healthy' : 'muted'">{{ rule.is_enabled ? '已启用' : '已停用' }}</span></div></div>
            </section>
          </template>

          <template v-else-if="activeTab === 'snapshots'">
            <section class="supplier-section">
              <div class="section-heading"><div><h2>库存快照</h2><p>“实时可售”仅表示快照新鲜且供应商返回可用，不构成库存承诺。</p></div><span>{{ inventory.length }} 条</span></div>
              <div v-if="!inventory.length" class="supplier-empty">未同步库存，不展示推测数据</div>
              <div v-else class="data-table inventory-table"><div class="data-head"><span>地区</span><span>供应商状态</span><span>数据状态</span><span>实时可售</span><span>查询时间</span></div><div v-for="item in inventory" :key="item.id" class="data-row"><span><b>{{ item.city_name || item.country_code }}</b><small>{{ item.country_code }}</small></span><span>{{ inventoryMeta[item.availability] || item.availability }}</span><span><em class="status-chip" :data-tone="currentState(item.data_state).tone">{{ currentState(item.data_state).label }}</em></span><span><Check v-if="item.is_realtime_available" :size="15" /><ShieldOff v-else :size="15" />{{ item.is_realtime_available ? '可用于人工判断' : '不可用' }}</span><span>{{ dateTime(item.checked_at) }}</span></div></div>
            </section>

            <section class="supplier-section">
              <div class="section-heading"><div><h2>供应商成本快照</h2><p>仅管理员可见。官网售价继续由玖玖IP商品目录控制。</p></div><span>{{ quotes.length }} 条</span></div>
              <div v-if="!quotes.length" class="supplier-empty">未同步报价，不展示演示成本</div>
              <div v-else class="data-table quote-table"><div class="data-head"><span>供应商产品</span><span>国家/周期</span><span>单位成本</span><span>总成本</span><span>数据状态</span><span>报价时间</span></div><div v-for="item in quotes" :key="item.id" class="data-row"><span>产品 {{ item.external_product_ref }}</span><span>{{ item.country_code || '通用' }} · {{ item.duration_days ? `${item.duration_days} 天` : '基础档' }}</span><span class="cost-value">{{ money(item.unit_cost) }} {{ item.currency }}</span><span class="cost-value">{{ money(item.total_cost) }}</span><span><em class="status-chip" :data-tone="currentState(item.data_state).tone">{{ currentState(item.data_state).label }}</em></span><span>{{ dateTime(item.quoted_at) }}</span></div></div>
            </section>
          </template>

          <template v-else-if="activeTab === 'publish'">
            <section class="supplier-section">
              <div class="section-heading"><div><h2>人工审核发布</h2><p>选择已映射的内部商品和地区、库存与报价快照，并确定官网售价。</p></div></div>
              <div class="publish-fields">
                <label>玖玖IP商品<select v-model="publishDraft.product_id" @change="publishDraft.region_id = ''; publishDraft.inventory_snapshot_id = ''; publishDraft.quote_snapshot_id = ''"><option value="">请选择</option><option v-for="item in localProducts" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
                <label>玖玖IP地区<select v-model="publishDraft.region_id" @change="publishDraft.inventory_snapshot_id = ''; publishDraft.quote_snapshot_id = ''"><option value="">请选择</option><option v-for="item in localRegions.filter((region) => region.id === selectedProduct?.region_id)" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
                <label>库存快照<select v-model="publishDraft.inventory_snapshot_id"><option value="">请选择</option><option v-for="item in eligibleInventory" :key="item.id" :value="item.id">#{{ item.id }} · {{ inventoryMeta[item.availability] || item.availability }} · {{ currentState(item.data_state).label }} · {{ dateTime(item.checked_at) }}</option></select></label>
                <label>报价快照<select v-model="publishDraft.quote_snapshot_id"><option value="">请选择</option><option v-for="item in eligibleQuotes" :key="item.id" :value="item.id">#{{ item.id }} · {{ money(item.unit_cost) }} {{ item.currency }} · {{ currentState(item.data_state).label }}</option></select></label>
                <label>官网售价<input v-model="publishDraft.price" type="number" min="0.01" step="0.01" inputmode="decimal" /></label>
                <button class="primary-action" :disabled="busy || !publishDraft.product_id || !publishDraft.region_id || !publishDraft.inventory_snapshot_id || !publishDraft.quote_snapshot_id || !publishDraft.price || publicationWarning" @click="submitPublication"><Check :size="15" />发布</button>
              </div>
            </section>
            <section class="supplier-section">
              <div class="section-heading"><div><h2>发布版本</h2><p>撤回当前版本将恢复该次发布前的售价与库存状态。</p></div><span>{{ publications.length }} 条</span></div>
              <div v-if="!publications.length" class="supplier-empty">尚无人工发布记录</div>
              <div v-else class="publication-list"><div v-for="item in publications" :key="item.id"><span><b>{{ localProductName(item.product_id) }} · v{{ item.version }}</b><small>{{ localRegionName(item.region_id) }} · 库存 #{{ item.inventory_snapshot_id }} · 报价 #{{ item.quote_snapshot_id }} · 操作者 #{{ item.actor_id }}</small></span><span>¥{{ item.before_values.price }} → ¥{{ item.after_values.price }}<small>库存 {{ item.before_values.is_in_stock ? '有' : '无' }} → {{ item.after_values.is_in_stock ? '有' : '无' }}</small></span><em class="status-chip" :data-tone="item.withdrawn_at || !latestPublicationIds.has(item.id) ? 'muted' : 'healthy'">{{ item.withdrawn_at ? '已撤回' : latestPublicationIds.has(item.id) ? '已发布' : '历史版本' }}</em><button v-if="latestPublicationIds.has(item.id) && !item.withdrawn_at" class="secondary-action" :disabled="!!busy" @click="withdrawPublication(item)"><X :size="14" />撤回</button></div></div>
            </section>
          </template>

          <template v-else>
            <section class="supplier-section">
              <div class="section-heading"><div><h2>同步历史</h2><p>错误信息经过标准化，不保存请求头、密钥或供应商原始响应。</p></div><History :size="18" /></div>
              <div v-if="!syncRuns.length" class="supplier-empty">尚无同步记录</div>
              <div v-else class="data-table history-table"><div class="data-head"><span>类型</span><span>状态</span><span>结果</span><span>错误</span><span>开始时间</span><span>操作</span></div><div v-for="run in syncRuns" :key="run.id" class="data-row"><span>{{ syncTypeText[run.sync_type] || run.sync_type }}</span><span><em class="status-chip" :data-tone="currentState(run.status).tone">{{ currentState(run.status).label }}</em></span><span>{{ run.items_changed }} / {{ run.items_seen }}</span><span><b v-if="run.error_code">{{ run.error_code }}</b><small>{{ run.error_message || '—' }}</small></span><span>{{ dateTime(run.started_at) }}</span><span><button v-if="['failed', 'partial_failed'].includes(run.status)" class="retry-action" :disabled="busy === `retry-${run.id}` || !overview.supplier.api_key_configured" @click="retryRun(run)"><RefreshCw :size="14" />重试</button><small v-else>—</small></span></div></div>
            </section>
          </template>
        </template>
      </main>
    </div>
  </div>
</template>

<style scoped>
.supplier-header{justify-content:flex-start}.supplier-header>div{display:grid}.supplier-header h1{font-size:19px}.supplier-header p{font-size:12px;color:#71837e;margin:4px 0 0}.procurement-lock{margin-left:auto;display:inline-flex;align-items:center;gap:6px;padding:6px 9px;border:1px solid #e5c9a5;border-radius:5px;background:#fff7e9;color:#82591e;font-size:11px;font-weight:700}.supplier-content{max-width:1600px}.supplier-loading{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}.supplier-loading span{height:102px;border-radius:6px;background:#dfe8e5;animation:supplier-pulse 1.1s ease-in-out infinite alternate}.supplier-loading span:nth-child(n+6){height:300px;grid-column:span 3}.supplier-loading span:last-child{grid-column:span 2}@keyframes supplier-pulse{to{background:#eef3f1}}.supplier-config-warning{display:flex;align-items:flex-start;gap:11px;padding:13px 14px;margin-bottom:14px;border:1px solid #e5c99e;border-radius:6px;background:#fff8eb;color:#79541f}.supplier-config-warning span{display:grid}.supplier-config-warning b{font-size:12px}.supplier-config-warning small{font-size:11px;color:#8d6b39;margin-top:2px}.supplier-summary{display:grid;grid-template-columns:repeat(5,1fr);border:1px solid #d8e2df;border-radius:6px;background:#f9fbfa;margin-bottom:14px}.supplier-summary>div{min-width:0;padding:15px 16px;border-right:1px solid #e0e8e5;display:grid;gap:4px}.supplier-summary>div:last-child{border:0}.supplier-summary span{font-size:10px;color:#738580}.supplier-summary strong{font-size:20px;line-height:1.2;letter-spacing:0;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}.supplier-summary small{font-size:10px;color:#899791;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.supplier-tabs{display:flex;gap:2px;border-bottom:1px solid #ccd9d5;margin-bottom:14px}.supplier-tabs button{padding:10px 13px;border-bottom:2px solid transparent;color:#667a75;font-size:12px}.supplier-tabs button:hover{color:#245f56}.supplier-tabs button.active{border-color:#2f8c7d;color:#185f54;font-weight:750}.supplier-section{padding:17px 18px;margin-bottom:14px;border:1px solid #d8e2df;border-radius:6px;background:#f9fbfa}.section-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;padding-bottom:13px;border-bottom:1px solid #e0e8e5}.section-heading h2{font-size:15px;line-height:1.3;margin:0;letter-spacing:0}.section-heading p{font-size:11px;color:#768883;margin:4px 0 0}.section-heading>span,.section-heading>small{font-size:10px;color:#768883;white-space:nowrap}.sync-actions{display:flex;align-items:end;gap:8px;padding-top:14px}.sync-actions label{display:grid;gap:4px}.sync-actions label span{font-size:10px;color:#748681}.sync-actions input{width:90px;height:36px;border:1px solid #c8d6d2;border-radius:5px;background:#fff;padding:0 9px;text-transform:uppercase}.primary-action,.secondary-action,.retry-action{min-height:36px;display:inline-flex;align-items:center;justify-content:center;gap:7px;padding:7px 11px;border-radius:5px;font-size:11px;font-weight:700}.primary-action{background:#227f70;color:#fff}.primary-action:hover{background:#176d60}.secondary-action,.retry-action{border:1px solid #c4d4cf;background:#fff;color:#315f58}.secondary-action:hover,.retry-action:hover{border-color:#54998c}.primary-action:disabled,.secondary-action:disabled,.retry-action:disabled{opacity:.48;cursor:not-allowed}.spinning{animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}.state-grid{display:grid;grid-template-columns:repeat(4,1fr);padding-top:14px}.state-grid>div{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px 13px;border-right:1px solid #e0e8e5}.state-grid>div:last-child{border:0}.state-grid span{font-size:11px;color:#657973}.status-chip{display:inline-flex;align-items:center;width:max-content;padding:4px 7px;border-radius:4px;background:#e8efed;color:#546a65;font-size:10px;font-style:normal;white-space:nowrap}.status-chip[data-tone="healthy"]{background:#def1ea;color:#226e5b}.status-chip[data-tone="warning"]{background:#fff0d8;color:#895c19}.status-chip[data-tone="critical"]{background:#f8e2de;color:#9a3f32}.status-chip[data-tone="info"]{background:#deedf1;color:#26677a}.last-error{display:flex;gap:8px;align-items:flex-start;margin-top:13px;padding:10px 11px;background:#f8e6e2;color:#873d32;border-radius:5px;font-size:11px}.last-error span{display:grid}.last-error b{font-size:10px}.data-table{border:1px solid #dce5e2;border-radius:6px;overflow:hidden;margin-top:14px}.data-head,.data-row{display:grid;gap:12px;align-items:center;padding:0 12px}.data-head{min-height:38px;background:#edf3f1;color:#71827d;font-size:10px}.data-row{min-height:58px;border-top:1px solid #e2e9e6;font-size:11px;background:#fff}.data-row:hover{background:#f4f8f6}.data-row>span{min-width:0}.data-row b,.data-row small{display:block}.data-row b{font-size:11px}.data-row small{font-size:9px;color:#82918c;overflow-wrap:anywhere}.data-row select,.rule-fields select{width:100%;height:34px;border:1px solid #c8d6d2;border-radius:5px;background:#fff;padding:0 8px;color:#263b37;font-size:11px}.product-mapping-table .data-head,.product-mapping-table .data-row{grid-template-columns:1.25fr .55fr 1.2fr .8fr}.region-mapping-table .data-head,.region-mapping-table .data-row{grid-template-columns:1.2fr .45fr 1.1fr .55fr}.inventory-table .data-head,.inventory-table .data-row{grid-template-columns:1fr .7fr .7fr 1fr .9fr}.inventory-table .data-row>span:nth-child(4){display:flex;align-items:center;gap:6px}.quote-table .data-head,.quote-table .data-row{grid-template-columns:.7fr 1fr .9fr .9fr .65fr 1fr}.history-table .data-head,.history-table .data-row{grid-template-columns:.75fr .6fr .55fr 1.3fr .95fr .55fr}.cost-value{font-variant-numeric:tabular-nums;font-weight:750;color:#215d54}.supplier-empty{min-height:120px;display:grid;place-content:center;color:#7b8d87;font-size:11px}.rule-fields{display:grid;grid-template-columns:repeat(4,minmax(140px,1fr)) auto auto;align-items:end;gap:9px;padding-top:14px}.rule-fields label{display:grid;gap:5px;font-size:10px;color:#71847e}.rule-toggle{height:36px;display:flex!important;align-items:center;gap:7px;padding:0 9px;border:1px solid #d1dcd8;border-radius:5px;background:#fff;white-space:nowrap}.rule-toggle input{accent-color:#267d70}.rule-list{display:grid;margin-top:14px;border-top:1px solid #dce5e2}.rule-list>div{display:grid;grid-template-columns:1.5fr .7fr .5fr .6fr;gap:12px;align-items:center;min-height:54px;border-bottom:1px solid #e0e8e5;font-size:11px}.rule-list span{display:grid}.rule-list small{font-size:9px;color:#82918c}.rule-list em{font-style:normal}.workspace-alert{margin-bottom:14px}.workspace-alert>span{font-size:11px}
@media(max-width:1200px){.supplier-summary{grid-template-columns:repeat(3,1fr)}.supplier-summary>div:nth-child(3){border-right:0}.supplier-summary>div:nth-child(-n+3){border-bottom:1px solid #e0e8e5}.rule-fields{grid-template-columns:repeat(2,1fr)}.quote-table .data-head,.quote-table .data-row{grid-template-columns:.7fr 1fr .8fr .65fr .9fr}.quote-table .data-head span:nth-child(4),.quote-table .data-row span:nth-child(4){display:none}.history-table .data-head,.history-table .data-row{grid-template-columns:.7fr .6fr .5fr 1fr .8fr .5fr}}
@media(max-width:900px){.supplier-summary{grid-template-columns:repeat(2,1fr)}.supplier-summary>div{border-bottom:1px solid #e0e8e5}.supplier-summary>div:nth-child(even){border-right:0}.state-grid{grid-template-columns:1fr 1fr}.state-grid>div{border-bottom:1px solid #e0e8e5}.data-head{display:none}.data-table{border:0;overflow:visible}.data-row,.product-mapping-table .data-row,.region-mapping-table .data-row,.inventory-table .data-row,.quote-table .data-row,.history-table .data-row{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:8px;padding:13px;border:1px solid #dce5e2;border-radius:6px}.data-row>span{display:grid!important}.sync-actions{flex-wrap:wrap}.rule-list>div{grid-template-columns:1fr 1fr}}
@media(max-width:768px){.supplier-header{height:82px}.supplier-header p{font-size:10px}.supplier-content{padding:15px}.procurement-lock{font-size:0}.supplier-summary{grid-template-columns:1fr 1fr}.supplier-tabs{overflow-x:auto}.supplier-tabs button{white-space:nowrap}.supplier-section{padding:14px}.section-heading{align-items:flex-start}.sync-actions{display:grid;grid-template-columns:1fr 90px}.sync-actions .primary-action{grid-column:1/-1}.sync-actions .secondary-action{align-self:end}.rule-fields{grid-template-columns:1fr}.rule-list>div{grid-template-columns:1fr 1fr}.supplier-loading{grid-template-columns:1fr 1fr}.supplier-loading span:nth-child(n+6){grid-column:span 2;height:180px}}
@media(max-width:480px){.supplier-summary{grid-template-columns:1fr}.supplier-summary>div{border-right:0}.state-grid{grid-template-columns:1fr}.state-grid>div{border-right:0}.data-row,.product-mapping-table .data-row,.region-mapping-table .data-row,.inventory-table .data-row,.quote-table .data-row,.history-table .data-row{grid-template-columns:1fr}.sync-actions{grid-template-columns:1fr}.sync-actions label,.sync-actions input{width:100%}.sync-actions .primary-action{grid-column:auto}.rule-list>div{grid-template-columns:1fr}}
.publish-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;padding-top:14px}.publish-fields label{display:grid;gap:5px;min-width:0;font-size:11px;color:#657973}.publish-fields select,.publish-fields input{width:100%;height:36px;min-width:0;padding:0 8px;border:1px solid #c8d6d2;border-radius:5px;background:#fff;color:#263b37}.publish-fields button{justify-self:start}.publication-list{display:grid;margin-top:14px}.publication-list>div{display:grid;grid-template-columns:minmax(0,2fr) minmax(90px,1fr) auto auto;gap:12px;align-items:center;padding:10px 0;border-bottom:1px solid #dce5e2;font-size:11px}.publication-list small{display:block;color:#82918c}.publication-list b{display:block}.publication-list .secondary-action{min-width:72px}@media(max-width:768px){.publish-fields{grid-template-columns:1fr}.publication-list>div{grid-template-columns:minmax(0,1fr) auto}.publication-list>div>:first-child{grid-column:1/-1}}
</style>
