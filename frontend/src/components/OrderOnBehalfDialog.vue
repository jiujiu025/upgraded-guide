<script setup>
// 代客下单弹窗：销售给名下客户开单，管理员可给任意客户开单并设置折扣（默认上限 30% = 7 折）。
// 设计见 docs/销售代客下单设计.md。客户身份固定只读，避免选错人。
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { Loader2, ShoppingCart, X } from 'lucide-vue-next'
import { createOnBehalfOrder, getPublicProducts, previewOrder } from '../api'
import CitySegmentPicker from './CitySegmentPicker.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  customer: { type: Object, default: null },
  canDiscount: { type: Boolean, default: false },
  maxDiscountPercent: { type: Number, default: 30 },
})
const emit = defineEmits(['close', 'created'])

const products = ref([])
const loading = ref(false)
const previewing = ref(false)
const submitting = ref(false)
const error = ref('')
const preview = ref(null)
const cityChoice = ref({ cityCode: '', ipSegment: '' })
const form = reactive({
  productId: null,
  quantity: 1,
  billingDays: 30,
  paymentMethod: 'contact_manager',
  discountPercent: 0,
  remark: '',
})
// 幂等键在弹窗打开时生成一次：双击、超时重试都落到同一张单上，成功后重置。
let idempotencyKey = ''
let previewTimer

const selectedProduct = computed(() => products.value.find((item) => item.id === form.productId) || null)
const periods = computed(() => {
  const fromBackend = selectedProduct.value?.periods ?? []
  if (fromBackend.length) return fromBackend.map((item) => item.billing_days)
  return selectedProduct.value ? [selectedProduct.value.billing_days] : []
})
const hasAccount = computed(() => props.customer?.has_account !== false)
const discountApplied = computed(() => props.canDiscount && Number(form.discountPercent) > 0)
const discountedTotal = computed(() => {
  if (!preview.value) return null
  const factor = (100 - Number(form.discountPercent)) / 100
  return Math.round(preview.value.total_amount * factor * 100) / 100
})
const canSubmit = computed(() => Boolean(selectedProduct.value) && !submitting.value && Boolean(preview.value))

function money(value) {
  return `¥${Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function newKey() {
  // crypto.randomUUID 在非安全上下文不可用（http 内网访问），退回时间戳 + 随机串，仍满足 36 位长度要求。
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return `ob-${Date.now()}-${Math.random().toString(36).slice(2, 12)}-${Math.random().toString(36).slice(2, 12)}`
}

function resetForm() {
  Object.assign(form, { productId: null, quantity: 1, billingDays: 30, paymentMethod: 'contact_manager', discountPercent: 0, remark: '' })
  cityChoice.value = { cityCode: '', ipSegment: '' }
  preview.value = null
  error.value = ''
  idempotencyKey = newKey()
}

async function loadCatalog() {
  if (products.value.length) return
  loading.value = true
  try {
    const { data } = await getPublicProducts()
    products.value = data.filter((item) => item.is_active !== false)
    const first = products.value.find((item) => item.is_in_stock) || products.value[0]
    if (first) {
      form.productId = first.id
      form.billingDays = first.billing_days
    }
  } catch (requestError) {
    error.value = requestError.userMessage || '产品列表加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

async function refreshPreview() {
  if (!selectedProduct.value) { preview.value = null; return }
  previewing.value = true
  preview.value = null
  try {
    const { data } = await previewOrder({
      product_id: selectedProduct.value.id,
      region_id: selectedProduct.value.region_id,
      quantity: form.quantity,
      billing_days: form.billingDays,
    })
    preview.value = data
    error.value = ''
  } catch (requestError) {
    preview.value = null
    error.value = requestError.userMessage || '价格获取失败，请重试'
  } finally {
    previewing.value = false
  }
}

watch(() => props.open, async (open) => {
  if (open) {
    resetForm()
    await loadCatalog()
    refreshPreview()
  }
}, { immediate: true })

watch([() => form.productId, () => form.quantity, () => form.billingDays], () => {
  const product = selectedProduct.value
  if (product && !periods.value.includes(form.billingDays)) form.billingDays = product.billing_days
  clearTimeout(previewTimer)
  previewTimer = setTimeout(refreshPreview, 300)
})

// 换产品意味着换国家，旧城市选择必须清掉（选择器自己也会清理不可售项）。
watch(() => form.productId, () => { cityChoice.value = { cityCode: '', ipSegment: '' } })

// 客户没有登录账号时，余额/在线支付都会被后端拒绝，这里直接把选项禁掉并说明原因。
watch(hasAccount, (value) => {
  if (!value) form.paymentMethod = 'contact_manager'
}, { immediate: true })

function close() {
  if (submitting.value) return
  clearTimeout(previewTimer)
  emit('close')
}

async function submit() {
  if (!canSubmit.value || !props.customer) return
  if (form.quantity < 1 || form.quantity > 100) { error.value = '数量需要在 1 到 100 之间'; return }
  submitting.value = true
  error.value = ''
  try {
    const payload = {
      customer_id: props.customer.id,
      product_id: form.productId,
      region_id: selectedProduct.value?.region_id ?? null,
      quantity: Number(form.quantity),
      billing_days: Number(form.billingDays),
      payment_method: form.paymentMethod,
      remark: form.remark.trim() || null,
      idempotency_key: idempotencyKey,
      city_code: cityChoice.value.cityCode || null,
      ip_segment: cityChoice.value.ipSegment || null,
    }
    if (discountApplied.value) payload.discount_percent = Number(form.discountPercent)
    const { data } = await createOnBehalfOrder(payload)
    emit('created', data)
    emit('close')
  } catch (requestError) {
    error.value = requestError.userMessage || requestError.message || '下单失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}

function onKeydown(event) {
  if (event.key === 'Escape') close()
}
watch(() => props.open, (open) => {
  if (typeof window === 'undefined') return
  if (open) window.addEventListener('keydown', onKeydown)
  else window.removeEventListener('keydown', onKeydown)
})
onBeforeUnmount(() => {
  if (typeof window !== 'undefined') window.removeEventListener('keydown', onKeydown)
  clearTimeout(previewTimer)
})
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="obf-backdrop" @click.self="close">
      <section class="obf-card" role="dialog" aria-modal="true" aria-label="代客下单">
        <header class="obf-head">
          <div>
            <p>代客下单</p>
            <h2>{{ customer?.name || '未选择客户' }}</h2>
            <small>{{ customer?.phone || customer?.email || '未填写联系方式' }}<template v-if="!hasAccount"> · 该客户没有登录账号</template></small>
          </div>
          <button type="button" class="obf-close" aria-label="关闭" :disabled="submitting" @click="close"><X :size="18" /></button>
        </header>

        <div v-if="loading" class="obf-loading"><Loader2 :size="18" class="spin" /> 正在加载产品…</div>
        <form v-else class="obf-body" @submit.prevent="submit">
          <label class="obf-field obf-wide">产品
            <select v-model.number="form.productId" required>
              <option v-for="item in products" :key="item.id" :value="item.id" :disabled="!item.is_in_stock">
                {{ item.region }} · {{ item.name }}{{ item.is_in_stock ? '' : '（暂时无货）' }}
              </option>
            </select>
          </label>

          <label class="obf-field">周期
            <select v-model.number="form.billingDays">
              <option v-for="days in periods" :key="days" :value="days">{{ days }} 天</option>
            </select>
          </label>

          <label class="obf-field">数量
            <input v-model.number="form.quantity" type="number" min="1" max="100" step="1" required />
          </label>

          <label class="obf-field obf-wide">支付方式
            <select v-model="form.paymentMethod">
              <option value="contact_manager">联系经理 / 线下收款</option>
              <option value="online" :disabled="!hasAccount">客户在线支付{{ hasAccount ? '' : '（需登录账号）' }}</option>
              <option value="balance" :disabled="!hasAccount">客户余额支付{{ hasAccount ? '' : '（需登录账号）' }}</option>
            </select>
          </label>

          <label v-if="canDiscount" class="obf-field obf-wide">折扣（%）
            <input v-model.number="form.discountPercent" type="number" min="0" :max="maxDiscountPercent" step="1" />
            <small class="obf-hint">最多 {{ maxDiscountPercent }}%（即 {{ (100 - maxDiscountPercent) / 10 }} 折）；只有管理员能设置折扣，销售按标价开单。</small>
          </label>

          <div v-if="selectedProduct" class="obf-wide">
            <CitySegmentPicker v-model="cityChoice" :product-id="selectedProduct.id" :disabled="submitting" />
          </div>

          <label class="obf-field obf-wide">备注
            <textarea v-model="form.remark" maxlength="1000" rows="2" placeholder="线路要求、交付备注（可选）" />
          </label>

          <div class="obf-price obf-wide">
            <span>标价合计</span>
            <strong v-if="previewing">计算中…</strong>
            <strong v-else-if="preview">{{ money(preview.total_amount) }}</strong>
            <strong v-else>—</strong>
            <em v-if="discountApplied && discountedTotal !== null">折后 {{ money(discountedTotal) }}</em>
          </div>

          <p v-if="error" class="obf-error" role="alert">{{ error }}</p>

          <footer class="obf-actions">
            <button type="button" class="obf-cancel" :disabled="submitting" @click="close">取消</button>
            <button type="submit" class="obf-submit" :disabled="!canSubmit">
              <Loader2 v-if="submitting" :size="15" class="spin" /><ShoppingCart v-else :size="15" />
              {{ submitting ? '提交中…' : '创建订单' }}
            </button>
          </footer>
        </form>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.obf-backdrop{position:fixed;inset:0;z-index:80;display:grid;place-items:center;padding:18px;background:#0f231d80}
.obf-card{width:min(100%,560px);max-height:calc(100vh - 36px);overflow:auto;background:#fff;border:1px solid #dfe8e2;border-radius:8px;box-shadow:0 20px 60px #142d2233}
.obf-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;padding:18px 20px 14px;border-bottom:1px solid #e9eeeb}
.obf-head p{margin:0;color:#6d8177;font-size:11px;letter-spacing:.04em}
.obf-head h2{margin:4px 0 0;font-size:18px;color:#243b32}
.obf-head small{display:block;margin-top:4px;color:#7c8d85;font-size:11px}
.obf-close{width:32px;height:32px;display:grid;place-items:center;border-radius:5px;color:#5f7469}
.obf-close:hover{background:#eff4f1}
.obf-loading{display:flex;align-items:center;gap:8px;padding:28px 20px;color:#5f7469;font-size:12px}
.obf-body{display:grid;grid-template-columns:1fr 1fr;gap:13px;padding:16px 20px 20px}
.obf-wide{grid-column:1/-1}
.obf-field{display:grid;gap:6px;min-width:0}
.obf-field>span,.obf-field{color:#52685f;font-size:11px;font-weight:650}
.obf-field select,.obf-field input,.obf-field textarea{width:100%;height:38px;padding:0 9px;border:1px solid #d8e2dc;border-radius:5px;background:#fff;color:#20332d;font:inherit;font-size:12px}
.obf-field textarea{height:auto;min-height:58px;padding:8px 9px;resize:vertical}
.obf-field select:focus,.obf-field input:focus,.obf-field textarea:focus{outline:none;border-color:#449377;box-shadow:0 0 0 3px #44937720}
.obf-field select:disabled,.obf-field input:disabled{opacity:.55}
.obf-hint{margin:0;color:#7d8f86;font-size:10px;font-weight:400;line-height:1.6}
.obf-price{display:flex;align-items:center;gap:12px;padding:11px 12px;background:#f5f9f7;border:1px solid #e3ece7;border-radius:6px}
.obf-price span{color:#657b71;font-size:11px}
.obf-price strong{color:#176e55;font-size:17px;font-variant-numeric:tabular-nums}
.obf-price em{margin-left:auto;font-style:normal;color:#a2662f;font-size:12px}
.obf-error{margin:0;padding:9px 11px;border-radius:5px;background:#fff3ef;border:1px solid #e8c5bb;color:#8d4235;font-size:11px}
.obf-actions{display:flex;justify-content:flex-end;gap:9px;margin-top:2px}
.obf-cancel,.obf-submit{min-height:38px;padding:0 16px;display:inline-flex;align-items:center;gap:7px;border-radius:5px;font-size:12px;font-weight:650}
.obf-cancel{border:1px solid #d6e1db;background:#fff;color:#476258}
.obf-cancel:hover{background:#f1f6f3}
.obf-submit{border:1px solid #176e55;background:#176e55;color:#fff}
.obf-submit:hover{background:#125b46}
.obf-submit:disabled,.obf-cancel:disabled{opacity:.5;cursor:not-allowed}
.spin{animation:obf-spin .8s linear infinite}
@keyframes obf-spin{to{transform:rotate(360deg)}}
@media(max-width:640px){
  .obf-body{grid-template-columns:1fr}
  .obf-card{max-height:calc(100vh - 12px)}
}
@media(prefers-reduced-motion:reduce){.spin{animation:none}}
</style>
