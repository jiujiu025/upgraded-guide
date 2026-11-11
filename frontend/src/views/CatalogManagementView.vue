<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { Check, ChevronLeft, ChevronRight, CircleAlert, Database, MapPin, PackageCheck, Plus, RefreshCw, Save, Search, Tags, Trash2, X } from 'lucide-vue-next'
import AppSelect from '../components/AppSelect.vue'
import PriceCell from '../components/PriceCell.vue'
import {
  bulkUpdateCatalogListings, bulkUpdateManagedCatalogProducts, bulkUpdateManagedRetailPrices,
  createManagedCity, deleteManagedCity, getCatalogListings, getManagedCatalogProducts, getManagedCities,
  getManagedRetailPrices, setManagedProductActive, syncSupplierCitySegments, updateCatalogListing,
  updateManagedCatalogProduct, updateManagedCity, updateManagedRetailPrice,
} from '../api'

const tab = ref('products')
const busy = ref(false)
const loading = ref(false)
const error = ref('')
const notice = ref('')
const products = ref([])
const productTotal = ref(0)
const productPage = ref(1)
const productFilter = reactive({ keyword: '', stock: '' })
const selectedProducts = ref(new Set())
const productBulkPrice = ref('')
const productBulkStock = ref('unchanged')
const prices = ref([])
const priceTotal = ref(0)
const pricePage = ref(1)
const priceCategories = ref([])
const priceCountries = ref([])
const priceBillingDays = ref([])
const priceFilter = reactive({ resource_category: '', country_code: '', billing_days: '' })
const selectedPrices = ref(new Set())
const retailBulkPrice = ref('')
const pendingBulk = ref('')
// 官网可见性：三条线 × 全部启用国家的上架状态
const listings = ref({ lines: [], regions: [], cells: [], summary: {}, pools: [], scope: {} })
const listingFilter = ref('')
const listingBusy = ref(false)
// '' = 平台级（全池生效）；数字 = 只改该资源池
const listingPool = ref('')
const listingPoolOptions = computed(() => [
  { value: '', label: '平台级（全池）' },
  ...(listings.value.pools || []).map((pool) => ({ value: pool.id, label: pool.name })),
])
// 城市与号段：客户可选的精细化选项（按 分类 + 国家 维护）
const cities = ref([])
const cityMeta = ref({ categories: [], countries: [] })
const cityBusy = ref(false)
const citySyncBusy = ref(false)
const cityFilter = reactive({ resource_category: '', country_code: '', keyword: '' })
const cityForm = reactive({ resource_category: 'static_residential', country_code: '', city_code: '', city_name: '', ip_segments: '', sort_order: 100 })
const listingFilterOptions = [
  { value: '', label: '全部国家' },
  { value: 'missing', label: '仅缺报价' },
  { value: 'unlisted', label: '仅已下架' },
]
const listingRows = computed(() => {
  const rows = new Map()
  for (const cell of listings.value.cells || []) {
    if (!rows.has(cell.region_id)) {
      rows.set(cell.region_id, { id: cell.region_id, name: cell.region_name, code: cell.country_code, cells: {} })
    }
    rows.get(cell.region_id).cells[cell.line_code] = cell
  }
  const all = [...rows.values()]
  if (listingFilter.value === 'missing') return all.filter((row) => Object.values(row.cells).every((c) => !c.has_price))
  if (listingFilter.value === 'unlisted') return all.filter((row) => Object.values(row.cells).every((c) => !c.visible))
  return all
})
const productPageCount = computed(() => Math.max(1, Math.ceil(productTotal.value / 25)))
const pricePageCount = computed(() => Math.max(1, Math.ceil(priceTotal.value / 50)))
const selectedProductList = computed(() => products.value.filter((item) => selectedProducts.value.has(item.id)))
const selectedPriceList = computed(() => prices.value.filter((item) => selectedPrices.value.has(item.id)))
const productBulkSummary = computed(() => {
  const changes = []
  if (productBulkPrice.value !== '') changes.push(`售价统一调整为 ¥${Number(productBulkPrice.value).toFixed(2)}`)
  if (productBulkStock.value !== 'unchanged') changes.push(`售卖状态设为${productBulkStock.value === 'in_stock' ? '有货' : '无货'}`)
  return `${selectedProducts.value.size} 个商品：${changes.join('；')}`
})

// 下拉选项：改用自绘 AppSelect 后，选项要显式给出来。
const stockFilterOptions = [
  { value: '', label: '全部状态' },
  { value: 'in_stock', label: '有货' },
  { value: 'out_of_stock', label: '无货' },
]
const bulkStockOptions = [
  { value: 'unchanged', label: '保持不变' },
  { value: 'in_stock', label: '设为有货' },
  { value: 'out_of_stock', label: '设为无货' },
]
const productStockOptions = [
  { value: true, label: '有货' },
  { value: false, label: '无货' },
]
const categoryOptions = computed(() => [
  { value: '', label: '全部类型' },
  ...priceCategories.value.map((item) => ({ value: item.code, label: item.label })),
])
const countryFilterOptions = computed(() => [
  { value: '', label: '全部国家/地区' },
  ...sortedPriceCountries.value.map((item) => ({
    value: item.code,
    label: `${item.name || countryName(item.code)}（${item.code}）`,
  })),
])
const billingDayOptions = computed(() => [
  { value: '', label: '全部周期' },
  ...priceBillingDays.value.map((days) => ({ value: String(days), label: `${days} 天` })),
])
const cityCategoryOptions = computed(() => [
  { value: '', label: '全部类型' },
  ...cityMeta.value.categories.map((item) => ({ value: item.code, label: item.label })),
])
const cityCountryOptions = computed(() => [
  { value: '', label: '全部国家/地区' },
  ...cityMeta.value.countries.map((item) => ({ value: item.code, label: `${item.name}（${item.code}）` })),
])
const cityFormCountryOptions = computed(() => cityMeta.value.countries.map((item) => ({
  value: item.code, label: `${item.name}（${item.code}）`,
})))
const cityFormCategoryOptions = computed(() => cityMeta.value.categories.map((item) => ({
  value: item.code, label: item.label,
})))

const priceLabel = (value) => `¥${Number(value || 0).toFixed(2)}`
const dateLabel = (value) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
const countryDisplayNames = new Intl.DisplayNames(['zh-CN'], { type: 'region' })
const countryName = (code) => {
  const normalized = String(code || '').toUpperCase()
  try {
    return countryDisplayNames.of(normalized) || normalized || '未知国家'
  } catch {
    return normalized || '未知国家'
  }
}
const sortedPriceCountries = computed(() => [...priceCountries.value].sort((a, b) =>
  (a.name || countryName(a.code)).localeCompare(b.name || countryName(b.code), 'zh-CN')))

function showNotice(message) {
  notice.value = message
  window.clearTimeout(showNotice.timer)
  showNotice.timer = window.setTimeout(() => { notice.value = '' }, 3200)
}

async function loadProducts() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await getManagedCatalogProducts({
      keyword: productFilter.keyword.trim() || undefined,
      stock: productFilter.stock || undefined,
      page: productPage.value,
      page_size: 25,
    })
    products.value = data.items
    productTotal.value = data.total
  } catch (requestError) {
    error.value = requestError.userMessage || '商品目录加载失败'
  } finally {
    loading.value = false
  }
}

async function loadPrices() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await getManagedRetailPrices({
      resource_category: priceFilter.resource_category || undefined,
      country_code: priceFilter.country_code.trim().toUpperCase() || undefined,
      billing_days: priceFilter.billing_days ? Number(priceFilter.billing_days) : undefined,
      page: pricePage.value,
      page_size: 50,
    })
    prices.value = data.items
    priceTotal.value = data.total
    priceCategories.value = data.categories
    priceCountries.value = data.countries || []
    priceBillingDays.value = data.billing_days_options || []
  } catch (requestError) {
    error.value = requestError.userMessage || '国家周期零售价加载失败'
  } finally {
    loading.value = false
  }
}

async function loadListings() {
  loading.value = true
  error.value = ''
  try {
    listings.value = (await getCatalogListings(listingPool.value ? { pool_id: listingPool.value } : {})).data
  } catch (requestError) {
    error.value = requestError.userMessage || '官网可见性加载失败'
  } finally {
    loading.value = false
  }
}

async function loadCurrent() {
  pendingBulk.value = ''
  if (tab.value === 'products') await loadProducts()
  else if (tab.value === 'prices') await loadPrices()
  else if (tab.value === 'cities') await loadCities()
  else await loadListings()
}

async function loadCities() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await getManagedCities({
      resource_category: cityFilter.resource_category || undefined,
      country_code: cityFilter.country_code || undefined,
      keyword: cityFilter.keyword.trim() || undefined,
    })
    cities.value = data.items
    cityMeta.value = { categories: data.categories || [], countries: data.countries || [] }
    if (!cityForm.country_code && cityMeta.value.countries.length) {
      cityForm.country_code = cityMeta.value.countries[0].code
    }
  } catch (requestError) {
    error.value = requestError.userMessage || '城市与号段加载失败'
  } finally {
    loading.value = false
  }
}

/** 从资源池1拉真实城市与号段：只合并号段，不改手工维护的启停与展示名。 */
async function syncCitySegments() {
  if (citySyncBusy.value) return
  citySyncBusy.value = true
  error.value = ''
  try {
    const { data } = await syncSupplierCitySegments({ product_type_id: 3, country_code: cityFilter.country_code || undefined })
    await loadCities()
    const failed = (data.errors || []).length
    showNotice(`已同步 ${data.cities_seen} 个城市、${data.segments_saved} 个号段${failed ? `，${failed} 个失败` : ''}`)
  } catch (requestError) {
    error.value = requestError.userMessage || '同步城市号段失败，请确认资源池1的 API Key 已配置'
  } finally {
    citySyncBusy.value = false
  }
}

async function addCity() {
  if (cityBusy.value) return
  if (!cityForm.country_code || !cityForm.city_code.trim() || !cityForm.city_name.trim()) {
    error.value = '国家、城市码、城市名都必填'
    return
  }
  cityBusy.value = true
  error.value = ''
  try {
    await createManagedCity({
      resource_category: cityForm.resource_category,
      country_code: cityForm.country_code,
      city_code: cityForm.city_code.trim(),
      city_name: cityForm.city_name.trim(),
      ip_segments: cityForm.ip_segments.trim(),
      sort_order: Number(cityForm.sort_order) || 100,
    })
    cityForm.city_code = ''
    cityForm.city_name = ''
    cityForm.ip_segments = ''
    await loadCities()
    showNotice('城市已新增，客户下单时即可选择')
  } catch (requestError) {
    error.value = requestError.userMessage || '新增城市失败'
  } finally {
    cityBusy.value = false
  }
}

/** 行内保存：城市名 / 号段 / 排序 / 上下架都即时生效，不需要另找保存按钮。 */
async function patchCity(item, patch, successText) {
  if (cityBusy.value) return
  cityBusy.value = true
  error.value = ''
  try {
    await updateManagedCity(item.id, patch)
    await loadCities()
    if (successText) showNotice(successText)
  } catch (requestError) {
    error.value = requestError.userMessage || '保存城市失败'
    await loadCities()
  } finally {
    cityBusy.value = false
  }
}

function saveCityName(item, event) {
  const value = event.target.value.trim()
  if (!value || value === item.city_name) { event.target.value = item.city_name; return }
  return patchCity(item, { city_name: value }, `${item.city_code} 已改为「${value}」`)
}

function saveCitySegments(item, event) {
  const value = event.target.value.trim()
  if (value === (item.segments || []).join(',')) return
  return patchCity(item, { ip_segments: value }, `${item.city_code} 号段已更新`)
}

async function removeCity(item) {
  if (cityBusy.value) return
  if (typeof window !== 'undefined' && !window.confirm(`删除城市「${item.city_name}（${item.city_code}）」？已下单的订单不受影响。`)) return
  cityBusy.value = true
  error.value = ''
  try {
    await deleteManagedCity(item.id)
    await loadCities()
    showNotice('城市已删除')
  } catch (requestError) {
    error.value = requestError.userMessage || '删除失败'
  } finally {
    cityBusy.value = false
  }
}

function applyCityFilters() {
  loadCities()
}

/** 点格子直接切换：上架/下架是一次点击就生效的操作，不用再弹一次确认。 */
async function toggleListing(cell) {
  if (!cell || listingBusy.value) return
  const next = cell.visible ? 'unlisted' : 'listed'
  listingBusy.value = true
  error.value = ''
  try {
    await updateCatalogListing({
      line_code: cell.line_code, region_id: cell.region_id, status: next,
      pool_id: listingPool.value || null,
    })
    await loadListings()
    showNotice(`${cell.region_name} · ${cell.line_name} 已${next === 'listed' ? '上架' : '下架'}`)
  } catch (requestError) {
    error.value = requestError.userMessage || '切换上架状态失败'
  } finally {
    listingBusy.value = false
  }
}

async function bulkToggleLine(line, status) {
  if (listingBusy.value) return
  const regionIds = [...new Set((listings.value.cells || []).map((cell) => cell.region_id))]
  if (!regionIds.length) return
  listingBusy.value = true
  error.value = ''
  try {
    const { data } = await bulkUpdateCatalogListings({
      line_code: line.code, region_ids: regionIds, status, pool_id: listingPool.value || null,
    })
    await loadListings()
    showNotice(`${line.name}：${data.updated} 个国家已${status === 'listed' ? '上架' : '下架'}`)
  } catch (requestError) {
    error.value = requestError.userMessage || '批量切换失败'
  } finally {
    listingBusy.value = false
  }
}

/** 商品级上下架：停用后整条商品在所有国家都不出现。 */
async function toggleProductActive(item) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await setManagedProductActive(item.id, !item.is_active)
    await loadProducts()
    showNotice(`${item.name} 已${item.is_active ? '下架' : '上架'}`)
  } catch (requestError) {
    error.value = requestError.userMessage || '切换商品上架状态失败'
  } finally {
    busy.value = false
  }
}

/**
 * 单行保存：价格与售卖状态都即时生效，不再需要"先改、再找保存按钮"。
 * 价格走 PriceCell 的 ✓（或 Enter）作为二次确认；状态下拉点选项本身就是确认。
 * 保存的是这一行当前的两个字段，所以两个入口共用一个函数，不会互相覆盖。
 */
async function patchProduct(item, patch, successText) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await updateManagedCatalogProduct(item.id, patch)
    await loadProducts()
    showNotice(successText)
  } catch (requestError) {
    error.value = requestError.userMessage || '保存商品失败'
    await loadProducts() // 失败时用服务端值覆盖本地，避免界面显示没生效的价格
  } finally {
    busy.value = false
  }
}

function saveProductPrice(item, value) {
  if (!value || value === Number(item.price) || item.price_locked) return
  return patchProduct(item, { price: value }, `${item.name} 售价已改为 ¥${value}`)
}

function saveProductStock(item, value) {
  if (value === item.is_in_stock) return
  return patchProduct(item, { is_in_stock: value }, `${item.name} 已设为${value ? '有货' : '无货'}`)
}

async function saveRetailPrice(item, value) {
  if (busy.value || !Number.isFinite(value) || value <= 0 || value === Number(item.unit_price)) return
  busy.value = true
  error.value = ''
  try {
    await updateManagedRetailPrice(item.id, { unit_price: value })
    await loadPrices()
    showNotice(`${countryName(item.country_code)}（${item.country_code}）· ${item.billing_days} 天售价已更新`)
  } catch (requestError) {
    error.value = requestError.userMessage || '保存零售价失败'
    await loadPrices()
  } finally {
    busy.value = false
  }
}

function previewBulk(type) {
  error.value = ''
  if (type === 'products') {
    if (!selectedProducts.value.size) return
    if (productBulkPrice.value !== '' && (!Number.isFinite(Number(productBulkPrice.value)) || Number(productBulkPrice.value) <= 0)) {
      error.value = '批量售价必须大于 0'
      return
    }
    if (productBulkPrice.value === '' && productBulkStock.value === 'unchanged') {
      error.value = '请填写统一售价或选择售卖状态'
      return
    }
  } else {
    if (!selectedPrices.value.size) return
    if (!Number.isFinite(Number(retailBulkPrice.value)) || Number(retailBulkPrice.value) <= 0) {
      error.value = '批量零售价必须大于 0'
      return
    }
  }
  pendingBulk.value = type
}

async function applyBulk() {
  if (busy.value || !pendingBulk.value) return
  busy.value = true
  error.value = ''
  try {
    if (pendingBulk.value === 'products') {
      const payload = { product_ids: [...selectedProducts.value] }
      if (productBulkPrice.value !== '') payload.price = Number(productBulkPrice.value)
      if (productBulkStock.value !== 'unchanged') payload.is_in_stock = productBulkStock.value === 'in_stock'
      const { data } = await bulkUpdateManagedCatalogProducts(payload)
      selectedProducts.value = new Set()
      productBulkPrice.value = ''
      productBulkStock.value = 'unchanged'
      pendingBulk.value = ''
      await loadProducts()
      showNotice(`已更新 ${data.updated_count} 个商品`)
    } else {
      const { data } = await bulkUpdateManagedRetailPrices({ price_rule_ids: [...selectedPrices.value], unit_price: Number(retailBulkPrice.value) })
      selectedPrices.value = new Set()
      retailBulkPrice.value = ''
      pendingBulk.value = ''
      await loadPrices()
      showNotice(`已更新 ${data.updated_count} 条国家周期售价`)
    }
  } catch (requestError) {
    error.value = requestError.userMessage || '批量修改失败，未完成本次操作'
  } finally {
    busy.value = false
  }
}

function applyProductFilters() {
  productPage.value = 1
  selectedProducts.value = new Set()
  loadProducts()
}

function applyPriceFilters() {
  pricePage.value = 1
  selectedPrices.value = new Set()
  loadPrices()
}

function resetPriceFilters() {
  priceFilter.resource_category = ''
  priceFilter.country_code = ''
  priceFilter.billing_days = ''
  applyPriceFilters()
}

function toggleAllProducts(checked) {
  const next = new Set(selectedProducts.value)
  for (const item of products.value) {
    if (checked) next.add(item.id)
    else next.delete(item.id)
  }
  selectedProducts.value = next
}

function toggleAllPrices(checked) {
  const next = new Set(selectedPrices.value)
  for (const item of prices.value) {
    if (checked) next.add(item.id)
    else next.delete(item.id)
  }
  selectedPrices.value = next
}

function toggleProductSelection(id, checked) {
  const next = new Set(selectedProducts.value)
  if (checked) next.add(id)
  else next.delete(id)
  selectedProducts.value = next
}

function togglePriceSelection(id, checked) {
  const next = new Set(selectedPrices.value)
  if (checked) next.add(id)
  else next.delete(id)
  selectedPrices.value = next
}

onMounted(loadProducts)
</script>

<template>
  <main class="catalog-admin">
    <header class="catalog-header"><div><h1>商品与价格</h1><p>维护官网商品售价、售卖状态及资源池国家周期零售价</p></div><button type="button" class="icon-button" aria-label="刷新当前列表" title="刷新" :disabled="loading" @click="loadCurrent"><RefreshCw :size="16" :class="{ spinning: loading }" /></button></header>
    <div v-if="notice" class="catalog-notice" role="status"><Check :size="15" />{{ notice }}</div>
    <div v-if="error" class="catalog-error" role="alert"><CircleAlert :size="15" /><span>{{ error }}</span><button type="button" aria-label="关闭提示" @click="error = ''"><X :size="15" /></button></div>

    <nav class="catalog-tabs" aria-label="价格管理类别"><button type="button" :class="{ active: tab === 'products' }" @click="tab = 'products'; loadProducts()"><PackageCheck :size="15" />官网商品</button><button type="button" :class="{ active: tab === 'prices' }" @click="tab = 'prices'; loadPrices()"><Tags :size="15" />国家周期售价 <span>{{ priceTotal }}</span></button><button type="button" :class="{ active: tab === 'listings' }" @click="tab = 'listings'; loadListings()"><Database :size="15" />官网可见性</button><button type="button" :class="{ active: tab === 'cities' }" @click="tab = 'cities'; loadCities()"><MapPin :size="15" />城市与号段 <span>{{ cities.length }}</span></button></nav>

    <template v-if="tab === 'products'">
      <form class="catalog-filters" @submit.prevent="applyProductFilters"><label class="search-field"><Search :size="15" /><input v-model="productFilter.keyword" type="search" placeholder="搜索商品、类型或地区" /></label><AppSelect v-model="productFilter.stock" :options="stockFilterOptions" aria-label="按售卖状态筛选" /><button class="secondary-action" type="submit">筛选</button></form>
      <section v-if="selectedProducts.size" class="bulk-editor">
        <div class="bulk-head"><b>已选 {{ selectedProducts.size }} 个商品</b><small>跨页选择</small><button type="button" class="text-action" @click="selectedProducts = new Set()">清除选择</button></div>
        <div class="bulk-fields"><label><span>统一设置售价（元）</span><input v-model="productBulkPrice" type="number" min="0.01" max="99999999" step="0.01" placeholder="留空表示不修改" /></label><label><span>手动售卖状态</span><AppSelect v-model="productBulkStock" :options="bulkStockOptions" aria-label="批量设置售卖状态" /></label><button class="primary-action" type="button" @click="previewBulk('products')"><Save :size="14" />预览批量修改</button></div>
        <div v-if="pendingBulk === 'products'" class="bulk-confirm"><span>{{ productBulkSummary }}</span><button type="button" class="secondary-action" @click="pendingBulk = ''">返回</button><button type="button" class="primary-action" :disabled="busy" @click="applyBulk">{{ busy ? '提交中…' : '确认修改' }}</button></div>
      </section>
      <section class="catalog-table-section">
        <div v-if="loading" class="table-state"><RefreshCw :size="17" class="spinning" />正在加载商品</div>
        <div v-else-if="!products.length" class="table-state"><Database :size="22" />没有匹配的商品</div>
        <div v-else class="table-scroll"><table class="catalog-table"><thead><tr><th class="select-column"><input type="checkbox" aria-label="选择当前页全部商品" :checked="products.length > 0 && products.every((item) => selectedProducts.has(item.id))" @change="toggleAllProducts($event.target.checked)" /></th><th>商品</th><th>地区</th><th>计费周期</th><th>官网售价</th><th>手动售卖状态</th><th>官网上架</th></tr></thead><tbody><tr v-for="item in products" :key="item.id"><td class="select-column"><input type="checkbox" :aria-label="`选择 ${item.name}`" :checked="selectedProducts.has(item.id)" @change="toggleProductSelection(item.id, $event.target.checked)" /></td><td><b>{{ item.name }}</b><small>{{ item.product_type }} · #{{ item.id }}</small></td><td>{{ item.region_name || '—' }}</td><td>{{ item.billing_days }} 天</td><td><PriceCell :value="item.price" :locked="item.price_locked" :busy="busy" :aria-label="`${item.name} 售价`" lock-hint="价格由零售价目表的月价决定，请在「国家周期售价」页修改" @save="saveProductPrice(item, $event)" /><small v-if="item.price_locked">随月价联动</small></td><td><AppSelect compact :model-value="item.is_in_stock" :options="productStockOptions" :disabled="busy" :aria-label="`${item.name} 售卖状态`" @update:model-value="saveProductStock(item, $event)" /></td><td><button class="product-active" :class="{ off: !item.is_active }" type="button" :disabled="busy" :title="item.is_active ? '点击下架（官网不再展示）' : '点击上架'" @click="toggleProductActive(item)">{{ item.is_active ? '上架' : '下架' }}</button></td></tr></tbody></table></div>
        <footer v-if="productTotal" class="table-footer"><span>{{ productTotal }} 个商品 · 第 {{ productPage }} / {{ productPageCount }} 页</span><div><button type="button" aria-label="上一页" :disabled="productPage <= 1 || loading" @click="productPage--; loadProducts()"><ChevronLeft :size="16" /></button><button type="button" aria-label="下一页" :disabled="productPage >= productPageCount || loading" @click="productPage++; loadProducts()"><ChevronRight :size="16" /></button></div></footer>
      </section>
    </template>

    <template v-else-if="tab === 'prices'">
      <form class="catalog-filters retail-filters" @submit.prevent="applyPriceFilters">
        <label><span>资源类型</span><AppSelect v-model="priceFilter.resource_category" :options="categoryOptions" aria-label="按资源类型筛选" /></label>
        <label><span>国家/地区</span><AppSelect v-model="priceFilter.country_code" :options="countryFilterOptions" aria-label="按国家筛选" /></label>
        <label><span>计费周期</span><AppSelect v-model="priceFilter.billing_days" :options="billingDayOptions" aria-label="按计费周期筛选" /></label>
        <button class="secondary-action" type="submit" :disabled="loading">筛选</button>
        <button class="text-action" type="button" :disabled="loading" @click="resetPriceFilters">重置</button>
      </form>
      <section v-if="selectedPrices.size" class="bulk-editor"><div class="bulk-head"><b>已选 {{ selectedPrices.size }} 条价格规则</b><small>跨页选择</small><button type="button" class="text-action" @click="selectedPrices = new Set()">清除选择</button></div><div class="bulk-fields retail-bulk"><label><span>统一零售价（元 / 所选周期）</span><input v-model="retailBulkPrice" type="number" min="0.01" max="99999999" step="0.01" placeholder="输入统一售价" /></label><button class="primary-action" type="button" @click="previewBulk('prices')"><Save :size="14" />预览批量修改</button></div><div v-if="pendingBulk === 'prices'" class="bulk-confirm"><span>将 {{ selectedPrices.size }} 条规则的售价统一调整为 {{ priceLabel(retailBulkPrice) }} / 所选周期</span><button type="button" class="secondary-action" @click="pendingBulk = ''">返回</button><button type="button" class="primary-action" :disabled="busy" @click="applyBulk">{{ busy ? '提交中…' : '确认修改' }}</button></div></section>
      <section class="catalog-table-section">
        <div v-if="loading" class="table-state"><RefreshCw :size="17" class="spinning" />正在加载零售价</div><div v-else-if="!prices.length" class="table-state"><Tags :size="22" />没有匹配的价格规则</div>
        <div v-else class="table-scroll"><table class="catalog-table retail-table"><thead><tr><th class="select-column"><input type="checkbox" aria-label="选择当前页全部零售价" :checked="prices.length > 0 && prices.every((item) => selectedPrices.has(item.id))" @change="toggleAllPrices($event.target.checked)" /></th><th>资源类型</th><th>国家名称</th><th>国家代码</th><th>计费周期</th><th>零售价</th><th>来源版本</th><th>更新时间</th></tr></thead><tbody><tr v-for="item in prices" :key="item.id"><td class="select-column"><input type="checkbox" :aria-label="`选择 ${item.category_label} ${countryName(item.country_code)} ${item.country_code} ${item.billing_days} 天`" :checked="selectedPrices.has(item.id)" @change="togglePriceSelection(item.id, $event.target.checked)" /></td><td>{{ item.category_label }}</td><td>{{ item.country_name || countryName(item.country_code) }}</td><td><b class="country-code">{{ item.country_code }}</b></td><td>{{ item.billing_days }} 天</td><td><PriceCell :value="item.unit_price" :locked="item.billing_days !== 30" :busy="busy" :aria-label="`${countryName(item.country_code)} ${item.country_code} ${item.billing_days} 天售价`" lock-hint="长周期价由月价自动计算（3/6/12 倍），改 30 天档即可" @save="saveRetailPrice(item, $event)" /><small v-if="item.billing_days !== 30">自动计算</small></td><td>{{ item.source_version }}</td><td>{{ dateLabel(item.updated_at) }}</td></tr></tbody></table></div>
        <footer v-if="priceTotal" class="table-footer"><span>{{ priceTotal }} 条价格规则 · 第 {{ pricePage }} / {{ pricePageCount }} 页</span><div><button type="button" aria-label="上一页" :disabled="pricePage <= 1 || loading" @click="pricePage--; loadPrices()"><ChevronLeft :size="16" /></button><button type="button" aria-label="下一页" :disabled="pricePage >= pricePageCount || loading" @click="pricePage++; loadPrices()"><ChevronRight :size="16" /></button></div></footer>
      </section>
    </template>

    <template v-else-if="tab === 'cities'">
      <form class="catalog-filters" @submit.prevent="applyCityFilters">
        <label><span>资源类型</span><AppSelect v-model="cityFilter.resource_category" :options="cityCategoryOptions" aria-label="按资源类型筛选" /></label>
        <label><span>国家/地区</span><AppSelect v-model="cityFilter.country_code" :options="cityCountryOptions" aria-label="按国家筛选" /></label>
        <label class="search-field"><Search :size="15" /><input v-model="cityFilter.keyword" type="search" placeholder="搜索城市码或城市名" /></label>
        <button class="secondary-action" type="submit" :disabled="loading">筛选</button>
        <button class="secondary-action" type="button" :disabled="citySyncBusy" @click="syncCitySegments">
          <RefreshCw :size="14" :class="{ spinning: citySyncBusy }" />{{ citySyncBusy ? '同步中…' : '从资源池1同步城市/号段' }}
        </button>
      </form>

      <section class="city-create">
        <div class="bulk-head"><b>新增可售城市</b><small>客户只能选这里启用的城市；IP 段可留空，留空表示该城市不允许指定号段</small></div>
        <div class="city-create-grid">
          <label><span>资源类型</span><AppSelect v-model="cityForm.resource_category" :options="cityFormCategoryOptions" aria-label="新城市所属资源类型" /></label>
          <label><span>国家/地区</span><AppSelect v-model="cityForm.country_code" :options="cityFormCountryOptions" aria-label="新城市所属国家" /></label>
          <label><span>城市码</span><input v-model="cityForm.city_code" maxlength="32" placeholder="如 LAX" /></label>
          <label><span>城市名</span><input v-model="cityForm.city_name" maxlength="80" placeholder="如 洛杉矶" /></label>
          <label class="wide"><span>可售 IP 段（逗号分隔，可留空）</span><input v-model="cityForm.ip_segments" maxlength="500" placeholder="如 1.1.1,1.1.2" /></label>
          <label><span>排序</span><input v-model.number="cityForm.sort_order" type="number" min="0" max="9999" /></label>
          <button class="primary-action" type="button" :disabled="cityBusy" @click="addCity"><Plus :size="14" />新增城市</button>
        </div>
      </section>

      <div class="listing-summary">
        <span>共 <b>{{ cities.length }}</b> 个城市</span>
        <span>已启用 <b>{{ cities.filter((item) => item.is_enabled).length }}</b></span>
        <span>可指定号段 <b>{{ cities.filter((item) => (item.segments || []).length).length }}</b></span>
        <span class="listing-hint">客户不选城市时默认自动分配；选了就按所选交付，不做静默替换</span>
      </div>

      <section class="catalog-table-section">
        <div v-if="loading" class="table-state"><RefreshCw :size="17" class="spinning" />正在加载城市</div>
        <div v-else-if="!cities.length" class="table-state"><MapPin :size="22" />还没有配置城市，客户下单时将自动分配</div>
        <div v-else class="table-scroll"><table class="catalog-table city-table">
          <thead><tr><th>国家/地区</th><th>资源类型</th><th>城市码</th><th>城市名</th><th>可售 IP 段</th><th>状态</th><th>操作</th></tr></thead>
          <tbody><tr v-for="item in cities" :key="item.id">
            <td><b>{{ item.country_name }}</b><small>{{ item.country_code }}</small></td>
            <td>{{ item.category_label }}</td>
            <td><b class="country-code">{{ item.city_code }}</b></td>
            <td><input class="city-inline" :value="item.city_name" maxlength="80" :aria-label="`${item.city_code} 城市名`" @change="saveCityName(item, $event)" /></td>
            <td><input class="city-inline wide" :value="(item.segments || []).join(',')" maxlength="500" placeholder="留空表示不可指定号段" :aria-label="`${item.city_code} 可售 IP 段`" @change="saveCitySegments(item, $event)" /></td>
            <td><button class="product-active" :class="{ off: !item.is_enabled }" type="button" :disabled="cityBusy" :title="item.is_enabled ? '点击停用（客户不再可选）' : '点击启用'" @click="patchCity(item, { is_enabled: !item.is_enabled }, `${item.city_code} 已${item.is_enabled ? '停用' : '启用'}`)">{{ item.is_enabled ? '已启用' : '已停用' }}</button></td>
            <td><button class="city-delete" type="button" :disabled="cityBusy" aria-label="删除城市" @click="removeCity(item)"><Trash2 :size="13" /></button></td>
          </tr></tbody>
        </table></div>
      </section>
    </template>

    <template v-else>
      <div class="listing-summary">
        <span>当前范围 <b>{{ listings.scope?.pool_name || '平台级（全池）' }}</b></span>
        <span>共 <b>{{ listings.summary.total || 0 }}</b> 个组合</span>
        <span>在售 <b>{{ listings.summary.listed || 0 }}</b></span>
        <span>已下架 <b>{{ listings.summary.unlisted || 0 }}</b></span>
        <span>缺报价 <b>{{ listings.summary.missing_price || 0 }}</b></span>
        <span v-if="listings.summary.manual">已设置 <b>{{ listings.summary.manual }}</b></span>
        <span class="listing-hint">默认全部下架，点格子才上架；「平台级」改动对所有资源池生效，选具体池只改该池</span>
      </div>
      <form class="catalog-filters" @submit.prevent>
        <label><span>生效范围</span><AppSelect v-model="listingPool" :options="listingPoolOptions" aria-label="选择资源池" @update:model-value="loadListings" /></label>
        <label><span>筛选</span><AppSelect v-model="listingFilter" :options="listingFilterOptions" aria-label="筛选国家" /></label>
      </form>
      <section class="catalog-table-section">
        <div v-if="loading" class="table-state"><RefreshCw :size="17" class="spinning" />正在加载可见性</div>
        <div v-else-if="!listingRows.length" class="table-state"><Database :size="22" />没有匹配的国家</div>
        <div v-else class="table-scroll">
          <table class="catalog-table listing-table">
            <thead>
              <tr>
                <th>国家/地区</th>
                <th v-for="line in listings.lines" :key="line.code">
                  <div class="listing-head">
                    <span>{{ line.name }}</span>
                    <em>
                      <button type="button" :disabled="listingBusy" @click="bulkToggleLine(line, 'listed')">全上架</button>
                      <button type="button" :disabled="listingBusy" @click="bulkToggleLine(line, 'unlisted')">全下架</button>
                    </em>
                  </div>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in listingRows" :key="row.id">
                <td><b>{{ row.name }}</b><small>{{ row.code }}</small></td>
                <td v-for="line in listings.lines" :key="line.code">
                  <button
                    type="button"
                    class="listing-cell"
                    :class="!row.cells[line.code] ? 'off' : !row.cells[line.code].visible ? 'off' : (row.cells[line.code].has_price ? 'on' : 'no-price')"
                    :disabled="listingBusy"
                    :title="row.cells[line.code]?.source === 'pool' ? '该资源池单独设置' : row.cells[line.code]?.source === 'global' ? '平台级设置（全池生效）' : '未上架，点一下上架'"
                    @click="toggleListing(row.cells[line.code])"
                  >
                    <template v-if="row.cells[line.code]">
                      {{ row.cells[line.code].visible ? (row.cells[line.code].has_price ? '在售' : '无报价') : '已下架' }}
                      <i v-if="row.cells[line.code].source === 'pool'">·池</i>
                    </template>
                    <template v-else>—</template>
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
  </main>
</template>

<style scoped>
.catalog-admin{min-height:100vh;padding:30px clamp(16px,3vw,40px) 42px;background:#f2f5f3;color:#263b33}.catalog-header{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:18px}.catalog-header h1{margin:0;font-size:23px;line-height:1.25}.catalog-header p{margin:6px 0 0;color:#71827a;font-size:11px}.icon-button{width:36px;height:36px;display:grid;place-items:center;border:1px solid #d1ddd6;border-radius:5px;background:#fff;color:#47675a}.icon-button:disabled{opacity:.45}.catalog-notice,.catalog-error{display:flex;align-items:center;gap:8px;margin-bottom:12px;padding:10px 12px;border:1px solid #bfdeca;border-radius:5px;background:#eef8f1;color:#2d6e4d;font-size:11px}.catalog-error{border-color:#e5c2bb;background:#fff2ef;color:#984a3d}.catalog-error span{flex:1}.catalog-tabs{display:flex;gap:5px;margin-bottom:14px;border-bottom:1px solid #d8e2dc}.catalog-tabs button{min-height:41px;display:flex;align-items:center;gap:7px;padding:0 12px;border-bottom:2px solid transparent;color:#6a7d74;font-size:11px}.catalog-tabs button.active{border-color:#2c8167;color:#1f6b53;font-weight:700}.catalog-tabs button span{color:#7b8b83;font-size:9px}.catalog-filters{display:flex;align-items:end;gap:9px;margin-bottom:12px;padding:12px;background:#fff;border:1px solid #dce5e0;border-radius:6px}.catalog-filters>label{display:grid;gap:5px;color:#62776d;font-size:10px}.catalog-filters select,.catalog-filters input{height:35px;min-width:150px;padding:0 9px;border:1px solid #cfdad4;border-radius:4px;background:#fff;color:#334a40;font:inherit;font-size:10px}.search-field{flex:1;display:flex!important;align-items:center;gap:7px;padding:0 9px;border:1px solid #cfdad4;border-radius:4px;color:#7b8c84}.search-field input{width:100%;min-width:0;border:0;outline:0}.search-field:focus-within{outline:2px solid #7bb49c;outline-offset:1px}.secondary-action,.primary-action,.save-row,.text-action{min-height:34px;display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:0 10px;border:1px solid #cfdbd5;border-radius:4px;background:#fff;color:#476459;font-size:10px;font-weight:650;white-space:nowrap}.secondary-action:hover,.save-row:hover{background:#f1f7f3;border-color:#a8c7b8}.primary-action{border-color:#26785f;background:#26785f;color:#fff}.primary-action:hover{background:#1e694f}.primary-action:disabled,.save-row:disabled{opacity:.45;cursor:not-allowed}.text-action{min-height:auto;padding:3px;border:0;color:#527467}.bulk-editor{margin-bottom:12px;padding:13px 14px;background:#fff;border:1px solid #d9e4de;border-radius:6px}.bulk-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px}.bulk-head b{font-size:11px}.bulk-fields{display:flex;align-items:end;gap:10px}.bulk-fields label{display:grid;gap:5px;min-width:190px;color:#657971;font-size:9px}.bulk-fields input,.bulk-fields select{height:34px;padding:0 9px;border:1px solid #cedbd4;border-radius:4px;background:#fff;color:#30483d;font-size:10px}.retail-bulk label{min-width:240px}.bulk-confirm{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-top:11px;padding-top:11px;border-top:1px solid #e6ece8}.bulk-confirm span{flex:1 1 280px;color:#405a4e;font-size:10px}.catalog-table-section{overflow:hidden;background:#fff;border:1px solid #dce5e0;border-radius:6px}.table-scroll{width:100%;overflow:auto}.catalog-table{width:100%;min-width:830px;border-collapse:collapse;text-align:left}.retail-table{min-width:1050px}.catalog-table th{height:40px;padding:0 11px;background:#f6f8f7;border-bottom:1px solid #e3eae6;color:#6f8178;font-size:9px;font-weight:650;white-space:nowrap}.catalog-table td{height:58px;padding:8px 11px;border-bottom:1px solid #edf1ef;color:#485e53;font-size:10px;white-space:nowrap}.catalog-table tr:last-child td{border-bottom:0}.catalog-table tbody tr:hover{background:#fafcfb}.catalog-table td:nth-child(2){min-width:170px}.catalog-table td:nth-child(2) b{display:block;color:#2d4439;font-size:11px}.catalog-table td:nth-child(2) small{display:block;margin-top:3px;color:#829188;font-size:9px}.catalog-table input[type="checkbox"]{width:15px;height:15px;accent-color:#287a61}.select-column{width:36px;text-align:center}.catalog-table select{height:32px;padding:0 7px;border:1px solid #d1ddd6;border-radius:4px;background:#fff;color:#40594d;font-size:10px}.price-input{width:112px;height:32px;display:flex;align-items:center;gap:4px;padding:0 7px;border:1px solid #cfdcd5;border-radius:4px;background:#fff;color:#73867c}.price-input:focus-within{border-color:#448d70;box-shadow:0 0 0 2px #448d701c}.price-input input{width:100%;min-width:0;border:0;outline:0;color:#2f4b3c;font-size:11px;font-variant-numeric:tabular-nums}.save-row{min-height:30px;padding:0 8px}.country-code{display:inline-grid;place-items:center;min-width:33px;padding:4px;border-radius:3px;background:#eff5f1;color:#536c60;font-size:9px}.table-state{min-height:200px;display:flex;align-items:center;justify-content:center;gap:8px;color:#7d8c84;font-size:11px}.table-footer{min-height:44px;display:flex;align-items:center;justify-content:space-between;padding:6px 11px;border-top:1px solid #e6ece9;color:#788981;font-size:9px}.table-footer>div{display:flex;gap:5px}.table-footer button{width:28px;height:28px;display:grid;place-items:center;border:1px solid #d3ded8;border-radius:4px;background:#fff;color:#4e685d}.table-footer button:disabled{opacity:.35;cursor:not-allowed}.spinning{animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}.catalog-admin :focus-visible{outline:2px solid #39866a;outline-offset:2px}
.product-active{min-height:28px;padding:0 9px;border:1px solid #a6d5b6;border-radius:4px;background:#eaf7ef;color:#1f6b53;font-size:10px;font-weight:650;cursor:pointer}
.product-active.off{border-color:#dfe6e2;background:#f4f6f5;color:#8a9a92}
.product-active:hover:not(:disabled){filter:brightness(.975)}
.product-active:disabled{opacity:.5;cursor:progress}
.listing-summary{display:flex;flex-wrap:wrap;gap:14px;align-items:center;margin-bottom:12px;padding:10px 12px;background:#fff;border:1px solid #dce5e0;border-radius:6px;color:#5f776d;font-size:10px}
.listing-summary b{color:#1f6b53;font-size:12px}
.listing-hint{color:#8a9a92;margin-left:auto}
.listing-head{display:flex;flex-direction:column;gap:5px;align-items:flex-start}
.listing-head em{display:flex;gap:4px;font-style:normal}
.listing-head button{padding:2px 6px;border:1px solid #d3ded8;border-radius:3px;background:#fff;color:#4e685d;font-size:9px;cursor:pointer}
.listing-head button:hover:not(:disabled){background:#f1f7f3;border-color:#a8c7b8}
.listing-head button:disabled{opacity:.5;cursor:progress}
.listing-cell{min-width:92px;height:28px;display:inline-flex;align-items:center;justify-content:center;gap:4px;border:1px solid #cfdbd5;border-radius:4px;font-size:10px;font-weight:650;cursor:pointer;transition:background .12s}
.listing-cell.on{background:#eaf7ef;border-color:#a6d5b6;color:#1f6b53}
.listing-cell.no-price{background:#fff8e8;border-color:#e4d0a2;color:#8a6a22}
.listing-cell.off{background:#f4f6f5;border-color:#dfe6e2;color:#8a9a92}
.listing-cell:hover:not(:disabled){filter:brightness(.975)}
.listing-cell i{font-style:normal;color:#8a9a92;font-size:9px}
.listing-cell:disabled{opacity:.6;cursor:progress}
.listing-table td:first-child b{display:block;font-size:11px;color:#2d4439}
.listing-table td:first-child small{display:block;color:#829188;font-size:9px;margin-top:2px}
@media(max-width:760px){.catalog-admin{padding:68px 13px 26px}.catalog-filters{align-items:stretch;flex-wrap:wrap}.catalog-filters .search-field{flex-basis:100%}.catalog-filters>label{flex:1}.catalog-filters select,.catalog-filters input{min-width:0;width:100%}.bulk-fields{align-items:stretch;flex-direction:column}.bulk-fields label,.retail-bulk label{min-width:0}.bulk-fields .primary-action{align-self:flex-start}.catalog-header h1{font-size:20px}}
@media(max-width:420px){.catalog-tabs button{padding:0 8px;font-size:10px}.catalog-filters .secondary-action{flex:1}.bulk-confirm{align-items:stretch}.bulk-confirm span{flex-basis:100%}.bulk-confirm button{flex:1}}
@media(prefers-reduced-motion:reduce){.spinning{animation:none}}
.city-create{margin-bottom:12px;padding:12px;background:#fff;border:1px solid #dce5e0;border-radius:6px}
.city-create .bulk-head{margin-bottom:2px}
.city-create-grid{display:flex;flex-wrap:wrap;align-items:end;gap:9px;margin-top:7px}
.city-create-grid>label{display:grid;gap:5px;color:#62776d;font-size:10px;min-width:140px}
.city-create-grid>label.wide{flex:1;min-width:220px}
.city-create-grid input{height:35px;padding:0 9px;border:1px solid #cfdad4;border-radius:4px;background:#fff;color:#334a40;font:inherit;font-size:10px}
.city-table .city-inline{width:100%;min-width:110px;height:30px;padding:0 7px;border:1px solid #dbe4df;border-radius:4px;background:#fff;color:#2d4439;font:inherit;font-size:11px}
.city-table .city-inline.wide{min-width:180px}
.city-table .city-inline:focus{outline:2px solid #7bb49c;outline-offset:1px}
.city-delete{width:28px;height:28px;display:grid;place-items:center;border:1px solid #e0d6d3;border-radius:4px;background:#fff;color:#984a3d;cursor:pointer}
.city-delete:hover:not(:disabled){background:#fff2ef}
.city-delete:disabled{opacity:.5;cursor:progress}
@media(max-width:760px){.city-create-grid>label{flex:1;min-width:0}.city-create-grid>label.wide{flex-basis:100%}}
</style>
