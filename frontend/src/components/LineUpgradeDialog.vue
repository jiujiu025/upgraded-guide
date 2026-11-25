<script setup>
// 线路升级弹窗：静态住宅 → 视频/社媒、直播，只补差价，到期时间不变。
// 差价全部由后端算（同国家、同周期、目标单价 − 当前单价），前端只展示，避免两套算法。
// 客户中心、销售工作台、管理员后台三处共用：workspace 决定调哪组接口，权限仍由后端兜底。
import { computed, ref, watch } from 'vue'
import { ArrowUpCircle, Loader2, X } from 'lucide-vue-next'
import {
  getCustomerLineUpgradeOptions,
  getLineUpgradeOptions,
  getSalesLineUpgradeOptions,
  upgradeCustomerLine,
  upgradeLine,
  upgradeSalesLine,
} from '../api'

const props = defineProps({
  open: { type: Boolean, default: false },
  line: { type: Object, default: null },
  // client / sales / admin
  workspace: { type: String, default: 'admin' },
})
const emit = defineEmits(['close', 'created'])

// 三个入口共用同一个弹窗，按 workspace 选接口。这里用函数惰性取，
// 而不是在 setup 顶层拼对象——顶层直接读具名导入会让只 mock 了部分接口的
// 测试在未打开弹窗时也报错。
function apiFor(workspace) {
  if (workspace === 'client') {
    return { options: getCustomerLineUpgradeOptions, upgrade: upgradeCustomerLine }
  }
  if (workspace === 'sales') {
    return { options: getSalesLineUpgradeOptions, upgrade: upgradeSalesLine }
  }
  return { options: getLineUpgradeOptions, upgrade: upgradeLine }
}

const loading = ref(false)
const saving = ref(false)
const error = ref('')
// 幂等键按「线路 + 目标 + 付款方式」复用：双击或超时重试落到同一张单上，成功后才换新键。
const attempt = ref(null)
const data = ref(null)
const selected = ref('')
const paymentMethod = ref('contact_manager')

const api = computed(() => apiFor(props.workspace))
const isSales = computed(() => props.workspace === 'sales')
const options = computed(() => data.value?.options || [])
const current = computed(() => data.value?.current || null)
const reason = computed(() => data.value?.reason || '')
const chosen = computed(() => options.value.find((item) => item.category === selected.value) || null)
// 业务口径：客户口中的「视频专线」就是社媒运营专线。客户侧优先显示前者，
// 小字保留后者，避免和购买页的产品名对不上。
const displayLabel = (item) => {
  if (item.category === 'social_media' && props.workspace !== 'admin') return '视频专线'
  return item.label
}
const displayNote = (item) => {
  if (item.category === 'social_media' && props.workspace !== 'admin') return `社媒运营专线 · ${item.product_name}`
  return item.product_name
}

function newKey() {
  if (crypto.randomUUID) return crypto.randomUUID()
  return Array.from(crypto.getRandomValues(new Uint8Array(18)), (v) => v.toString(16).padStart(2, '0')).join('')
}

async function load() {
  if (!props.line) return
  loading.value = true
  error.value = ''
  data.value = null
  selected.value = ''
  attempt.value = null
  // 销售代客户升级只能「联系经理确认」，不能替客户动余额。
  paymentMethod.value = isSales.value ? 'contact_manager' : 'contact_manager'
  try {
    const response = await api.value.options(props.line.id)
    data.value = response.data
    selected.value = response.data.options?.[0]?.category || ''
  } catch (err) {
    error.value = err?.userMessage || '读取升级选项失败'
  } finally {
    loading.value = false
  }
}

watch(() => [props.open, props.line?.id], ([open]) => { if (open) load() })

async function submit() {
  if (!chosen.value) return
  saving.value = true
  error.value = ''
  try {
    const body = { target_category: chosen.value.category, payment_method: paymentMethod.value }
    const fingerprint = JSON.stringify([props.line.id, body])
    if (!attempt.value || attempt.value.fingerprint !== fingerprint) {
      attempt.value = { fingerprint, key: newKey() }
    }
    const response = await api.value.upgrade(props.line.id, { ...body, idempotency_key: attempt.value.key })
    attempt.value = null
    const order = response.data
    emit('created', {
      ...chosen.value,
      label: displayLabel(chosen.value),
      order,
      payment_method: paymentMethod.value,
      // 客户选了余额/在线支付时，订单落在 pending_payment，要去钱包把差价付掉才生效。
      needsPayment: !isSales.value && paymentMethod.value !== 'contact_manager'
        && order?.status === 'pending_payment',
    })
    emit('close')
  } catch (err) {
    error.value = err?.userMessage || '创建升级单失败'
  } finally {
    saving.value = false
  }
}

function money(value) {
  return value == null ? '—' : `¥${Number(value).toFixed(2)}`
}
function formatDate(value) {
  return value ? new Date(value).toLocaleDateString('zh-CN') : '—'
}
</script>

<template>
  <div v-if="open" class="lup-backdrop" @click.self="emit('close')">
    <div class="lup-card" role="dialog" aria-modal="true" aria-label="线路升级">
      <div class="lup-head">
        <div>
          <p>线路升级</p>
          <h2 v-if="workspace === 'admin'">{{ line?.customer_name }} · {{ line?.product_name }}</h2>
          <h2 v-else>{{ line?.line_name }} · {{ line?.product_type }}</h2>
          <small>只补差价，到期时间不变（{{ formatDate(line?.expires_at) }}）</small>
        </div>
        <button class="lup-close" type="button" aria-label="关闭" @click="emit('close')"><X :size="18" /></button>
      </div>

      <div v-if="loading" class="lup-loading"><Loader2 :size="16" class="spin" />读取可升级的产品线…</div>

      <div v-else class="lup-body">
        <div v-if="error" class="lup-error">{{ error }}</div>

        <div v-if="current" class="lup-current">
          <span>当前：{{ current.label }}</span>
          <b>{{ money(current.unit_price) }} / {{ current.billing_days }} 天</b>
        </div>

        <div v-if="!options.length" class="lup-empty">
          {{ reason || '这条线路没有可升级的产品线。可能是该国家下没建更高档的 SKU，或当前已经是最贵的一档。' }}
        </div>

        <template v-else>
          <div class="lup-options">
            <button v-for="item in options" :key="item.category" type="button" class="lup-option" :data-active="selected === item.category" @click="selected = item.category">
              <span><b>{{ displayLabel(item) }}</b><small>{{ displayNote(item) }}</small></span>
              <span class="lup-diff">补差价 <b>{{ money(item.diff) }}</b></span>
            </button>
          </div>

          <label v-if="!isSales" class="lup-field"><span>付款方式</span><select v-model="paymentMethod"><option value="contact_manager">线下转账 / 对公</option><option value="balance">客户余额</option><option value="online">在线支付</option></select></label>
          <p v-else class="lup-sales-note">销售代客户升级只能选「联系经理确认」，不能动用客户余额。</p>

          <p class="lup-hint">差价 =（目标单价 − 当前单价）× 原周期倍数，按整周期折算，不按剩余天数；升级后到期日与当前线路完全一致。原住宅 IP 保留，视频/直播只加一层中转。</p>
          <p v-if="!isSales && paymentMethod !== 'contact_manager'" class="lup-hint lup-hint-pay">提交后生成待支付升级单，到账户余额付清差价后线路立即切换；未付款不会改动现有线路。</p>
        </template>
      </div>

      <div v-if="!loading" class="lup-foot">
        <button type="button" class="lup-cancel" @click="emit('close')">取消</button>
        <button type="button" class="lup-submit" :disabled="saving || !chosen" @click="submit">
          <ArrowUpCircle v-if="!saving" :size="15" /><Loader2 v-else :size="15" class="spin" />
          {{ saving ? '提交中…' : chosen ? `创建升级单 · 补 ${money(chosen.diff)}` : '请选择目标产品线' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.lup-backdrop{position:fixed;inset:0;z-index:80;display:grid;place-items:center;padding:18px;background:#0f231d80}
.lup-card{width:min(100%,520px);max-height:calc(100vh - 36px);overflow:auto;background:#fff;border:1px solid #dfe8e2;border-radius:8px;box-shadow:0 20px 60px #142d2233}
.lup-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;padding:18px 20px 14px;border-bottom:1px solid #e9eeeb}
.lup-head p{margin:0;color:#6d8177;font-size:11px;letter-spacing:.04em}
.lup-head h2{margin:4px 0 0;font-size:17px;color:#243b32}
.lup-head small{display:block;margin-top:4px;color:#7c8d85;font-size:11px}
.lup-close{width:32px;height:32px;display:grid;place-items:center;border-radius:5px;color:#5f7469}
.lup-close:hover{background:#eff4f1}
.lup-loading{display:flex;align-items:center;gap:8px;padding:28px 20px;color:#5f7469;font-size:12px}
.lup-body{display:grid;gap:12px;padding:16px 20px}
.lup-error{padding:9px 11px;border:1px solid #e6c2bc;border-radius:5px;background:#fff2ef;color:#8e3e32;font-size:11px}
.lup-current{display:flex;align-items:center;justify-content:space-between;padding:10px 12px;border:1px solid #e3ece7;border-radius:6px;background:#f5f9f7;font-size:12px;color:#52685f}
.lup-current b{color:#243b32}
.lup-options{display:grid;gap:8px}
.lup-option{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:11px 12px;border:1px solid #d8e2dc;border-radius:6px;background:#fff;text-align:left;transition:border-color .16s ease,background .16s ease}
.lup-option>span{display:grid;gap:3px}
.lup-option b{color:#243b32;font-size:12px}
.lup-option small{color:#7d8f86;font-size:10px}
.lup-option:hover{border-color:#9ec9b8}
.lup-option[data-active="true"]{border-color:#167c62;background:#eaf7f1;box-shadow:inset 0 0 0 1px #167c62}
.lup-diff{color:#6d8177;font-size:11px;white-space:nowrap}
.lup-diff b{color:#10684f;font-size:14px}
.lup-field{display:grid;gap:5px;font-size:11px;font-weight:650;color:#52685f}
.lup-field select{width:100%;height:38px;padding:0 9px;border:1px solid #d8e2dc;border-radius:5px;background:#fff;color:#20332d;font:inherit;font-size:12px}
.lup-hint{margin:0;color:#7d8f86;font-size:10px;line-height:1.6}
.lup-hint-pay{color:#10684f;background:#f0f8f4;border:1px solid #d9ece2;border-radius:5px;padding:8px 10px}
.lup-sales-note{margin:0;padding:9px 11px;border:1px solid #e3ece7;border-radius:5px;background:#f5f9f7;color:#52685f;font-size:11px;line-height:1.6}
.lup-empty{padding:16px;border:1px dashed #d8e2dc;border-radius:6px;color:#7d8f86;font-size:11px;line-height:1.6;text-align:center}
.lup-foot{display:flex;justify-content:flex-end;gap:9px;padding:0 20px 18px}
.lup-cancel{padding:0 14px;min-height:38px;border:1px solid #d8e2dc;border-radius:5px;color:#52685f;font-size:12px}
.lup-submit{display:inline-flex;align-items:center;gap:7px;padding:0 15px;min-height:38px;border-radius:5px;background:#167c62;color:#fff;font-size:12px;font-weight:650}
.lup-submit:hover:not(:disabled){background:#10684f}
.lup-submit:disabled{opacity:.5;cursor:not-allowed}
.spin{animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
