<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { Check, ChevronLeft, ChevronRight, CircleAlert, Database, PackageCheck, RefreshCw, Save, Search, Tags, X } from 'lucide-vue-next'
import {
  bulkUpdateManagedCatalogProducts, bulkUpdateManagedRetailPrices,
  getManagedCatalogProducts, getManagedRetailPrices,
  updateManagedCatalogProduct, updateManagedRetailPrice,
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
const productDrafts = reactive({})
const selectedProducts = ref(new Set())
const productBulkPrice = ref('')
const productBulkStock = ref('unchanged')
const prices = ref([])
const priceTotal = ref(0)
const pricePage = ref(1)
const priceCategories = ref([])
const priceFilter = reactive({ resource_category: '', country_code: '', billing_days: '' })
const priceDrafts = reactive({})
const selectedPrices = ref(new Set())
const retailBulkPrice = ref('')
const pendingBulk = ref('')
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

const priceLabel = (value) => `¥${Number(value || 0).toFixed(2)}`
const dateLabel = (value) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'

function showNotice(message) {
  notice.value = message
  window.clearTimeout(showNotice.timer)
  showNotice.timer = window.setTimeout(() => { notice.value = '' }, 3200)
}

function setProductDrafts(items) {
  Object.keys(productDrafts).forEach((key) => delete productDrafts[key])
  items.forEach((item) => { productDrafts[item.id] = { price: String(item.price), is_in_stock: item.is_in_stock } })
}

function setPriceDrafts(items) {
  Object.keys(priceDrafts).forEach((key) => delete priceDrafts[key])
  items.forEach((item) => { priceDrafts[item.id] = String(item.unit_price) })
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
    setProductDrafts(data.items)
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
    setPriceDrafts(data.items)
  } catch (requestError) {
    error.value = requestError.userMessage || '国家周期零售价加载失败'
  } finally {
    loading.value = false
  }
}

async function loadCurrent() {
  pendingBulk.value = ''
  if (tab.value === 'products') await loadProducts()
  else await loadPrices()
}

async function saveProduct(item) {
  const draft = productDrafts[item.id]
  if (busy.value || !draft) return
  const payload = {}
  if (Number(draft.price) !== Number(item.price)) payload.price = Number(draft.price)
  if (draft.is_in_stock !== item.is_in_stock) payload.is_in_stock = draft.is_in_stock
  if (!Object.keys(payload).length) return
  busy.value = true
  error.value = ''
  try {
    await updateManagedCatalogProduct(item.id, payload)
    await loadProducts()
    showNotice(`${item.name} 已更新`)
  } catch (requestError) {
    error.value = requestError.userMessage || '保存商品失败'
  } finally {
    busy.value = false
  }
}

async function saveRetailPrice(item) {
  const value = Number(priceDrafts[item.id])
  if (busy.value || !Number.isFinite(value) || value <= 0 || value === Number(item.unit_price)) return
  busy.value = true
  error.value = ''
  try {
    await updateManagedRetailPrice(item.id, { unit_price: value })
    await loadPrices()
    showNotice(`${item.country_code} · ${item.billing_days} 天售价已更新`)
  } catch (requestError) {
    error.value = requestError.userMessage || '保存零售价失败'
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

    <nav class="catalog-tabs" aria-label="价格管理类别"><button type="button" :class="{ active: tab === 'products' }" @click="tab = 'products'; loadProducts()"><PackageCheck :size="15" />官网商品</button><button type="button" :class="{ active: tab === 'prices' }" @click="tab = 'prices'; loadPrices()"><Tags :size="15" />国家周期售价 <span>{{ priceTotal }}</span></button></nav>

    <template v-if="tab === 'products'">
      <form class="catalog-filters" @submit.prevent="applyProductFilters"><label class="search-field"><Search :size="15" /><input v-model="productFilter.keyword" type="search" placeholder="搜索商品、类型或地区" /></label><select v-model="productFilter.stock" aria-label="按售卖状态筛选"><option value="">全部状态</option><option value="in_stock">有货</option><option value="out_of_stock">无货</option></select><button class="secondary-action" type="submit">筛选</button></form>
      <section v-if="selectedProducts.size" class="bulk-editor">
        <div class="bulk-head"><b>已选 {{ selectedProducts.size }} 个商品</b><small>跨页选择</small><button type="button" class="text-action" @click="selectedProducts = new Set()">清除选择</button></div>
        <div class="bulk-fields"><label><span>统一设置售价（元）</span><input v-model="productBulkPrice" type="number" min="0.01" max="99999999" step="0.01" placeholder="留空表示不修改" /></label><label><span>手动售卖状态</span><select v-model="productBulkStock"><option value="unchanged">保持不变</option><option value="in_stock">设为有货</option><option value="out_of_stock">设为无货</option></select></label><button class="primary-action" type="button" @click="previewBulk('products')"><Save :size="14" />预览批量修改</button></div>
        <div v-if="pendingBulk === 'products'" class="bulk-confirm"><span>{{ productBulkSummary }}</span><button type="button" class="secondary-action" @click="pendingBulk = ''">返回</button><button type="button" class="primary-action" :disabled="busy" @click="applyBulk">{{ busy ? '提交中…' : '确认修改' }}</button></div>
      </section>
      <section class="catalog-table-section">
        <div v-if="loading" class="table-state"><RefreshCw :size="17" class="spinning" />正在加载商品</div>
        <div v-else-if="!products.length" class="table-state"><Database :size="22" />没有匹配的商品</div>
        <div v-else class="table-scroll"><table class="catalog-table"><thead><tr><th class="select-column"><input type="checkbox" aria-label="选择当前页全部商品" :checked="products.length > 0 && products.every((item) => selectedProducts.has(item.id))" @change="toggleAllProducts($event.target.checked)" /></th><th>商品</th><th>地区</th><th>计费周期</th><th>官网售价</th><th>手动售卖状态</th><th>操作</th></tr></thead><tbody><tr v-for="item in products" :key="item.id"><td class="select-column"><input type="checkbox" :aria-label="`选择 ${item.name}`" :checked="selectedProducts.has(item.id)" @change="toggleProductSelection(item.id, $event.target.checked)" /></td><td><b>{{ item.name }}</b><small>{{ item.product_type }} · #{{ item.id }}</small></td><td>{{ item.region_name || '—' }}</td><td>{{ item.billing_days }} 天</td><td><label class="price-input"><span>¥</span><input v-model="productDrafts[item.id].price" type="number" min="0.01" max="99999999" step="0.01" :aria-label="`${item.name} 售价`" /></label></td><td><select v-model="productDrafts[item.id].is_in_stock" :aria-label="`${item.name} 售卖状态`"><option :value="true">有货</option><option :value="false">无货</option></select></td><td><button class="save-row" type="button" :disabled="busy || (Number(productDrafts[item.id].price) === Number(item.price) && productDrafts[item.id].is_in_stock === item.is_in_stock)" @click="saveProduct(item)"><Save :size="14" />保存</button></td></tr></tbody></table></div>
        <footer v-if="productTotal" class="table-footer"><span>{{ productTotal }} 个商品 · 第 {{ productPage }} / {{ productPageCount }} 页</span><div><button type="button" aria-label="上一页" :disabled="productPage <= 1 || loading" @click="productPage--; loadProducts()"><ChevronLeft :size="16" /></button><button type="button" aria-label="下一页" :disabled="productPage >= productPageCount || loading" @click="productPage++; loadProducts()"><ChevronRight :size="16" /></button></div></footer>
      </section>
    </template>

    <template v-else>
      <form class="catalog-filters retail-filters" @submit.prevent="applyPriceFilters"><label><span>资源类型</span><select v-model="priceFilter.resource_category"><option value="">全部类型</option><option v-for="item in priceCategories" :key="item.code" :value="item.code">{{ item.label }}</option></select></label><label><span>国家代码</span><input v-model="priceFilter.country_code" maxlength="10" placeholder="如 US" /></label><label><span>计费周期</span><select v-model="priceFilter.billing_days"><option value="">全部周期</option><option value="30">30 天</option><option value="180">180 天</option><option value="360">360 天</option></select></label><button class="secondary-action" type="submit">筛选</button></form>
      <section v-if="selectedPrices.size" class="bulk-editor"><div class="bulk-head"><b>已选 {{ selectedPrices.size }} 条价格规则</b><small>跨页选择</small><button type="button" class="text-action" @click="selectedPrices = new Set()">清除选择</button></div><div class="bulk-fields retail-bulk"><label><span>统一零售价（元 / 所选周期）</span><input v-model="retailBulkPrice" type="number" min="0.01" max="99999999" step="0.01" placeholder="输入统一售价" /></label><button class="primary-action" type="button" @click="previewBulk('prices')"><Save :size="14" />预览批量修改</button></div><div v-if="pendingBulk === 'prices'" class="bulk-confirm"><span>将 {{ selectedPrices.size }} 条规则的售价统一调整为 {{ priceLabel(retailBulkPrice) }} / 所选周期</span><button type="button" class="secondary-action" @click="pendingBulk = ''">返回</button><button type="button" class="primary-action" :disabled="busy" @click="applyBulk">{{ busy ? '提交中…' : '确认修改' }}</button></div></section>
      <section class="catalog-table-section">
        <div v-if="loading" class="table-state"><RefreshCw :size="17" class="spinning" />正在加载零售价</div><div v-else-if="!prices.length" class="table-state"><Tags :size="22" />没有匹配的价格规则</div>
        <div v-else class="table-scroll"><table class="catalog-table retail-table"><thead><tr><th class="select-column"><input type="checkbox" aria-label="选择当前页全部零售价" :checked="prices.length > 0 && prices.every((item) => selectedPrices.has(item.id))" @change="toggleAllPrices($event.target.checked)" /></th><th>资源类型</th><th>国家</th><th>计费周期</th><th>零售价</th><th>来源版本</th><th>更新时间</th><th>操作</th></tr></thead><tbody><tr v-for="item in prices" :key="item.id"><td class="select-column"><input type="checkbox" :aria-label="`选择 ${item.category_label} ${item.country_code} ${item.billing_days} 天`" :checked="selectedPrices.has(item.id)" @change="togglePriceSelection(item.id, $event.target.checked)" /></td><td>{{ item.category_label }}</td><td><b class="country-code">{{ item.country_code }}</b></td><td>{{ item.billing_days }} 天</td><td><label class="price-input"><span>¥</span><input v-model="priceDrafts[item.id]" type="number" min="0.01" max="99999999" step="0.01" :aria-label="`${item.country_code} ${item.billing_days} 天售价`" /></label></td><td>{{ item.source_version }}</td><td>{{ dateLabel(item.updated_at) }}</td><td><button class="save-row" type="button" :disabled="busy || Number(priceDrafts[item.id]) === Number(item.unit_price)" @click="saveRetailPrice(item)"><Save :size="14" />保存</button></td></tr></tbody></table></div>
        <footer v-if="priceTotal" class="table-footer"><span>{{ priceTotal }} 条价格规则 · 第 {{ pricePage }} / {{ pricePageCount }} 页</span><div><button type="button" aria-label="上一页" :disabled="pricePage <= 1 || loading" @click="pricePage--; loadPrices()"><ChevronLeft :size="16" /></button><button type="button" aria-label="下一页" :disabled="pricePage >= pricePageCount || loading" @click="pricePage++; loadPrices()"><ChevronRight :size="16" /></button></div></footer>
      </section>
    </template>
  </main>
</template>

<style scoped>
.catalog-admin{min-height:100vh;padding:30px clamp(16px,3vw,40px) 42px;background:#f2f5f3;color:#263b33}.catalog-header{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:18px}.catalog-header h1{margin:0;font-size:23px;line-height:1.25}.catalog-header p{margin:6px 0 0;color:#71827a;font-size:11px}.icon-button{width:36px;height:36px;display:grid;place-items:center;border:1px solid #d1ddd6;border-radius:5px;background:#fff;color:#47675a}.icon-button:disabled{opacity:.45}.catalog-notice,.catalog-error{display:flex;align-items:center;gap:8px;margin-bottom:12px;padding:10px 12px;border:1px solid #bfdeca;border-radius:5px;background:#eef8f1;color:#2d6e4d;font-size:11px}.catalog-error{border-color:#e5c2bb;background:#fff2ef;color:#984a3d}.catalog-error span{flex:1}.catalog-tabs{display:flex;gap:5px;margin-bottom:14px;border-bottom:1px solid #d8e2dc}.catalog-tabs button{min-height:41px;display:flex;align-items:center;gap:7px;padding:0 12px;border-bottom:2px solid transparent;color:#6a7d74;font-size:11px}.catalog-tabs button.active{border-color:#2c8167;color:#1f6b53;font-weight:700}.catalog-tabs button span{color:#7b8b83;font-size:9px}.catalog-filters{display:flex;align-items:end;gap:9px;margin-bottom:12px;padding:12px;background:#fff;border:1px solid #dce5e0;border-radius:6px}.catalog-filters>label{display:grid;gap:5px;color:#62776d;font-size:10px}.catalog-filters select,.catalog-filters input{height:35px;min-width:150px;padding:0 9px;border:1px solid #cfdad4;border-radius:4px;background:#fff;color:#334a40;font:inherit;font-size:10px}.search-field{flex:1;display:flex!important;align-items:center;gap:7px;padding:0 9px;border:1px solid #cfdad4;border-radius:4px;color:#7b8c84}.search-field input{width:100%;min-width:0;border:0;outline:0}.search-field:focus-within{outline:2px solid #7bb49c;outline-offset:1px}.secondary-action,.primary-action,.save-row,.text-action{min-height:34px;display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:0 10px;border:1px solid #cfdbd5;border-radius:4px;background:#fff;color:#476459;font-size:10px;font-weight:650;white-space:nowrap}.secondary-action:hover,.save-row:hover{background:#f1f7f3;border-color:#a8c7b8}.primary-action{border-color:#26785f;background:#26785f;color:#fff}.primary-action:hover{background:#1e694f}.primary-action:disabled,.save-row:disabled{opacity:.45;cursor:not-allowed}.text-action{min-height:auto;padding:3px;border:0;color:#527467}.bulk-editor{margin-bottom:12px;padding:13px 14px;background:#fff;border:1px solid #d9e4de;border-radius:6px}.bulk-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px}.bulk-head b{font-size:11px}.bulk-fields{display:flex;align-items:end;gap:10px}.bulk-fields label{display:grid;gap:5px;min-width:190px;color:#657971;font-size:9px}.bulk-fields input,.bulk-fields select{height:34px;padding:0 9px;border:1px solid #cedbd4;border-radius:4px;background:#fff;color:#30483d;font-size:10px}.retail-bulk label{min-width:240px}.bulk-confirm{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-top:11px;padding-top:11px;border-top:1px solid #e6ece8}.bulk-confirm span{flex:1 1 280px;color:#405a4e;font-size:10px}.catalog-table-section{overflow:hidden;background:#fff;border:1px solid #dce5e0;border-radius:6px}.table-scroll{width:100%;overflow:auto}.catalog-table{width:100%;min-width:830px;border-collapse:collapse;text-align:left}.retail-table{min-width:1050px}.catalog-table th{height:40px;padding:0 11px;background:#f6f8f7;border-bottom:1px solid #e3eae6;color:#6f8178;font-size:9px;font-weight:650;white-space:nowrap}.catalog-table td{height:58px;padding:8px 11px;border-bottom:1px solid #edf1ef;color:#485e53;font-size:10px;white-space:nowrap}.catalog-table tr:last-child td{border-bottom:0}.catalog-table tbody tr:hover{background:#fafcfb}.catalog-table td:nth-child(2){min-width:170px}.catalog-table td:nth-child(2) b{display:block;color:#2d4439;font-size:11px}.catalog-table td:nth-child(2) small{display:block;margin-top:3px;color:#829188;font-size:9px}.catalog-table input[type="checkbox"]{width:15px;height:15px;accent-color:#287a61}.select-column{width:36px;text-align:center}.catalog-table select{height:32px;padding:0 7px;border:1px solid #d1ddd6;border-radius:4px;background:#fff;color:#40594d;font-size:10px}.price-input{width:112px;height:32px;display:flex;align-items:center;gap:4px;padding:0 7px;border:1px solid #cfdcd5;border-radius:4px;background:#fff;color:#73867c}.price-input:focus-within{border-color:#448d70;box-shadow:0 0 0 2px #448d701c}.price-input input{width:100%;min-width:0;border:0;outline:0;color:#2f4b3c;font-size:11px;font-variant-numeric:tabular-nums}.save-row{min-height:30px;padding:0 8px}.country-code{display:inline-grid;place-items:center;min-width:33px;padding:4px;border-radius:3px;background:#eff5f1;color:#536c60;font-size:9px}.table-state{min-height:200px;display:flex;align-items:center;justify-content:center;gap:8px;color:#7d8c84;font-size:11px}.table-footer{min-height:44px;display:flex;align-items:center;justify-content:space-between;padding:6px 11px;border-top:1px solid #e6ece9;color:#788981;font-size:9px}.table-footer>div{display:flex;gap:5px}.table-footer button{width:28px;height:28px;display:grid;place-items:center;border:1px solid #d3ded8;border-radius:4px;background:#fff;color:#4e685d}.table-footer button:disabled{opacity:.35;cursor:not-allowed}.spinning{animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}.catalog-admin :focus-visible{outline:2px solid #39866a;outline-offset:2px}
@media(max-width:760px){.catalog-admin{padding:68px 13px 26px}.catalog-filters{align-items:stretch;flex-wrap:wrap}.catalog-filters .search-field{flex-basis:100%}.catalog-filters>label{flex:1}.catalog-filters select,.catalog-filters input{min-width:0;width:100%}.bulk-fields{align-items:stretch;flex-direction:column}.bulk-fields label,.retail-bulk label{min-width:0}.bulk-fields .primary-action{align-self:flex-start}.catalog-header h1{font-size:20px}}
@media(max-width:420px){.catalog-tabs button{padding:0 8px;font-size:10px}.catalog-filters .secondary-action{flex:1}.bulk-confirm{align-items:stretch}.bulk-confirm span{flex-basis:100%}.bulk-confirm button{flex:1}}
@media(prefers-reduced-motion:reduce){.spinning{animation:none}}
</style>
