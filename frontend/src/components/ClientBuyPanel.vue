<script setup>
// 客户中心内完成选购与下单：不跳官网，下单成功直接进客户中心的订单详情。
// 价格来自 /api/catalog（登录态才有），金额最终仍以 previewOrder 返回为准。
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ArrowRight, Check, Loader2, Minus, Plus, ShoppingCart } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import { createCustomerOrder, getCatalog, previewOrder } from '../api'
import CitySegmentPicker from './CitySegmentPicker.vue'

const router = useRouter()
const route = useRoute()
const loading = ref(true)
const error = ref('')
const previewing = ref(false)
const submitting = ref(false)
const preview = ref(null)
const cityChoice = ref({ cityCode: '', ipSegment: '' })
const citySupported = ref(false)
const cityMeta = ref({ items: [] })
const catalog = ref({ lines: [] })
const form = reactive({
  lineCode: '',
  regionId: null,
  productId: null,
  billingDays: 30,
  quantity: 1,
  paymentMethod: 'contact_manager',
  remark: '',
})
let previewTimer

const lines = computed(() => catalog.value.lines || [])
const line = computed(() => lines.value.find((item) => item.code === form.lineCode) || null)
const regions = computed(() => line.value?.regions || [])
const region = computed(() => regions.value.find((item) => item.id === form.regionId) || null)
const offers = computed(() => region.value?.offers || [])
const periods = computed(() => line.value?.periods || [])
const offer = computed(() => offers.value.find((item) => item.product_id === form.productId) || null)
const cityLabel = computed(() => {
  if (!cityChoice.value.cityCode) return ''
  const hit = (cityMeta.value.items || []).find((item) => item.city_code === cityChoice.value.cityCode)
  return hit ? `${hit.city_name}（${hit.city_code}）` : cityChoice.value.cityCode
})
// 没有可售城市时整块隐藏，规格步骤自动从 ④ 顶到 ③，避免编号出现空档
const specStep = computed(() => (citySupported.value ? '④' : '③'))
const canSubmit = computed(() => Boolean(offer.value?.in_stock) && Boolean(preview.value) && !submitting.value)

function regionPrice(entry) {
  const list = entry.offers || []
  if (!list.length) return null
  return Math.min(...list.map((item) => item.unit_price))
}

function pickLine(code) {
  form.lineCode = code
  const target = lines.value.find((item) => item.code === code)
  const regionWithOffer = (target?.regions || []).find((item) => (item.offers || []).length) || target?.regions?.[0]
  form.regionId = regionWithOffer?.id ?? null
  form.billingDays = target?.periods?.[0] || 30
  syncProduct()
}

function pickRegion(id) {
  form.regionId = id
  syncProduct()
}

// 同一地区可能有多个规格（直播：手机/电脑），默认选第一个有货的。
function syncProduct() {
  const list = region.value?.offers || []
  const preferred = list.find((item) => item.in_stock) || list[0]
  form.productId = preferred?.product_id ?? null
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await getCatalog()
    catalog.value = data
    // 从产品详情页带 ?product=<id> 过来时直接定位到该规格，省得再点一遍。
    const wanted = Number(route.query?.product || 0)
    if (wanted) {
      for (const item of lines.value) {
        for (const entry of item.regions) {
          if ((entry.offers || []).some((offer) => offer.product_id === wanted)) {
            pickLine(item.code)
            pickRegion(entry.id)
            form.productId = wanted
            return
          }
        }
      }
    }
    const first = lines.value.find((item) => item.regions.some((region) => (region.offers || []).length)) || lines.value[0]
    if (first) pickLine(first.code)
  } catch (requestError) {
    error.value = requestError.userMessage || '产品目录加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

async function refreshPreview() {
  if (!offer.value || !region.value) { preview.value = null; return }
  previewing.value = true
  try {
    const { data } = await previewOrder({
      product_id: offer.value.product_id,
      region_id: region.value.id,
      quantity: form.quantity,
      billing_days: form.billingDays,
    })
    preview.value = data
    error.value = ''
  } catch (requestError) {
    preview.value = null
    error.value = requestError.userMessage || '价格计算失败，请重试'
  } finally {
    previewing.value = false
  }
}

watch([() => form.productId, () => form.billingDays, () => form.quantity], () => {
  clearTimeout(previewTimer)
  previewTimer = setTimeout(refreshPreview, 250)
})

async function submit() {
  if (!canSubmit.value || !offer.value) return
  submitting.value = true
  error.value = ''
  try {
    const { data } = await createCustomerOrder({
      product_id: offer.value.product_id,
      region_id: region.value.id,
      quantity: Number(form.quantity),
      billing_days: Number(form.billingDays),
      payment_method: form.paymentMethod,
      remark: form.remark.trim() || null,
      city_code: cityChoice.value.cityCode || null,
      ip_segment: cityChoice.value.ipSegment || null,
    })
    router.push(`/client/orders/${data.id}?created=1`)
  } catch (requestError) {
    error.value = requestError.userMessage || '下单失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="client-buy">
    <div v-if="loading" class="buy-state">正在读取产品与价格…</div>
    <div v-else-if="error && !lines.length" class="buy-state buy-error">
      <p>{{ error }}</p>
      <button class="button button-small" @click="load">重试</button>
    </div>

    <template v-else>
      <div class="buy-head">
        <div>
          <p class="buy-eyebrow">购买线路</p>
          <h1>在本页完成选购，下单后直接进入订单详情</h1>
          <p class="buy-desc">价格取自业务系统；下单后可在「我的订单」查看，交付后线路出现在「我的线路」。</p>
        </div>
        <button class="buy-back" type="button" @click="router.push('/client')">返回工作台</button>
      </div>

      <div class="buy-layout">
        <div class="buy-main">
          <section class="buy-block">
            <h2>① 选择产品线</h2>
            <div class="line-grid">
              <button
                v-for="item in lines"
                :key="item.code"
                type="button"
                :class="{ selected: form.lineCode === item.code }"
                @click="pickLine(item.code)"
              >
                <b>{{ item.name }}</b>
                <small>{{ item.region_count }} 个国家/地区</small>
                <em v-if="item.min_price !== null">¥{{ item.min_price }} / 30天起</em>
                <em v-else>暂无报价</em>
              </button>
            </div>
          </section>

          <section class="buy-block">
            <h2>② 选择国家 / 地区</h2>
            <div class="region-grid">
              <button
                v-for="item in regions"
                :key="item.id"
                type="button"
                :class="{ selected: form.regionId === item.id, empty: !(item.offers || []).length }"
                @click="pickRegion(item.id)"
              >
                <b>{{ item.name }}</b>
                <small v-if="regionPrice(item) !== null">¥{{ regionPrice(item) }} / 30天</small>
                <small v-else>暂无报价</small>
              </button>
            </div>
          </section>

          <section v-show="citySupported" class="buy-block">
            <h2>③ 城市与 IP 段（可选）</h2>
            <div v-if="!offers.length" class="inline-empty">该国家在当前产品线下暂无报价，换一个国家试试。</div>
            <CitySegmentPicker
              v-else-if="offer"
              v-model="cityChoice"
              plain
              :product-id="offer.product_id"
              @ready="cityMeta = $event; citySupported = Boolean($event.supported)"
            />
            <p v-else class="inline-empty">当前地区暂无城市可选。</p>
          </section>

          <section class="buy-block">
            <h2>{{ specStep }} 规格、周期与数量</h2>
            <div v-if="!offers.length" class="inline-empty">该国家在当前产品线下暂无报价，换一个国家试试。</div>
            <template v-else>
              <div v-if="offers.length > 1" class="spec-list">
                <button
                  v-for="item in offers"
                  :key="item.product_id"
                  type="button"
                  :disabled="!item.in_stock"
                  :class="{ selected: form.productId === item.product_id }"
                  @click="form.productId = item.product_id"
                >
                  <b>{{ item.spec }}</b>
                  <span>¥{{ item.unit_price }} / 30天</span>
                  <small v-if="!item.in_stock">暂时无货</small>
                </button>
              </div>

              <div class="buy-controls">
                <label>使用周期
                  <select v-model.number="form.billingDays">
                    <option v-for="days in periods" :key="days" :value="days">{{ days }} 天</option>
                  </select>
                </label>
                <label>线路数量
                  <span class="counter">
                    <button type="button" :disabled="form.quantity <= 1" @click="form.quantity--"><Minus :size="14" /></button>
                    <b>{{ form.quantity }}</b>
                    <button type="button" :disabled="form.quantity >= 100" @click="form.quantity++"><Plus :size="14" /></button>
                  </span>
                </label>
              </div>

              <label class="buy-remark">备注（可选）
                <textarea v-model="form.remark" rows="2" maxlength="1000" placeholder="线路要求、交付说明" />
              </label>
            </template>
          </section>
        </div>

        <aside class="buy-summary">
          <p class="buy-eyebrow">订单预览</p>
          <h2>{{ line?.name || '—' }}</h2>
          <div class="summary-line"><span>地区</span><b>{{ region?.name || '—' }}</b></div>
          <div class="summary-line"><span>规格</span><b>{{ offer?.spec || '标准' }}</b></div>
          <div v-if="cityLabel" class="summary-line"><span>城市</span><b>{{ cityLabel }}</b></div>
          <div v-if="cityChoice.ipSegment" class="summary-line"><span>IP 段</span><b>{{ cityChoice.ipSegment }}</b></div>
          <div class="summary-line"><span>周期</span><b>{{ form.billingDays }} 天</b></div>
          <div class="summary-line"><span>数量</span><b>{{ form.quantity }} 条</b></div>
          <div class="summary-line"><span>单价</span><b>{{ preview ? `¥${preview.unit_price.toFixed(2)}` : '—' }}</b></div>
          <div class="summary-total">
            <span>合计</span>
            <strong>{{ previewing ? '计算中…' : preview ? `¥${preview.total_amount.toFixed(2)}` : '—' }}</strong>
          </div>

          <div class="pay-options">
            <button type="button" :class="{ selected: form.paymentMethod === 'contact_manager' }" @click="form.paymentMethod = 'contact_manager'">联系经理确认<small>订单进入人工确认</small></button>
            <button type="button" :class="{ selected: form.paymentMethod === 'online' }" @click="form.paymentMethod = 'online'">在线支付<small>下单后到订单页付款</small></button>
            <button type="button" :class="{ selected: form.paymentMethod === 'balance' }" @click="form.paymentMethod = 'balance'">余额支付<small>余额不足会支付失败</small></button>
          </div>

          <p v-if="error" class="buy-error-text">{{ error }}</p>
          <button class="buy-submit" type="button" :disabled="!canSubmit" @click="submit">
            <Loader2 v-if="submitting" :size="15" class="spin" /><ShoppingCart v-else :size="15" />
            {{ submitting ? '正在创建订单…' : '确认下单' }}
          </button>
          <p class="buy-note"><Check :size="13" /> 下单后可在「我的订单」查看进度，交付后到「我的线路」查看节点信息。</p>
        </aside>
      </div>
    </template>
  </div>
</template>

<style scoped>
.client-buy{display:grid;gap:16px}
.buy-state{min-height:280px;display:grid;place-content:center;justify-items:center;gap:12px;color:#5d726b;font-size:13px}
.buy-error p{margin:0;color:#9a4538}
.buy-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}
.buy-eyebrow{margin:0;color:#6d8177;font-size:11px;letter-spacing:.06em}
.buy-head h1{margin:6px 0 0;font-size:20px;color:#22352e}
.buy-desc{margin:6px 0 0;color:#71837b;font-size:12px}
.buy-back{min-height:34px;padding:0 14px;border:1px solid #d6e1db;border-radius:6px;background:#fff;color:#476258;font-size:12px;font-weight:650;white-space:nowrap}
.buy-layout{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:16px;align-items:start}
.buy-main{display:grid;gap:14px;min-width:0}
.buy-block{background:#f9fbfa;border:1px solid #dbe3e0;border-radius:6px;padding:16px 18px}
.buy-block h2{margin:0 0 12px;font-size:14px;color:#2c4338}
.line-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}
.line-grid button{display:grid;gap:4px;padding:13px 12px;border:1px solid #dbe4e1;border-radius:6px;background:#fff;text-align:left}
.line-grid button.selected{border-color:#2c9b88;background:#e7f6f1}
.line-grid b{font-size:13px;color:#1d322b}
.line-grid small{color:#71837b;font-size:11px}
.line-grid em{font-style:normal;color:#2c6f60;font-size:11px;font-weight:650}
.region-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:7px;max-height:280px;overflow:auto}
.region-grid button{display:grid;gap:3px;padding:9px 10px;border:1px solid #dbe4e1;border-radius:6px;background:#fff;text-align:left}
.region-grid button.selected{border-color:#2c9b88;background:#e7f6f1}
.region-grid button.empty{background:#f5f7f6}
.region-grid b{font-size:12px;color:#1d322b}
.region-grid small{color:#7a8b83;font-size:10px}
.spec-list{display:flex;gap:9px;margin-bottom:12px}
.spec-list button{display:grid;gap:2px;padding:10px 14px;border:1px solid #dbe4e1;border-radius:6px;background:#fff;text-align:left}
.spec-list button.selected{border-color:#2c9b88;background:#e7f6f1}
.spec-list button:disabled{opacity:.5;cursor:not-allowed}
.spec-list b{font-size:13px}
.spec-list span{color:#2c6f60;font-size:11px;font-weight:650}
.spec-list small{color:#9a4538;font-size:10px}
.buy-controls{display:flex;gap:14px;flex-wrap:wrap}
.buy-controls label{display:grid;gap:5px;color:#5e7771;font-size:11px}
.buy-controls select{min-width:130px;height:36px;padding:0 10px;border:1px solid #d6e1db;border-radius:5px;background:#fff;color:#20332d}
.counter{display:inline-flex;align-items:center;height:36px;border:1px solid #d6e1db;border-radius:5px;background:#fff}
.counter button{width:32px;height:100%;display:grid;place-items:center;color:#3c6a5c}
.counter b{width:32px;text-align:center;font-size:12px}
.buy-remark{display:grid;gap:5px;margin-top:12px;color:#5e7771;font-size:11px}
.buy-remark textarea{min-height:54px;padding:8px 10px;border:1px solid #d6e1db;border-radius:5px;background:#fff;font:inherit;font-size:12px;resize:vertical}
.buy-summary{position:sticky;top:16px;background:#173b37;color:#fff;border-radius:8px;padding:18px}
.buy-summary .buy-eyebrow{color:#8fb6ac}
.buy-summary h2{margin:6px 0 14px;font-size:18px}
.summary-line{display:flex;justify-content:space-between;padding:8px 0;border-top:1px solid #376059;color:#a9c2bd;font-size:12px}
.summary-line b{color:#fff;font-weight:500}
.summary-total{display:flex;justify-content:space-between;align-items:flex-end;border-top:1px solid #588477;margin-top:10px;padding-top:12px}
.summary-total span{font-size:11px;color:#a9c2bd}
.summary-total strong{font-size:22px;color:#49e0c1}
.pay-options{display:grid;gap:7px;margin:14px 0 12px}
.pay-options button{display:grid;gap:2px;padding:9px 11px;border:1px solid #376059;border-radius:6px;color:#dbeae6;text-align:left;font-size:12px}
.pay-options button.selected{border-color:#49e0c1;background:#1f4a44}
.pay-options small{color:#8fb6ac;font-size:10px}
.buy-submit{width:100%;min-height:40px;display:inline-flex;align-items:center;justify-content:center;gap:8px;border-radius:6px;background:#49e0c1;color:#08201d;font-size:13px;font-weight:750}
.buy-submit:disabled{opacity:.5;cursor:not-allowed}
.buy-note{display:flex;gap:6px;align-items:flex-start;margin:10px 0 0;color:#8fb6ac;font-size:10px;line-height:1.6}
.buy-error-text{margin:0 0 9px;color:#ffb3a3;font-size:11px}
.spin{animation:cb-spin .8s linear infinite}
@keyframes cb-spin{to{transform:rotate(360deg)}}
@media(max-width:900px){.buy-layout{grid-template-columns:1fr}.buy-summary{position:static}.line-grid{grid-template-columns:1fr}}
@media(prefers-reduced-motion:reduce){.spin{animation:none}}
</style>
