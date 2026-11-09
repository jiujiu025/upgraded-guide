<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { ArrowRight, Check, Minus, Plus, RefreshCw, Search } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import { getPublicProducts, getPublicRegions, previewOrder } from '../api'
import { session } from '../api/session'
import PageIntro from './PageIntro.vue'

const router = useRouter()
const route = useRoute()
const products = ref([])
const regions = ref([])
const query = ref('')
const selectedRegionId = ref(null)
const selectedProductId = ref(null)
const quantity = ref(1)
const billingDays = ref(30)
const preview = ref(null)
// 价格登录可见：未登录不调算价接口（后端也会 401），只在汇总里给登录入口。
const signedIn = computed(() => Boolean(session.user))
const loading = ref(true)
const previewing = ref(false)
const error = ref('')
let previewTimer

const selectedRegion = computed(() => regions.value.find((item) => item.id === selectedRegionId.value))
const availableProducts = computed(() => products.value.filter((item) => item.region_id === selectedRegionId.value))
const selectedProduct = computed(() => availableProducts.value.find((item) => item.id === selectedProductId.value))
const regionMatches = computed(() => regions.value.filter((item) => item.name.includes(query.value.trim())))
const periods = computed(() => {
  // 周期与价格全部由后端下发（线性定价：90天=3×、180天=6×、360天=12×），前端不再自己乘系数
  const fromBackend = selectedProduct.value?.periods ?? []
  if (fromBackend.length) return fromBackend.map((item) => item.billing_days)
  return selectedProduct.value ? [selectedProduct.value.billing_days] : []
})
const priceOf = (days) => selectedProduct.value?.periods?.find((item) => item.billing_days === days)?.unit_price ?? null
const productLabel = (product) => {
  if (!product.is_in_stock) return '暂时无货'
  // 未登录不显示价格（后端 catalog 已脱敏，这里不再引用商品价格字段）
  if (!signedIn.value) return '登录后查看价格'
  return (product.periods?.length ?? 0) > 1
    ? `¥${product.price} 起`
    : `¥${product.price} / ${product.billing_days} 天`
}

function selectRegion(id) {
  selectedRegionId.value = id
  const first = products.value.find((item) => item.region_id === id && item.is_in_stock)
  selectedProductId.value = first?.id ?? products.value.find((item) => item.region_id === id)?.id ?? null
}

// 上次选择存在 sessionStorage 里，可能被其他版本写坏或被手动改过。
// 解析失败就当没有选择，不能让整个产品列表跟着挂掉（以前会报成"网络错误"）。
function readSavedSelection() {
  try {
    const raw = sessionStorage.getItem('checkout_selection')
    return raw ? JSON.parse(raw) : null
  } catch {
    sessionStorage.removeItem('checkout_selection')
    return null
  }
}

async function loadCatalog() {
  loading.value = true
  error.value = ''
  try {
    const [productResponse, regionResponse] = await Promise.all([getPublicProducts(), getPublicRegions()])
    products.value = productResponse.data
    regions.value = regionResponse.data
    const saved = readSavedSelection()
    // 详情页「开始选购」带着 ?product=<id> 过来，优先级最高：直接选中该 SKU 与它的地区。
    const queryProduct = products.value.find((item) => String(item.id) === String(route.query.product || ''))
    const queryRegion = regions.value.find((item) => item.name === route.query.region)
    const initialRegion = (queryProduct && regions.value.find((item) => item.id === queryProduct.region_id))
      || regions.value.find((item) => item.id === saved?.region_id)
      || queryRegion
      || regions.value[0]
    selectRegion(initialRegion?.id ?? null)
    const savedProduct = availableProducts.value.find((item) => item.id === saved?.product_id)
    if (queryProduct) selectedProductId.value = queryProduct.id
    else if (savedProduct) selectedProductId.value = savedProduct.id
    quantity.value = Math.min(100, Math.max(1, Number(saved?.quantity) || 1))
    billingDays.value = Number(saved?.billing_days) || selectedProduct.value?.billing_days || 30
  } catch (requestError) { error.value = requestError.userMessage } finally { loading.value = false }
}

async function refreshPreview() {
  if (!signedIn.value) { preview.value = null; previewing.value = false; return }
  if (!selectedProduct.value || !selectedRegion.value) { preview.value = null; return }
  previewing.value = true
  error.value = ''
  try {
    const payload = { product_id: selectedProduct.value.id, region_id: selectedRegion.value.id, quantity: quantity.value, billing_days: billingDays.value }
    const { data } = await previewOrder(payload)
    preview.value = data
    sessionStorage.setItem('checkout_selection', JSON.stringify(payload))
  } catch (requestError) { preview.value = null; error.value = requestError.userMessage } finally { previewing.value = false }
}

watch([selectedProductId, quantity, billingDays], () => {
  clearTimeout(previewTimer)
  if (selectedProduct.value && !periods.value.includes(billingDays.value)) billingDays.value = selectedProduct.value.billing_days
  previewTimer = setTimeout(refreshPreview, 280)
})
function goLogin() { router.push({ path: '/login', query: { redirect: route.fullPath } }) }
onMounted(loadCatalog)
</script>

<template><div class="buy-page"><PageIntro title="选择你的线路" desc="产品、库存和价格均来自当前业务系统，最终金额以后端预览为准。" /><section class="section light buy-layout"><div v-if="loading" class="state-panel">正在读取产品和地区…</div><div v-else-if="error && !regions.length" class="state-panel error-state"><p>{{ error }}</p><button class="outline-action" @click="loadCatalog"><RefreshCw :size="15" />重试</button></div><template v-else><div class="buy-main"><div class="step-rail"><span class="active">01 <b>地区</b></span><i></i><span :class="{ active: selectedProduct }">02 <b>产品</b></span><i></i><span :class="{ active: preview }">03 <b>周期与数量</b></span></div><div class="buy-block"><div class="block-head"><div><p class="eyebrow dark">STEP 01</p><h2>你要连接哪里？</h2></div><label class="search-box"><Search :size="16" /><input v-model="query" placeholder="搜索国家或地区" /></label></div><div v-if="regionMatches.length" class="region-picker"><button v-for="region in regionMatches" :key="region.id" :class="{ selected: selectedRegionId === region.id }" @click="selectRegion(region.id)"><span>{{ region.country_code }}</span>{{ region.name }}<Check v-if="selectedRegionId === region.id" :size="15" /></button></div><p v-else class="inline-empty">没有匹配的地区</p></div><div class="buy-block"><div class="block-head"><div><p class="eyebrow dark">STEP 02</p><h2>选择当前地区的产品</h2></div></div><div v-if="availableProducts.length" class="type-picker"><button v-for="product in availableProducts" :key="product.id" :disabled="!product.is_in_stock" :class="{ selected: selectedProductId === product.id }" @click="selectedProductId = product.id"><span>{{ product.product_type }}</span><b>{{ product.name }}</b><small>{{ productLabel(product) }}</small></button></div><p v-else class="inline-empty">该地区暂无可选产品，请选择其他地区。</p></div><div class="buy-block"><div class="block-head"><div><p class="eyebrow dark">STEP 03</p><h2>确认数量与周期</h2></div></div><div class="order-controls"><label>线路数量 <span class="counter"><button :disabled="quantity <= 1" @click="quantity--"><Minus :size="14" /></button><b>{{ quantity }}</b><button :disabled="quantity >= 100" @click="quantity++"><Plus :size="14" /></button></span></label><label>使用周期 <select v-model="billingDays"><option v-for="days in periods" :key="days" :value="days">{{ days }} 天</option></select></label></div></div></div><aside class="order-summary"><p class="eyebrow dark">ORDER PREVIEW</p><h3>{{ selectedProduct?.name || '请选择产品' }}</h3><div class="summary-line"><span>地区</span><b>{{ selectedRegion?.name || '—' }}</b></div><div class="summary-line"><span>数量</span><b>{{ quantity }} 条线路</b></div><div class="summary-line"><span>周期</span><b>{{ billingDays }} 天</b></div><div class="summary-line"><span>单价</span><b>{{ !signedIn ? '登录可见' : preview ? `¥${preview.unit_price.toFixed(2)}` : '—' }}</b></div><div class="summary-line"><span>折合</span><b>{{ !signedIn ? '登录可见' : preview ? `¥${(preview.unit_price / preview.billing_days).toFixed(2)} / 天` : '—' }}</b></div><div class="summary-total"><span>后端预览总额</span><strong>{{ !signedIn ? '登录后显示' : previewing ? '计算中…' : preview ? `¥${preview.total_amount.toFixed(2)}` : '—' }}</strong></div><p v-if="error" class="summary-error">{{ error }}</p><p class="demo-note">价格由后端实时计算。余额与在线支付尚未接通，提交后仅生成待处理订单。</p><button v-if="signedIn" class="button full-button" :disabled="!preview || previewing" @click="router.push('/checkout')">确认并继续 <ArrowRight :size="16" /></button><button v-else class="button full-button" @click="goLogin">登录后查看价格 <ArrowRight :size="16" /></button></aside></template></section></div></template>
