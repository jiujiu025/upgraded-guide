<script setup>
// 产品线详情：静态住宅IP / 社媒运营专线 / 直播专线。
// 未登录只能看到国家与周期，价格由后端脱敏（不是前端藏起来）；登录后才有报价。
import { computed, ref, watch } from 'vue'
import { ArrowRight, ArrowUpRight, Check, Clock3, LockKeyhole, RefreshCw, ShieldCheck } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import { getCatalogDetail, getPublicCatalogDetail } from '../api'
import { session } from '../api/session'

const props = defineProps({ code: { type: String, default: '' } })
const router = useRouter()
const route = useRoute()
const detail = ref(null)
const loading = ref(true)
const error = ref('')
const selectedRegionId = ref(null)
const signedIn = computed(() => Boolean(session.user))
const priceVisible = computed(() => Boolean(detail.value?.price_visible))

const regions = computed(() => detail.value?.regions || [])
const selectedRegion = computed(() => regions.value.find((item) => item.id === selectedRegionId.value) || regions.value[0] || null)
const periods = computed(() => detail.value?.periods || [])
// 直播有两个规格（手机 / 电脑），住宅与社媒只有一个规格。
const offers = computed(() => selectedRegion.value?.offers || [])
const hasOffers = computed(() => offers.value.length > 0)
const cells = computed(() => offers.value.map((offer) => ({
  spec: offer.spec || '标准',
  price: offer.unit_price,
  inStock: offer.in_stock,
  productId: offer.product_id,
})))
const lowestOffer = computed(() => offers.value.find((offer) => offer.in_stock) || null)
const minPrice = computed(() => (hasOffers.value ? Math.min(...offers.value.map((offer) => offer.unit_price)) : null))

async function load() {
  if (!props.code) { loading.value = false; return }
  loading.value = true
  error.value = ''
  detail.value = null
  try {
    const { data } = signedIn.value ? await getCatalogDetail(props.code) : await getPublicCatalogDetail(props.code)
    detail.value = data
    const preferred = data.regions.find((region) => (region.offers || []).length) || data.regions[0]
    selectedRegionId.value = preferred?.id ?? null
  } catch (requestError) {
    error.value = requestError.userMessage || '产品信息加载失败'
  } finally {
    loading.value = false
  }
}

function goLogin() {
  router.push({ path: '/login', query: { redirect: route.fullPath } })
}

function buyTarget(productId) {
  // 已登录客户直接进客户中心内的选购页，不再跳官网，避免"回不去"。
  return session.user?.role === 'customer' ? `/client/buy?product=${productId}` : `/buy?product=${productId}`
}

function startOrder() {
  if (!priceVisible.value) { goLogin(); return }
  if (!lowestOffer.value) return
  router.push(buyTarget(lowestOffer.value.product_id))
}

function formatPrice(value) {
  return `¥${Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

watch(() => props.code, load, { immediate: true })
</script>

<template>
  <div>
    <section v-if="loading" class="detail-state">正在读取产品与价格…</section>
    <section v-else-if="error" class="detail-state detail-error">
      <p>{{ error }}</p>
      <button class="button button-small" @click="load"><RefreshCw :size="15" />重试</button>
    </section>
    <template v-else-if="detail">
      <section class="detail-hero">
        <div>
          <p class="eyebrow">PRODUCT / {{ detail.code.toUpperCase() }}</p>
          <h1>{{ detail.name }}</h1>
          <p v-if="detail.subtitle" class="line-subtitle">{{ detail.subtitle }}</p>
          <p>
            覆盖 {{ detail.region_count }} 个国家/地区，可选周期 {{ periods.join(' / ') }} 天<template v-if="priceVisible && minPrice !== null">，¥{{ minPrice }} / 30 天起</template>。
            所有价格由业务系统实时计算。
          </p>
          <div class="hero-actions">
            <button class="button" :disabled="priceVisible && !hasOffers" @click="startOrder">
              {{ priceVisible ? (hasOffers ? '开始选购' : '该国家暂无报价') : '登录后查看价格' }} <ArrowUpRight :size="15" />
            </button>
            <button v-if="!priceVisible" class="text-button" @click="goLogin"><LockKeyhole :size="15" />价格仅登录可见</button>
          </div>
        </div>
        <div class="detail-signal">
          <span>LIVE CATALOG</span>
          <strong>{{ priceVisible ? '价格已解锁' : '价格对未登录用户隐藏' }}</strong><i></i>
          <small>{{ priceVisible ? '报价实时读取，下单前再次校验' : '登录后可见各国报价与可下单规格' }}</small>
        </div>
      </section>

      <section class="section light country-section">
        <div class="section-head">
          <div>
            <p class="eyebrow dark">COUNTRY</p>
            <h2>选择国家 / 地区</h2>
            <p class="section-note">三条产品线共用同一份国家清单；标「暂无报价」的国家暂不可下单。</p>
          </div>
        </div>
        <div class="country-grid">
          <button
            v-for="region in regions"
            :key="region.id"
            type="button"
            :class="{ selected: region.id === selectedRegion?.id, empty: priceVisible && !(region.offers || []).length }"
            @click="selectedRegionId = region.id"
          >
            <b>{{ region.name }}</b>
            <small v-if="priceVisible && (region.offers || []).length">{{ formatPrice(Math.min(...region.offers.map((o) => o.unit_price))) }} / 30天</small>
            <small v-else-if="priceVisible">暂无报价</small>
            <small v-else>{{ region.country_code }}</small>
          </button>
        </div>
      </section>

      <section class="section light price-section">
        <div class="price-panel">
          <div class="price-head">
            <div>
              <p class="eyebrow dark">PRICE</p>
              <h2>{{ selectedRegion?.name || '—' }}</h2>
            </div>
            <span v-if="priceVisible" class="price-badge">30 天价格</span>
          </div>

          <div v-if="!priceVisible" class="price-locked">
            <LockKeyhole :size="22" />
            <b>登录后查看价格</b>
            <p>报价按国家与产品线分别维护，登录后即可看到{{ detail.name }}在 {{ selectedRegion?.name || '该国' }} 的具体价格与可下单规格。</p>
            <button class="button button-small" @click="goLogin">登录 / 注册 <ArrowRight :size="15" /></button>
          </div>

          <div v-else-if="!cells.length" class="price-locked">
            <b>该国家暂无报价</b>
            <p>可以换一个国家，或联系业务经理确认这条产品线在该地区的可售情况。</p>
          </div>

          <div v-else class="price-table-wrap">
            <table class="price-table">
              <thead>
                <tr>
                  <th>规格</th>
                  <th>30 天</th>
                  <th>库存</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="cell in cells" :key="cell.spec">
                  <td><b>{{ cell.spec }}</b></td>
                  <td><b>{{ formatPrice(cell.price) }}</b></td>
                  <td :class="{ muted: !cell.inStock }">{{ cell.inStock ? '可下单' : '暂时无货' }}</td>
                  <td>
                    <button v-if="cell.inStock" class="table-cta" @click="router.push(buyTarget(cell.productId))">选购</button>
                    <span v-else class="muted">—</span>
                  </td>
                </tr>
              </tbody>
            </table>
            <p class="price-note">90 / 180 / 360 天按 30 天价线性折算，具体金额在确认订单页显示。</p>
          </div>
        </div>

        <div class="delivery-panel">
          <div class="panel-title"><span>交付内容</span><span class="status"><i></i> 待开通</span></div>
          <div class="delivery-row"><span><Clock3 :size="15" />节点与订阅信息</span><b>开通后显示</b></div>
          <div class="delivery-row"><span><ShieldCheck :size="15" />查看位置</span><b class="green">客户中心 · 我的线路</b></div>
          <div class="delivery-empty">交付内容在订单开通后从客户中心读取，官网不展示节点链接或二维码。</div>
        </div>
      </section>

      <section class="section faq-section">
        <p class="eyebrow dark">FAQ</p>
        <h2>常见问题</h2>
        <div class="faq-list">
          <details open>
            <summary>为什么登录后才能看到价格？ <ArrowRight :size="15" /></summary>
            <p>报价按国家和产品线分别维护，属于客户可见信息；未登录访客只能看到产品范围与国家覆盖。</p>
          </details>
          <details>
            <summary>社媒运营专线和静态住宅 IP 有什么区别？ <ArrowRight :size="15" /></summary>
            <p>社媒专线在静态住宅 IP 的基础上增加了社媒场景用的中转链路，价格单独维护，不与住宅价成固定比例。</p>
          </details>
          <details>
            <summary>价格会变动吗？ <ArrowRight :size="15" /></summary>
            <p>页面价格来自当前价目表，提交订单时后端会重新计算；以订单上的金额为准。</p>
          </details>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.detail-state{min-height:320px;display:grid;place-content:center;justify-items:center;gap:12px;color:#5d726b;font-size:13px}
.detail-error p{margin:0;color:#9a4538}
.country-section{padding-bottom:0}
.section-note{margin:10px 0 0;color:#71837b;font-size:13px}
.country-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:9px}
.country-grid button{display:grid;gap:4px;padding:12px 13px;border:1px solid #dbe4e1;border-radius:8px;background:#fff;text-align:left;transition:border-color .15s,background .15s}
.country-grid button:hover{border-color:#6db4a7}
.country-grid button.selected{border-color:#2c9b88;background:#e7f6f1}
.country-grid button.empty{background:#f7f9f8}
.country-grid b{font-size:14px;color:#1d322b}
.country-grid small{color:#71837b;font-size:11px}
.price-section{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(280px,.65fr);gap:26px;align-items:start}
.price-panel{border:1px solid #e2e9e5;border-radius:10px;background:#fff;padding:22px}
.price-head{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;margin-bottom:16px}
.price-head h2{margin:4px 0 0;font-size:26px}
.price-badge{font-size:11px;color:#2c6f60;background:#e7f6f1;border-radius:20px;padding:5px 11px;white-space:nowrap}
.price-table-wrap{overflow:auto}
.price-table{width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap}
.price-table th,.price-table td{padding:12px 14px;text-align:left;border-bottom:1px solid #eef2f0}
.price-table th{background:#f6f9f7;color:#5f746b;font-size:11px;font-weight:650}
.price-table tbody tr:last-child td{border-bottom:0}
.price-table td b{color:#1d322b}
.price-table td.muted,.muted{color:#a3b0aa}
.price-note{margin:12px 0 0;color:#8a9a93;font-size:11px}
.table-cta{min-height:30px;padding:0 12px;border-radius:6px;background:#176e55;color:#fff;font-size:12px;font-weight:650}
.price-locked{display:grid;gap:10px;justify-items:start;padding:34px 8px;color:#4b6a61}
.price-locked b{font-size:17px;color:#1d322b}
.price-locked p{margin:0;color:#71837b;font-size:13px;line-height:1.7;max-width:420px}
@media(max-width:900px){.price-section{grid-template-columns:1fr}}
</style>
