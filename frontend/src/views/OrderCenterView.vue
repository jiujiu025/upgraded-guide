<script setup>
import BrandLogo from '../components/BrandLogo.vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { AlertTriangle, ArrowDownUp, Boxes, CalendarDays, Check, CircleDollarSign, Clock3, Database, FilePlus2, Filter, LogOut, Menu, PackageSearch, RefreshCw, ShieldOff, Users, X } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import { logout as endSession } from '../api'
import { session } from '../api/session'
import { getAdminNavigation } from '../navigation/adminNavigation'
import { createAdminResourceOrder, getAdminOrderCenterOptions, getAdminOrderCenterOrders, getAdminSubscriptions } from '../api'

const route = useRoute()
const router = useRouter()
const loading = ref(true)
const saving = ref(false)
const mobileNav = ref(false)
const error = ref('')
const feedback = ref('')
const activeTab = ref('subscriptions')
const options = ref({ customers: [], products: [], categories: [], supplier_products: [], supplier_regions: [], segments: [], quotes: [], retail_prices: [] })
const orders = ref([])
const subscriptions = ref([])
const createAttempt = ref(null)
const filters = reactive({ customer_id: '', product_id: '', expires_from: '', expires_to: '', source_type: '' })
const form = reactive({
  customer_id: '', resource_category: '', product_id: '', supplier_product_id: '', supplier_region_id: '',
  country_code: '', supplier_segment_snapshot_id: '', supplier_quote_id: '', quantity: 1,
  billing_days: '', remark: '',
})
const user = computed(() => session.user || {})
const nav = computed(() => getAdminNavigation(user.value.role))
const selectedProduct = computed(() => options.value.products.find((item) => item.id === Number(form.product_id)))
const mappedSupplierProducts = computed(() => options.value.supplier_products.filter((item) =>
  item.resource_category === form.resource_category && item.local_product_id === Number(form.product_id)))
const selectedSupplierProduct = computed(() => options.value.supplier_products.find((item) => item.id === Number(form.supplier_product_id)))
const countryOptions = computed(() => {
  const regions = options.value.supplier_regions.filter((item) => item.supplier_product_id === Number(form.supplier_product_id))
  const pricedCountries = new Set(options.value.retail_prices
    .filter((item) => item.resource_category === form.resource_category)
    .map((item) => item.country_code))
  const unique = new Map(regions.filter((item) => pricedCountries.has(item.country_code))
    .map((item) => [item.country_code, item.country_name]))
  return [...unique].map(([code, name]) => ({ code, name })).sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'))
})
const countryRegion = computed(() => options.value.supplier_regions.find((item) =>
  item.supplier_product_id === Number(form.supplier_product_id) && item.country_code === form.country_code))
const segmentOptions = computed(() => options.value.segments.filter((item) =>
  item.supplier_product_id === Number(form.supplier_product_id)
  && item.country_code === form.country_code
  && item.requested_quantity >= Number(form.quantity)))
const quoteOptions = computed(() => options.value.quotes.filter((item) =>
  item.supplier_product_id === Number(form.supplier_product_id)
  && (!item.country_code || item.country_code === form.country_code)
  && (!item.duration_days || item.duration_days === Number(form.billing_days || selectedProduct.value?.billing_days))))
const selectedQuote = computed(() => options.value.quotes.find((item) => item.id === Number(form.supplier_quote_id)))
const selectedRetailPrice = computed(() => options.value.retail_prices.find((item) =>
  item.resource_category === form.resource_category && item.country_code === form.country_code))
const billingDayOptions = computed(() => Object.keys(selectedRetailPrice.value?.prices || {})
  .map(Number).filter((days) => days >= 30).sort((a, b) => a - b))
const saleUnitPrice = computed(() => selectedRetailPrice.value?.prices?.[String(form.billing_days)] ?? null)
const retailTotal = computed(() => Number(saleUnitPrice.value || 0) * Number(form.quantity || 0))
const costTotal = computed(() => Number(selectedQuote.value?.unit_cost || 0) * Number(form.quantity || 0))
const pendingCount = computed(() => orders.value.filter((item) => ['pending_payment', 'pending_confirm'].includes(item.status)).length)

function textError(err, fallback) {
  return err?.userMessage || fallback
}
function formatDate(value) {
  return value ? new Date(value).toLocaleDateString('zh-CN') : '—'
}
function formatDateTime(value) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
}
function money(value, digits = 2) {
  return value == null ? '—' : `¥${Number(value).toFixed(digits)}`
}
function stateLabel(status) {
  return ({ active: '使用中', expired: '已到期', disabled: '已停用', pending_payment: '待付款', pending_confirm: '待审核', paid: '已付款', pending_delivery: '待交付', partially_delivered: '部分交付', delivered: '已交付', cancelled: '已取消', completed: '已完成' })[status] || status || '未知'
}
function stateTone(status) {
  return ['active', 'paid', 'completed', 'delivered'].includes(status) ? 'good'
    : ['pending_payment', 'pending_confirm', 'pending_delivery', 'partially_delivered'].includes(status) ? 'warn'
      : ['expired', 'cancelled', 'disabled'].includes(status) ? 'muted' : 'info'
}
function notify(message) {
  feedback.value = message
  window.clearTimeout(notify.timer)
  notify.timer = window.setTimeout(() => { feedback.value = '' }, 3600)
}
function newIdempotencyKey() {
  if (crypto.randomUUID) return crypto.randomUUID()
  return Array.from(crypto.getRandomValues(new Uint8Array(18)), (value) => value.toString(16).padStart(2, '0')).join('')
}
async function logout() {
  await endSession()
  router.replace('/admin/login')
}
async function loadOptions() {
  const response = await getAdminOrderCenterOptions()
  options.value = response.data
  if (!form.resource_category) form.resource_category = options.value.categories[0]?.code || ''
}
async function loadOrders() {
  orders.value = (await getAdminOrderCenterOrders()).data
}
async function loadSubscriptions() {
  const params = Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== ''))
  subscriptions.value = (await getAdminSubscriptions(params)).data
}
async function load() {
  loading.value = true
  error.value = ''
  try {
    await Promise.all([loadOptions(), loadOrders(), loadSubscriptions()])
  } catch (err) {
    error.value = textError(err, '订单中心暂时无法加载，请重试')
  } finally {
    loading.value = false
  }
}
function resetDependentFields() {
  form.supplier_product_id = ''
  form.supplier_region_id = ''
  form.country_code = ''
  form.supplier_segment_snapshot_id = ''
  form.supplier_quote_id = ''
  form.billing_days = ''
}
function selectDefaultBillingDays() {
  const productDays = Number(selectedProduct.value?.billing_days)
  form.billing_days = billingDayOptions.value.includes(productDays)
    ? String(productDays)
    : String(billingDayOptions.value[0] || '')
}
watch(() => [form.resource_category, form.product_id], resetDependentFields)
watch(() => form.supplier_product_id, () => {
  form.supplier_region_id = ''
  form.country_code = ''
  form.supplier_segment_snapshot_id = ''
  form.supplier_quote_id = ''
})
watch(() => form.country_code, () => {
  form.supplier_region_id = String(countryRegion.value?.id || '')
  form.supplier_segment_snapshot_id = ''
  form.supplier_quote_id = ''
  selectDefaultBillingDays()
})
watch(billingDayOptions, selectDefaultBillingDays)
watch(() => form.quantity, () => { form.supplier_segment_snapshot_id = '' })
watch(() => quoteOptions.value, (items) => {
  if (!items.some((item) => item.id === Number(form.supplier_quote_id))) form.supplier_quote_id = ''
})
async function applyFilters() {
  error.value = ''
  try { await loadSubscriptions() } catch (err) { error.value = textError(err, '订阅筛选失败') }
}
async function createOrder() {
  error.value = ''
  if (!form.customer_id || !form.product_id || !form.supplier_product_id || !form.supplier_region_id || !form.supplier_segment_snapshot_id || !form.supplier_quote_id || !saleUnitPrice.value) {
    error.value = '请先选好客户、产品、国家、有效周期、销售价、可用网段和成本报价'
    return
  }
  if (!Number.isInteger(Number(form.quantity)) || Number(form.quantity) < 1 || Number(form.quantity) > 300 || !billingDayOptions.value.includes(Number(form.billing_days))) {
    error.value = '数量需为 1 至 300 的整数，周期必须选择已配置的 30 天以上价格档'
    return
  }
  saving.value = true
  try {
    const payload = {
      customer_id: Number(form.customer_id), product_id: Number(form.product_id),
      supplier_product_id: Number(form.supplier_product_id), supplier_region_id: Number(form.supplier_region_id),
      supplier_segment_snapshot_id: Number(form.supplier_segment_snapshot_id), supplier_quote_id: Number(form.supplier_quote_id),
      resource_category: form.resource_category, quantity: Number(form.quantity),
      billing_days: Number(form.billing_days || selectedProduct.value?.billing_days), remark: form.remark || null,
    }
    const fingerprint = JSON.stringify(payload)
    if (!createAttempt.value || createAttempt.value.fingerprint !== fingerprint) {
      createAttempt.value = { fingerprint, key: newIdempotencyKey() }
    }
    await createAdminResourceOrder({ ...payload, idempotency_key: createAttempt.value.key })
    createAttempt.value = null
    notify('资源池订单已创建，状态为待付款 / 待人工审核')
    await Promise.all([loadOrders(), loadSubscriptions()])
    activeTab.value = 'create'
  } catch (err) {
    error.value = textError(err, '创建订单失败；请检查库存和报价快照是否过期')
  } finally {
    saving.value = false
  }
}
onMounted(load)
</script>

<template>
  <div class="workspace-shell order-center-shell">
    <aside class="workspace-sidebar" :class="{ open: mobileNav }">
      <div class="workspace-brand"><BrandLogo eager /><button aria-label="关闭导航" @click="mobileNav = false"><X :size="20" /></button></div>
      <p class="workspace-role">管理后台</p>
      <nav><button v-for="[label, path, icon] in nav" :key="path" :class="{ active: route.path === path || (path !== '/admin' && route.path.startsWith(`${path}/`)) }" @click="router.push(path); mobileNav = false"><component :is="icon" :size="17" />{{ label }}</button></nav>
      <div class="workspace-user"><span>{{ user.display_name || user.username }}</span><small>{{ user.role }}</small><button @click="logout"><LogOut :size="15" />退出登录</button></div>
    </aside>

    <div class="workspace-main">
      <header class="workspace-header order-header">
        <button class="workspace-menu" aria-label="打开导航" @click="mobileNav = true"><Menu :size="21" /></button>
        <div><h1>订单中心</h1><p>订阅线路与资源池订单统一管理</p></div>
        <span class="purchase-guard"><ShieldOff :size="15" />自动采购未启用</span>
      </header>

      <main class="workspace-content order-content">
        <div v-if="feedback" class="workspace-toast" role="status">{{ feedback }}</div>
        <div v-if="error" class="order-alert" role="alert"><AlertTriangle :size="17" /><span>{{ error }}</span><button type="button" @click="error = ''"><X :size="15" /></button></div>
        <div v-if="loading" class="order-skeleton" aria-label="订单中心加载中"><span v-for="n in 8" :key="n"></span></div>
        <template v-else>
          <section class="order-toolbar">
            <div class="order-tabs" role="tablist" aria-label="订单中心视图">
              <button type="button" role="tab" :aria-selected="activeTab === 'subscriptions'" :class="{ active: activeTab === 'subscriptions' }" @click="activeTab = 'subscriptions'"><CalendarDays :size="16" />订阅管理 <span>{{ subscriptions.length }}</span></button>
              <button type="button" role="tab" :aria-selected="activeTab === 'create'" :class="{ active: activeTab === 'create' }" @click="activeTab = 'create'"><FilePlus2 :size="16" />创建订单</button>
            </div>
            <button type="button" class="refresh-button" aria-label="刷新订单数据" title="刷新" @click="load"><RefreshCw :size="16" /></button>
          </section>

          <template v-if="activeTab === 'subscriptions'">
            <section class="subscription-summary" aria-label="订阅概况">
              <div><span>当前筛选线路</span><b>{{ subscriptions.length }}</b></div>
              <div><span>即将到期</span><b>{{ subscriptions.filter((line) => new Date(line.expires_at) <= Date.now() + 7 * 86400000 && new Date(line.expires_at) > Date.now()).length }}</b></div>
              <div><span>待付款订单</span><b>{{ pendingCount }}</b></div>
            </section>
            <section class="filter-bar" aria-label="订阅筛选">
              <label><span>客户</span><select v-model="filters.customer_id"><option value="">全部客户</option><option v-for="item in options.customers" :key="item.id" :value="item.id">{{ item.name }} · {{ item.email }}</option></select></label>
              <label><span>产品</span><select v-model="filters.product_id"><option value="">全部产品</option><option v-for="item in options.products" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
              <label><span>到期起</span><input v-model="filters.expires_from" type="date" /></label>
              <label><span>到期止</span><input v-model="filters.expires_to" type="date" /></label>
              <label><span>来源</span><select v-model="filters.source_type"><option value="">全部来源</option><option value="owned">平台商品</option><option value="resource_pool">资源池 1</option></select></label>
              <button type="button" class="primary-button" @click="applyFilters"><Filter :size="15" />筛选</button>
            </section>
            <section class="table-section">
              <div class="section-title"><div><h2>已开通线路</h2><p>仅显示已实际创建的客户线路，待付款订单不会出现在此处。</p></div><span>{{ subscriptions.length }} 条记录</span></div>
              <div v-if="!subscriptions.length" class="empty-state"><CalendarDays :size="22" /><b>没有符合条件的线路</b><span>调整客户、产品或到期时间筛选条件。</span></div>
              <div v-else class="table-wrap subscription-table"><table><thead><tr><th>客户</th><th>线路产品</th><th>来源</th><th>IP / 资源</th><th>状态</th><th>开通时间</th><th>到期时间</th></tr></thead><tbody><tr v-for="line in subscriptions" :key="line.id"><td><b>{{ line.customer_name }}</b><small>客户 #{{ line.customer_id }}</small></td><td><b>{{ line.product_name }}</b><small>线路 #{{ line.id }}</small></td><td><span class="source-label" :data-source="line.source_type">{{ line.source_type === 'resource_pool' ? '资源池 1' : '玖玖云' }}</span></td><td>{{ line.static_ip || '—' }}</td><td><span class="state-label" :data-tone="stateTone(line.status)">{{ stateLabel(line.status) }}</span></td><td>{{ formatDate(line.started_at) }}</td><td><b>{{ formatDate(line.expires_at) }}</b></td></tr></tbody></table></div>
            </section>
          </template>

          <template v-else>
            <section class="procurement-notice"><ShieldOff :size="18" /><span><b>订单创建不会触发供应商采购</b><small>订单会保存客户售价、成本与库存快照，并进入待付款/待人工审核。支付、供应商采购、退款补偿和真实开通流程尚未启用。</small></span></section>
            <section class="create-layout">
              <div class="form-panel">
                <div class="section-title"><div><h2>创建资源池订单</h2><p>使用最新的供应商映射、网段检测和报价快照。</p></div><span class="step-mark"><FilePlus2 :size="15" />人工下单</span></div>
                <form class="order-form" @submit.prevent="createOrder">
                  <label class="field-wide"><span>客户账号</span><select v-model="form.customer_id" required><option value="">选择已注册客户</option><option v-for="item in options.customers" :key="item.id" :value="item.id">{{ item.name }} · {{ item.email }}</option></select></label>
                  <label><span>资源分类</span><select v-model="form.resource_category" required><option value="">选择类型</option><option v-for="item in options.categories" :key="item.code" :value="item.code">{{ item.label }}</option></select></label>
                  <label><span>平台线路产品</span><select v-model="form.product_id" required><option value="">选择业务产品</option><option v-for="item in options.products" :key="item.id" :value="item.id">{{ item.name }} · {{ item.product_type }}</option></select></label>
                  <label class="field-wide"><span>资源来源 · 资源池 1</span><select v-model="form.supplier_product_id" required :disabled="!form.product_id"><option value="">选择已映射的供应商商品</option><option v-for="item in mappedSupplierProducts" :key="item.id" :value="item.id">{{ item.name }} · #{{ item.external_product_ref }}</option></select><small v-if="form.product_id && !mappedSupplierProducts.length" class="field-hint">所选平台产品与资源分类下暂无人工映射。</small></label>
                  <label><span>国家 / 地区</span><select v-model="form.country_code" required :disabled="!form.supplier_product_id"><option value="">选择国家</option><option v-for="item in countryOptions" :key="item.code" :value="item.code">{{ item.name }} · {{ item.code }}</option></select></label>
                  <label><span>开通数量</span><input v-model.number="form.quantity" type="number" min="1" max="300" step="1" required /></label>
                  <label class="field-wide"><span>可选 IP 段</span><select v-model="form.supplier_segment_snapshot_id" required :disabled="!form.country_code"><option value="">选择新鲜且数量充足的快照</option><option v-for="item in segmentOptions" :key="item.id" :value="item.id">{{ item.cidr }} · 检测 {{ item.requested_quantity }} 条 · {{ formatDateTime(item.checked_at) }}</option></select><small class="field-hint">检测结果不是库存预留；正式采购前仍需人工复核供应商实时库存。</small></label>
                  <label class="field-wide"><span>成本报价快照</span><select v-model="form.supplier_quote_id" required :disabled="!form.country_code"><option value="">选择最新适用报价</option><option v-for="item in quoteOptions" :key="item.id" :value="item.id">{{ money(item.unit_cost, 4) }} / 条 · {{ item.duration_days || form.billing_days || selectedProduct?.billing_days }} 天 · {{ formatDateTime(item.quoted_at) }}</option></select></label>
                  <label><span>周期</span><select v-model="form.billing_days" required :disabled="!billingDayOptions.length"><option value="">选择 30 天以上周期</option><option v-for="days in billingDayOptions" :key="days" :value="String(days)">{{ days }} 天</option></select></label>
                  <label><span>客户售价 / 条</span><div class="money-input"><i>¥</i><input :value="saleUnitPrice ?? ''" type="text" readonly :placeholder="selectedRetailPrice ? '该周期暂无售价' : '选择国家后自动定价'" /></div></label>
                  <label class="field-wide"><span>订单备注</span><textarea v-model="form.remark" maxlength="1000" rows="3" placeholder="添加交付或客户要求，最多 1000 字"></textarea></label>
                  <div class="price-summary field-wide"><div><span>成本快照合计</span><b>{{ selectedQuote ? money(costTotal, 4) : '待选择报价' }}</b></div><div><span>客户应付合计</span><b>{{ saleUnitPrice ? money(retailTotal) : '待选择有效售价' }}</b></div><small>客户售价按后台价目自动匹配；成本来自供应商报价快照。创建订单不会扣款或调用供应商购买接口。</small></div>
                  <button class="submit-order field-wide" type="submit" :disabled="saving || !options.customers.length || !segmentOptions.length || !quoteOptions.length || !billingDayOptions.length || !saleUnitPrice"><Check v-if="!saving" :size="17" /><RefreshCw v-else :size="16" class="spin" />{{ saving ? '创建中…' : '创建待付款订单' }}</button>
                </form>
              </div>
              <aside class="order-side-panel">
                <div><CircleDollarSign :size="18" /><span><b>价格快照</b><small>记录创建时客户售价与供应商报价</small></span></div>
                <div><Clock3 :size="18" /><span><b>库存检测有效期</b><small>仅可选择未过期且满足数量的检测结果</small></span></div>
                <div><ShieldOff :size="18" /><span><b>采购保护</b><small>管理员确认、手动交付和供应商写接口均保持禁用</small></span></div>
                <p>若网段或报价快照过期，请先到资源池重新检测或同步。</p>
              </aside>
            </section>
            <section class="table-section recent-orders">
              <div class="section-title"><div><h2>最近订单</h2><p>资源池订单需完成支付、管理员审批、沙箱及补偿流程后才能接入自动采购。</p></div><span>{{ orders.length }} 条</span></div>
              <div v-if="!orders.length" class="empty-state"><PackageSearch :size="22" /><b>还没有订单</b><span>创建订单后会在此记录客户、价格与处理状态。</span></div>
              <div v-else class="table-wrap order-table"><table><thead><tr><th>订单编号</th><th>客户</th><th>产品 / 国家</th><th>来源</th><th>数量</th><th>售价</th><th>成本快照</th><th>状态</th></tr></thead><tbody><tr v-for="order in orders.slice(0, 50)" :key="order.id"><td><b>{{ order.order_no }}</b><small>{{ formatDateTime(order.created_at) }}</small></td><td>{{ order.customer_name }}</td><td><b>{{ order.product_name }}</b><small>{{ order.region_name || '—' }}</small></td><td><span class="source-label" :data-source="order.source_type">{{ order.source_type === 'resource_pool' ? '资源池 1' : '玖玖云' }}</span></td><td>{{ order.quantity }}</td><td>{{ money(order.total_amount) }}</td><td>{{ order.supplier_cost == null ? '—' : money(order.supplier_cost, 4) }}</td><td><span class="state-label" :data-tone="stateTone(order.status)">{{ stateLabel(order.status) }}</span></td></tr></tbody></table></div>
            </section>
          </template>
        </template>
      </main>
    </div>
  </div>
</template>

<style scoped>
.order-center-shell{background:#f2f5f3;color:#192824}
.order-center-shell .workspace-sidebar{background:#fff;color:#21332e;border-right:1px solid #dce5e1;box-shadow:2px 0 12px #182d2508}
.order-center-shell .workspace-role{color:#7c9189}
.order-center-shell .workspace-sidebar nav button{color:#526860}
.order-center-shell .workspace-sidebar nav button:hover,.order-center-shell .workspace-sidebar nav button.active{background:#e7f4ef;color:#176b58}
.order-center-shell .workspace-sidebar nav button.active svg{color:#17856b}
.order-center-shell .workspace-user{border-color:#e1e9e5}
.order-center-shell .workspace-user small,.order-center-shell .workspace-user button{color:#71857d}
.order-header{justify-content:flex-start;background:#fff}
.order-header>div{display:grid;gap:4px}
.order-header p{font-family:inherit;font-size:12px;color:#748780;margin:0}
.purchase-guard{margin-left:auto;display:inline-flex;align-items:center;gap:7px;padding:8px 10px;border:1px solid #ecd7b2;border-radius:5px;background:#fff8ea;color:#805a1c;font-size:11px;font-weight:700}
.order-content{max-width:1600px}
.order-alert{display:flex;align-items:center;gap:10px;margin-bottom:14px;padding:11px 13px;border:1px solid #e6c2bc;border-radius:6px;background:#fff2ef;color:#8e3e32;font-size:12px}
.order-alert span{flex:1}.order-alert button{display:grid;place-items:center}
.order-skeleton{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.order-skeleton span{height:105px;border-radius:6px;background:#dfe8e4;animation:skeleton 1.1s ease-in-out infinite alternate}
.order-skeleton span:nth-child(n+5){grid-column:span 2;height:300px}
@keyframes skeleton{to{background:#edf2ef}}
.order-toolbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:15px;border-bottom:1px solid #d9e3df}
.order-tabs{display:flex;gap:4px}
.order-tabs button{display:flex;align-items:center;gap:8px;min-height:46px;padding:0 14px;border-bottom:2px solid transparent;color:#667970;font-size:12px;font-weight:650}
.order-tabs button:hover{color:#176c58}.order-tabs button.active{border-color:#168267;color:#126d56}
.order-tabs button span{min-width:20px;padding:2px 6px;border-radius:10px;background:#e7efeb;color:#54685f;font-size:10px;text-align:center}
.refresh-button{width:34px;height:34px;display:grid;place-items:center;border:1px solid #d3dfd9;border-radius:5px;background:#fff;color:#466359;transition:background .18s ease,transform .18s ease}
.refresh-button:hover{background:#e9f4ef;transform:rotate(15deg)}
.subscription-summary{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));margin-bottom:14px;border:1px solid #dce5e1;border-radius:6px;background:#fff}
.subscription-summary div{display:grid;gap:6px;padding:13px 17px;border-right:1px solid #e6ece9}.subscription-summary div:last-child{border:0}
.subscription-summary span,.filter-bar label span{color:#71847c;font-size:10px}.subscription-summary b{font-size:20px;font-variant-numeric:tabular-nums}
.filter-bar{display:grid;grid-template-columns:1.35fr 1fr .85fr .85fr .9fr auto;align-items:end;gap:9px;margin-bottom:14px;padding:13px;border:1px solid #dce5e1;border-radius:6px;background:#fff}
.filter-bar label,.order-form label{display:grid;gap:5px;min-width:0}
.filter-bar select,.filter-bar input,.order-form select,.order-form input,.order-form textarea{width:100%;min-width:0;height:36px;padding:0 9px;border:1px solid #cbd9d3;border-radius:5px;background:#fff;color:#243831;font:12px inherit}
.filter-bar select:focus,.filter-bar input:focus,.order-form select:focus,.order-form input:focus,.order-form textarea:focus{outline:2px solid #48a58b;outline-offset:1px}
.primary-button,.secondary-button{display:inline-flex;align-items:center;justify-content:center;gap:7px;min-height:36px;padding:7px 12px;border-radius:5px;font-size:11px;font-weight:700;white-space:nowrap}
.primary-button{background:#167c62;color:#fff}.primary-button:hover{background:#10684f}
.table-section,.form-panel{min-width:0;margin-bottom:15px;padding:16px;border:1px solid #dce5e1;border-radius:6px;background:#fff}
.section-title{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:14px}
.section-title h2{margin:0;color:#22352e;font-size:15px;line-height:1.35}.section-title p{margin:4px 0 0;color:#71837b;font-size:10px;line-height:1.5}.section-title>span{color:#71847c;font-size:10px;white-space:nowrap}
.table-wrap{max-width:100%;overflow:auto;border:1px solid #e0e8e4;border-radius:5px}
table{width:100%;border-collapse:collapse;font-size:11px;text-align:left;white-space:nowrap}
th{height:37px;padding:0 12px;background:#f0f5f2;color:#657a70;font-size:10px;font-weight:650}
td{height:53px;padding:7px 12px;border-top:1px solid #e8eeeb;color:#374d44}
tbody tr:hover{background:#f8fbf9}td b,td small{display:block}td small{margin-top:3px;color:#83938c;font-size:9px}
.source-label,.state-label{display:inline-flex;align-items:center;min-height:22px;padding:3px 7px;border-radius:4px;background:#e8f0ec;color:#496158;font-size:10px;font-weight:650}
.source-label[data-source="resource_pool"]{background:#e4eefb;color:#355e94}.state-label[data-tone="good"]{background:#e1f3ea;color:#21684e}.state-label[data-tone="warn"]{background:#fff1d8;color:#805917}.state-label[data-tone="info"]{background:#e4eefb;color:#355e94}.state-label[data-tone="muted"]{background:#edf0ee;color:#697870}
.empty-state{min-height:150px;display:grid;justify-items:center;align-content:center;gap:7px;color:#657971;text-align:center}.empty-state b{font-size:12px}.empty-state span{color:#87958e;font-size:10px}
.procurement-notice{display:flex;align-items:flex-start;gap:11px;margin-bottom:14px;padding:12px 14px;border:1px solid #e8d3ab;border-radius:6px;background:#fff8eb;color:#755316}.procurement-notice>span{display:grid;gap:3px}.procurement-notice b{font-size:12px}.procurement-notice small{color:#806b43;font-size:10px;line-height:1.5}
.create-layout{display:grid;grid-template-columns:minmax(0,1fr) 245px;align-items:start;gap:14px}
.form-panel{padding:17px}
.step-mark{display:inline-flex;align-items:center;gap:6px;color:#60766d;font-size:10px}
.order-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.order-form label>span{color:#576d63;font-size:10px;font-weight:650}
.order-form textarea{height:auto;min-height:76px;padding:9px;resize:vertical}
.field-wide{grid-column:1/-1}
.field-hint{color:#7f8e86;font-size:9px;line-height:1.4}
.money-input{position:relative}.money-input i{position:absolute;left:10px;top:10px;color:#74857d;font-size:12px;font-style:normal}.money-input input{padding-left:25px}
.price-summary{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:13px;border:1px solid #e0e9e4;border-radius:5px;background:#f6faf7}
.price-summary div{display:grid;gap:4px}.price-summary span{color:#70837a;font-size:10px}.price-summary b{color:#185e49;font-size:17px;font-variant-numeric:tabular-nums}
.price-summary small{grid-column:1/-1;color:#7e8d85;font-size:9px;line-height:1.45}
.submit-order{min-height:40px;display:flex;align-items:center;justify-content:center;gap:8px;border-radius:5px;background:#167c62;color:#fff;font-size:12px;font-weight:700;transition:background .18s ease,transform .18s ease}
.submit-order:hover:not(:disabled){background:#10684f;transform:translateY(-1px)}.submit-order:disabled{opacity:.48;cursor:not-allowed}
.spin{animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
.order-side-panel{display:grid;gap:0;padding:4px 0}.order-side-panel>div{display:flex;align-items:flex-start;gap:9px;padding:12px 8px;border-bottom:1px solid #e0e8e4;color:#34745e}.order-side-panel>div span{display:grid;gap:4px}.order-side-panel b{color:#354c42;font-size:11px}.order-side-panel small,.order-side-panel p{color:#798981;font-size:9px;line-height:1.5}.order-side-panel p{margin:12px 8px;padding:10px;border-radius:5px;background:#edf4f0}
.recent-orders{margin-top:2px}
@media(max-width:1100px){.filter-bar{grid-template-columns:repeat(3,minmax(0,1fr))}.create-layout{grid-template-columns:minmax(0,1fr) 210px}}
@media(max-width:800px){.order-center-shell{display:block}.order-center-shell .workspace-sidebar{position:fixed}.order-center-shell .workspace-header{height:76px;padding:0 18px}.order-content{padding:18px}.create-layout{grid-template-columns:1fr}.order-side-panel{grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.order-side-panel>div{padding:9px 6px;border:1px solid #e0e8e4;border-radius:5px}.order-side-panel p{grid-column:1/-1;margin:0}.table-wrap{overflow:visible;border:0}.table-wrap table,.table-wrap tbody,.table-wrap tr,.table-wrap td{display:block;width:100%}.table-wrap thead{display:none}.table-wrap tr{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:0 0 9px;padding:12px;border:1px solid #dfe7e3;border-radius:5px;background:#fff}.table-wrap td{height:auto;min-width:0;padding:0;border:0;white-space:normal;overflow-wrap:anywhere}.table-wrap td::before{display:block;margin-bottom:3px;color:#82918a;font-size:9px;content:attr(data-label)}}
@media(max-width:520px){.order-content{padding:14px}.order-header{padding:0 14px}.order-header h1{font-size:19px}.purchase-guard{font-size:0;padding:8px}.purchase-guard svg{flex:none}.order-tabs button{padding:0 9px;font-size:11px}.subscription-summary div{padding:11px 9px}.subscription-summary b{font-size:17px}.filter-bar{grid-template-columns:1fr 1fr;padding:10px}.filter-bar label:first-child,.filter-bar button{grid-column:1/-1}.order-form{grid-template-columns:1fr}.field-wide{grid-column:auto}.price-summary{grid-template-columns:1fr 1fr}.order-side-panel{grid-template-columns:1fr}.order-side-panel p{grid-column:auto}.table-section,.form-panel{padding:13px}.table-wrap tr{grid-template-columns:1fr}.order-skeleton{grid-template-columns:repeat(2,minmax(0,1fr))}.order-skeleton span:nth-child(n+5){grid-column:span 2}}
@media(max-width:800px){.subscription-table td:nth-child(1)::before{content:'客户'}.subscription-table td:nth-child(2)::before{content:'线路产品'}.subscription-table td:nth-child(3)::before{content:'来源'}.subscription-table td:nth-child(4)::before{content:'IP / 资源'}.subscription-table td:nth-child(5)::before{content:'状态'}.subscription-table td:nth-child(6)::before{content:'开通时间'}.subscription-table td:nth-child(7)::before{content:'到期时间'}.order-table td:nth-child(1)::before{content:'订单编号'}.order-table td:nth-child(2)::before{content:'客户'}.order-table td:nth-child(3)::before{content:'产品 / 国家'}.order-table td:nth-child(4)::before{content:'来源'}.order-table td:nth-child(5)::before{content:'数量'}.order-table td:nth-child(6)::before{content:'售价'}.order-table td:nth-child(7)::before{content:'成本快照'}.order-table td:nth-child(8)::before{content:'状态'}}
@media(max-width:800px){.order-center-shell .workspace-brand button{color:#253932}}
@media(prefers-reduced-motion:reduce){.order-skeleton span,.spin{animation:none}.refresh-button,.submit-order{transition:none}}
</style>
