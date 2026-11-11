<script setup>
// 官网 /buy：先选产品（4 个），再选国家，最后确认周期与数量。
// 目录来自 /api/public/catalog：未登录也能选产品与国家，只是金额由后端脱敏。
import { computed, onMounted, ref, watch } from 'vue'
import { ArrowRight, Check, Minus, Plus, RefreshCw, Search } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import { getPublicCatalog, previewOrder } from '../api'
import { buildProductViews, pickDefaultProduct } from '../utils/buyProducts'
import { session } from '../api/session'
import CitySegmentPicker from './CitySegmentPicker.vue'
import PageIntro from './PageIntro.vue'

const router = useRouter()
const route = useRoute()
const catalog = ref({ lines: [] })
const query = ref('')
const selectedProductCode = ref('')
const selectedRegionId = ref(null)
const selectedProductId = ref(null)
const quantity = ref(1)
const billingDays = ref(30)
const cityChoice = ref({ cityCode: '', ipSegment: '' })
const citySupported = ref(false)
const preview = ref(null)
// 价格登录可见：未登录不调算价接口（后端也会 401），只在汇总里给登录入口。
const signedIn = computed(() => Boolean(session.user))
const loading = ref(true)
const previewing = ref(false)
const error = ref('')
let previewTimer

const products = computed(() => buildProductViews(catalog.value.lines || []))
const product = computed(() => products.value.find((item) => item.code === selectedProductCode.value) || null)
const regions = computed(() => product.value?.regions || [])
const regionMatches = computed(() => regions.value.filter((item) => item.name.includes(query.value.trim())))
const selectedRegion = computed(() => regions.value.find((item) => item.id === selectedRegionId.value) || null)
const offers = computed(() => selectedRegion.value?.offers || [])
const selectedOffer = computed(() => offers.value.find((item) => item.product_id === selectedProductId.value) || null)
const periods = computed(() => product.value?.periods || [])

// 产品卡片上的价格：未登录不显示（catalog 已脱敏，min_price 本来就是 null）。
function productPrice(item) {
  if (!item.regionCount) return '暂无可售国家'
  if (item.minPrice === null) return signedIn.value ? '暂无报价' : '登录后查看价格'
  return `¥${item.minPrice} / 30天起`
}

function pickProduct(code) {
  selectedProductCode.value = code
  query.value = ''
  const target = products.value.find((item) => item.code === code)
  const firstRegion = target?.regions.find((item) => item.offers.some((offer) => offer.in_stock))
    || target?.regions?.[0]
  selectRegion(firstRegion?.id ?? null)
  billingDays.value = target?.periods?.[0] || 30
}

function selectRegion(id) {
  selectedRegionId.value = id
  cityChoice.value = { cityCode: '', ipSegment: '' }
  const list = regions.value.find((item) => item.id === id)?.offers || []
  selectedProductId.value = (list.find((item) => item.in_stock) || list[0])?.product_id ?? null
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

// 按 product_id 反查它属于哪个产品与国家：详情页 ?product= 和上次选择都用这一套。
function locateProduct(productId) {
  for (const item of products.value) {
    for (const entry of item.regions) {
      if (entry.offers.some((offer) => offer.product_id === productId)) {
        selectedProductCode.value = item.code
        selectRegion(entry.id)
        selectedProductId.value = productId
        return true
      }
    }
  }
  return false
}

async function loadCatalog() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await getPublicCatalog()
    catalog.value = data
    const saved = readSavedSelection()
    // 详情页「开始选购」带着 ?product=<id> 过来，优先级最高。
    const queryProduct = Number(route.query.product || 0)
    if (queryProduct && locateProduct(queryProduct)) {
      // 已定位
    } else if (saved?.product_id && locateProduct(Number(saved.product_id))) {
      // 用上次的选择
    } else {
      const first = pickDefaultProduct(products.value)
      if (first) pickProduct(first.code)
    }
    quantity.value = Math.min(100, Math.max(1, Number(saved?.quantity) || 1))
    if (periods.value.length && !periods.value.includes(Number(saved?.billing_days))) {
      billingDays.value = periods.value[0]
    } else if (saved?.billing_days) {
      billingDays.value = Number(saved.billing_days)
    }
    if (saved?.city_code || saved?.ip_segment) {
      cityChoice.value = { cityCode: saved?.city_code || '', ipSegment: saved?.ip_segment || '' }
    }
  } catch (requestError) { error.value = requestError.userMessage } finally { loading.value = false }
}

async function refreshPreview() {
  if (!signedIn.value) { preview.value = null; previewing.value = false; return }
  if (!selectedProductId.value || !selectedRegion.value) { preview.value = null; return }
  previewing.value = true
  error.value = ''
  try {
    const payload = {
      product_id: selectedProductId.value,
      region_id: selectedRegion.value.id,
      quantity: quantity.value,
      billing_days: billingDays.value,
      city_code: cityChoice.value.cityCode || null,
      ip_segment: cityChoice.value.ipSegment || null,
    }
    const { data } = await previewOrder(payload)
    preview.value = data
    sessionStorage.setItem('checkout_selection', JSON.stringify(payload))
  } catch (requestError) { preview.value = null; error.value = requestError.userMessage } finally { previewing.value = false }
}

watch([selectedProductId, quantity, billingDays], () => {
  clearTimeout(previewTimer)
  if (selectedProductId.value && periods.value.length && !periods.value.includes(billingDays.value)) {
    billingDays.value = periods.value[0]
  }
  previewTimer = setTimeout(refreshPreview, 280)
})
// 换产品/换国家后旧城市可能不属于新国家：清空，等选择器重新加载。
watch([selectedProductCode, selectedRegionId], () => { cityChoice.value = { cityCode: '', ipSegment: '' } })
watch(cityChoice, () => {
  clearTimeout(previewTimer)
  previewTimer = setTimeout(refreshPreview, 280)
}, { deep: true })
function goLogin() { router.push({ path: '/login', query: { redirect: route.fullPath } }) }
onMounted(loadCatalog)
</script>

<template>
  <div class="buy-page">
    <PageIntro title="选择你的线路" desc="产品、库存和价格均来自当前业务系统，最终金额以后端预览为准。" />
    <section class="section light buy-layout">
      <div v-if="loading" class="state-panel">正在读取产品和地区…</div>
      <div v-else-if="error && !products.length" class="state-panel error-state">
        <p>{{ error }}</p>
        <button class="outline-action" @click="loadCatalog"><RefreshCw :size="15" />重试</button>
      </div>
      <template v-else>
        <div class="buy-main">
          <div class="step-rail">
            <span class="active">01 <b>产品</b></span><i></i>
            <span :class="{ active: selectedRegion }">02 <b>国家</b></span><i></i>
            <span :class="{ active: preview }">03 <b>周期与数量</b></span>
          </div>

          <div class="buy-block">
            <div class="block-head">
              <div><p class="eyebrow dark">STEP 01</p><h2>选择产品</h2></div>
            </div>
            <div v-if="products.length" class="type-picker">
              <button
                v-for="item in products"
                :key="item.code"
                type="button"
                :disabled="!item.regionCount"
                :class="{ selected: selectedProductCode === item.code }"
                :title="item.desc"
                @click="pickProduct(item.code)"
              >
                <span>{{ item.regionCount }} 个国家 / 地区</span>
                <b>{{ item.name }}</b>
                <small>{{ productPrice(item) }}</small>
              </button>
            </div>
            <p v-else class="inline-empty">当前没有可售产品，请联系管理员。</p>
          </div>

          <div class="buy-block">
            <div class="block-head">
              <div><p class="eyebrow dark">STEP 02</p><h2>选择国家 / 地区</h2></div>
              <label class="search-box"><Search :size="16" /><input v-model="query" placeholder="搜索国家或地区" /></label>
            </div>
            <div v-if="regionMatches.length" class="region-picker">
              <button
                v-for="region in regionMatches"
                :key="region.id"
                :class="{ selected: selectedRegionId === region.id }"
                @click="selectRegion(region.id)"
              >
                <span>{{ region.country_code }}</span>{{ region.name }}
                <em v-if="region.min_price !== null">¥{{ region.min_price }}</em>
                <Check v-if="selectedRegionId === region.id" :size="15" />
              </button>
            </div>
            <p v-else class="inline-empty">{{ product ? '该产品暂无可售国家，换一个产品试试。' : '请先选择产品。' }}</p>
          </div>

          <div class="buy-block">
            <div class="block-head">
              <div><p class="eyebrow dark">STEP 03</p><h2>确认数量与周期</h2></div>
            </div>
            <div class="order-controls">
              <label>线路数量
                <span class="counter">
                  <button :disabled="quantity <= 1" @click="quantity--"><Minus :size="14" /></button>
                  <b>{{ quantity }}</b>
                  <button :disabled="quantity >= 100" @click="quantity++"><Plus :size="14" /></button>
                </span>
              </label>
              <label>使用周期
                <select v-model="billingDays">
                  <option v-for="days in periods" :key="days" :value="days">{{ days }} 天</option>
                </select>
              </label>
            </div>
          </div>

          <div v-if="signedIn" v-show="citySupported" class="buy-block">
            <div class="block-head">
              <div><p class="eyebrow dark">STEP 04</p><h2>选择城市与 IP 段（可选）</h2></div>
            </div>
            <CitySegmentPicker
              v-if="selectedProductId"
              v-model="cityChoice"
              :product-id="selectedProductId"
              @ready="citySupported = Boolean($event.supported)"
            />
          </div>
        </div>

        <aside class="order-summary">
          <p class="eyebrow dark">ORDER PREVIEW</p>
          <h3>{{ product?.name || '请选择产品' }}</h3>
          <div class="summary-line"><span>地区</span><b>{{ selectedRegion?.name || '—' }}</b></div>
          <div class="summary-line"><span>数量</span><b>{{ quantity }} 条线路</b></div>
          <div class="summary-line"><span>周期</span><b>{{ billingDays }} 天</b></div>
          <div v-if="cityChoice.cityCode" class="summary-line"><span>城市</span><b>{{ cityChoice.cityCode }}</b></div>
          <div v-if="cityChoice.ipSegment" class="summary-line"><span>IP 段</span><b>{{ cityChoice.ipSegment }}</b></div>
          <div class="summary-line">
            <span>单价</span>
            <b>{{ !signedIn ? '登录可见' : preview ? `¥${preview.unit_price.toFixed(2)}` : '—' }}</b>
          </div>
          <div class="summary-line">
            <span>折合</span>
            <b>{{ !signedIn ? '登录可见' : preview ? `¥${(preview.unit_price / preview.billing_days).toFixed(2)} / 天` : '—' }}</b>
          </div>
          <div class="summary-total">
            <span>后端预览总额</span>
            <strong>{{ !signedIn ? '登录后显示' : previewing ? '计算中…' : preview ? `¥${preview.total_amount.toFixed(2)}` : '—' }}</strong>
          </div>
          <p v-if="error" class="summary-error">{{ error }}</p>
          <p class="demo-note">价格由后端实时计算。余额与在线支付尚未接通，提交后仅生成待处理订单。</p>
          <button v-if="signedIn" class="button full-button" :disabled="!preview || previewing || !selectedOffer" @click="router.push('/checkout')">
            确认并继续 <ArrowRight :size="16" />
          </button>
          <button v-else class="button full-button" @click="goLogin">登录后查看价格 <ArrowRight :size="16" /></button>
        </aside>
      </template>
    </section>
  </div>
</template>
