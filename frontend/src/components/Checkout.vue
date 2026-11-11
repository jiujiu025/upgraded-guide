<script setup>
import { computed, onMounted, ref } from 'vue'
import { ArrowLeft, Check, CircleAlert, RefreshCw } from 'lucide-vue-next'
import { useRouter } from 'vue-router'
import { createCustomerOrder, previewOrder } from '../api'
import PageIntro from './PageIntro.vue'
import { readSession, removeSession } from '../utils/storage'
import { session } from '../api/session'

const router = useRouter()
// 默认选余额支付：这是唯一能当场完成、并触发自动开通的路径
const method = ref('balance')
const note = ref('')
const selection = ref(null)
const preview = ref(null)
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const isLoggedIn = computed(() => Boolean(session.user))

function readSelection() {
  const saved = readSession('checkout_selection')
  if (!saved) return null
  try { return JSON.parse(saved) } catch { return null }
}

async function loadPreview() {
  loading.value = true
  error.value = ''
  try {
    selection.value = readSelection()
    if (!selection.value) throw new Error('missing-selection')
    const { data } = await previewOrder(selection.value)
    preview.value = data
  } catch (requestError) {
    error.value = requestError.message === 'missing-selection'
      ? '没有找到待结算的选购信息，请重新选择产品。'
      : requestError.userMessage || '价格核验失败，请稍后重试。'
  } finally { loading.value = false }
}

async function submit() {
  if (!isLoggedIn.value) { await router.push({ path: '/login', query: { redirect: '/checkout' } }); return }
  if (!preview.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const payload = { ...selection.value, payment_method: method.value, remark: note.value.trim() || null }
    const { data } = await createCustomerOrder(payload)
    removeSession('checkout_selection')
    await router.push(`/client/orders/${data.id}?created=1`)
  } catch (requestError) { error.value = requestError.userMessage || '订单创建失败，请稍后重试' } finally { busy.value = false }
}
onMounted(loadPreview)
</script>

<template><div><PageIntro title="确认订单" desc="价格在下单前由后端重新校验，提交后在订单页完成支付。" /><section class="section light checkout-layout"><div v-if="loading" class="state-panel">正在核验订单价格…</div><div v-else-if="error && !preview" class="state-panel error-state"><p>{{ error }}</p><div class="state-actions"><button class="outline-action" @click="loadPreview"><RefreshCw :size="15" />重试</button><button class="button button-small" @click="router.push('/buy')">重新选购</button></div></div><template v-else><div class="checkout-card"><div class="checkout-title"><h2>订单信息</h2><button class="back-link" @click="router.push('/buy')"><ArrowLeft :size="15" />返回修改</button></div><div class="checkout-table"><div><span>产品</span><b>{{ preview.product_name }}</b></div><div><span>地区</span><b>{{ preview.region }}</b></div><div><span>数量</span><b>{{ preview.quantity }} 条</b></div><div><span>周期</span><b>{{ preview.billing_days }} 天</b></div><div v-if="selection.city_code"><span>指定城市</span><b>{{ selection.city_code }}<template v-if="selection.ip_segment"> · {{ selection.ip_segment }}</template></b></div><div><span>单价</span><b>¥{{ preview.unit_price.toFixed(2) }}</b></div><div><span>折合</span><b>¥{{ (preview.unit_price / preview.billing_days).toFixed(2) }} / 天</b></div><div class="checkout-total"><span>后端预览合计</span><strong>¥{{ preview.total_amount.toFixed(2) }}</strong></div></div><label class="field"><span>客户备注</span><textarea v-model="note" maxlength="1000" placeholder="告诉我们你的业务场景（可选）"></textarea></label></div><div class="checkout-card"><h2>支付方式</h2><div class="payment-options"><button :class="{ selected: method === 'balance' }" @click="method = 'balance'"><span>余额支付</span><small>从账户余额扣款，支付后自动开通线路</small></button><button :class="{ selected: method === 'online' }" @click="method = 'online'"><span>在线支付</span><small>跳转支付页面，到账后自动开通线路</small></button><button :class="{ selected: method === 'contact_manager' }" @click="method = 'contact_manager'"><span>联系经理确认</span><small>订单进入待确认，运营确认收款后开通</small></button></div><div class="demo-alert"><CircleAlert :size="17" /><span>提交后进入订单页完成支付；付款成功后系统会自动向运营商开通线路，通常几秒内就能在「我的线路」看到。</span></div><p v-if="error" class="form-error" role="alert">{{ error }}</p><button class="button full-button" :disabled="busy" @click="submit"><Check :size="16" />{{ busy ? '正在创建订单…' : (isLoggedIn ? '提交订单并去支付' : '登录后提交订单') }}</button></div></template></section></div></template>
