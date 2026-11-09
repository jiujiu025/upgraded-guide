<script setup>
// 产品详情：价格全部来自 /api/public/catalog/{code}，前端只做展示与跳转。
// 这里刻意不编造节点、带宽、延迟之类的"卖点"——产品库里没有的数据就不写。
import { computed, ref, watch } from 'vue'
import { ArrowRight, ArrowUpRight, Check, Clock3, RefreshCw, ShieldCheck } from 'lucide-vue-next'
import { useRouter } from 'vue-router'
import { getPublicCatalogDetail } from '../api'

const props = defineProps({ code: { type: String, default: '' } })
const router = useRouter()
const detail = ref(null)
const loading = ref(true)
const error = ref('')

const periods = computed(() => detail.value?.periods || [])
// 把"地区 → 周期"的报价摊平成表格：每个地区一行，每个周期一列。
const rows = computed(() => (detail.value?.regions || []).map((region) => ({
  ...region,
  cells: Object.fromEntries((region.offers || []).map((offer) => [offer.billing_days, offer])),
})))
const firstAvailable = computed(() => {
  for (const region of detail.value?.regions || []) {
    const offer = (region.offers || []).find((item) => item.in_stock)
    if (offer) return offer
  }
  return null
})

function cell(region, days) {
  const offer = region.cells?.[days]
  if (!offer) return { text: '—', muted: true }
  return offer.in_stock ? { text: `¥${offer.unit_price}`, muted: false } : { text: '无货', muted: true }
}

async function load() {
  if (!props.code) { loading.value = false; return }
  loading.value = true
  error.value = ''
  detail.value = null
  try {
    const { data } = await getPublicCatalogDetail(props.code)
    detail.value = data
  } catch (requestError) {
    error.value = requestError.userMessage || '产品信息加载失败'
  } finally {
    loading.value = false
  }
}

function startOrder() {
  if (firstAvailable.value) {
    router.push(`/buy?product=${firstAvailable.value.product_id}`)
    return
  }
  router.push('/products')
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
          <p class="eyebrow">PRODUCT / {{ detail.kind === 'fixed' ? 'FIXED PRICE' : 'RESOURCE POOL' }}</p>
          <h1>{{ detail.name }}</h1>
          <p>覆盖 {{ detail.region_count }} 个地区，可选周期 {{ periods.join(' / ') }} 天，起价 ¥{{ detail.min_price }}。所有价格由业务系统实时计算。</p>
          <button class="button" :disabled="!detail.in_stock" @click="startOrder">
            {{ detail.in_stock ? '开始选购' : '当前无货' }} <ArrowUpRight :size="15" />
          </button>
        </div>
        <div class="detail-signal">
          <span>LIVE CATALOG</span>
          <strong>产品信息来自业务系统</strong><i></i>
          <small>库存与价格实时读取，下单前再次校验</small>
        </div>
      </section>

      <section class="section light detail-body">
        <div class="detail-copy">
          <p class="eyebrow dark">PRICE LIST</p>
          <h2>地区 × 周期报价</h2>
          <p>表格里的价格即当前可下单的单价，单位「元 / 该周期」。空表示该地区没有这个周期，「无货」表示暂时不可下单。</p>
          <div class="detail-points">
            <div><Check :size="15" />周期：{{ periods.join(' / ') }} 天</div>
            <div><Check :size="15" />地区：{{ detail.region_count }} 个</div>
            <div><Check :size="15" />库存：{{ detail.in_stock ? '可购买' : '暂时无货' }}</div>
          </div>
        </div>
        <div class="delivery-panel">
          <div class="panel-title"><span>线路交付内容</span><span class="status"><i></i> 待开通</span></div>
          <div class="delivery-row"><span><Clock3 :size="15" />节点与订阅信息</span><b>开通后显示</b></div>
          <div class="delivery-row"><span><ShieldCheck :size="15" />查看位置</span><b class="green">客户中心 · 我的线路</b></div>
          <div class="delivery-empty">交付内容在订单开通后从客户中心读取，官网不展示节点链接或二维码。</div>
        </div>
      </section>

      <section class="section light price-section">
        <div class="price-table-wrap">
          <table class="price-table">
            <thead>
              <tr>
                <th>地区</th>
                <th v-for="days in periods" :key="days">{{ days }} 天</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="region in rows" :key="region.id">
                <td><b>{{ region.name }}</b><small>{{ region.country_code }}</small></td>
                <td v-for="days in periods" :key="days" :class="{ muted: cell(region, days).muted }">
                  {{ cell(region, days).text }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="section faq-section">
        <p class="eyebrow dark">FAQ</p>
        <h2>常见问题</h2>
        <div class="faq-list">
          <details open>
            <summary>下单后什么时候可以使用？ <ArrowRight :size="15" /></summary>
            <p>订单需经管理员确认和交付；交付后可在客户中心查看真实线路数据。</p>
          </details>
          <details>
            <summary>价格会变动吗？ <ArrowRight :size="15" /></summary>
            <p>页面价格来自当前价目表，提交订单时后端会重新计算；以订单上的金额为准。</p>
          </details>
          <details>
            <summary>可以更换地区或周期吗？ <ArrowRight :size="15" /></summary>
            <p>提交前可以返回修改；提交后的操作范围以后端订单状态为准。</p>
          </details>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.detail-state{min-height:320px;display:grid;place-content:center;justify-items:center;gap:12px;color:#5d726b;font-size:13px}
.detail-error p{margin:0;color:#9a4538}
.price-section{padding-top:0}
.price-table-wrap{overflow:auto;border:1px solid #e2e9e5;border-radius:8px;background:#fff}
.price-table{width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap}
.price-table th,.price-table td{padding:12px 16px;text-align:left;border-bottom:1px solid #eef2f0}
.price-table th{background:#f6f9f7;color:#5f746b;font-size:11px;font-weight:650;letter-spacing:.02em}
.price-table tbody tr:last-child td{border-bottom:0}
.price-table td b{display:block;color:#1d322b;font-size:13px}
.price-table td small{display:block;margin-top:2px;color:#8a9a93;font-size:10px}
.price-table td.muted{color:#a3b0aa}
</style>
