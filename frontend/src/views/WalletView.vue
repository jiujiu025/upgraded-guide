<script setup>
import BrandLogo from '../components/BrandLogo.vue'
import { computed, onMounted, ref } from 'vue'
import { ArrowDownToLine, ArrowUpRight, CircleDollarSign, LogOut, Menu, RefreshCw, ShieldCheck, X } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import { createOnlineRecharge, getWallet, logout as endSession, payWalletOrderOnline, payWalletOrderWithBalance } from '../api'
import { session } from '../api/session'

const route = useRoute()
const router = useRouter()
const user = computed(() => session.user || {})
const isSales = computed(() => user.value.role === 'sales')
const loading = ref(true)
const busy = ref('')
const mobileNav = ref(false)
const error = ref('')
const notice = ref('')
const wallet = ref(null)
const amount = ref('')
const paymentType = ref('')
const nav = computed(() => isSales.value
  ? [['销售概览', '/sales'], ['我的钱包', '/sales/wallet'], ['安全中心', '/sales/security']]
  : [['客户首页', '/client'], ['账户余额', '/client/wallet'], ['我的订单', '/client/orders'], ['我的线路', '/client/lines'], ['安全中心', '/client/security']])
const money = (cents) => `¥${(Number(cents || 0) / 100).toFixed(2)}`
const when = (value) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
function idempotencyKey() { return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}` }
function centsFromInput() {
  const text = String(amount.value).trim()
  if (!/^\d{1,8}(\.\d{1,2})?$/.test(text)) return null
  const [whole, fraction = ''] = text.split('.')
  return Number(whole) * 100 + Number((fraction + '00').slice(0, 2))
}
async function load() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await getWallet()
    wallet.value = data
    paymentType.value = data.payment_options.channel_types[0] || ''
  } catch (err) { error.value = err?.userMessage || '钱包数据加载失败' }
  finally { loading.value = false }
}
async function startRecharge() {
  const cents = centsFromInput()
  if (!cents) { error.value = '请输入有效充值金额，最多保留两位小数'; return }
  if (cents < wallet.value.payment_options.min_amount_cents || cents > wallet.value.payment_options.max_amount_cents) { error.value = '充值金额超出单笔限额'; return }
  if (!paymentType.value) { error.value = '支付通道没有配置可用方式'; return }
  busy.value = 'recharge'
  error.value = ''
  try {
    const { data } = await createOnlineRecharge({ amount_cents: cents, payment_type: paymentType.value, idempotency_key: idempotencyKey() })
    window.location.assign(data.payment_url)
  } catch (err) { error.value = err?.userMessage || '创建在线充值失败，请稍后重试' }
  finally { busy.value = '' }
}
async function payOrder(order, method) {
  if (busy.value) return
  busy.value = `${method}:${order.id}`
  error.value = ''
  try {
    if (method === 'balance') {
      await payWalletOrderWithBalance(order.id, { idempotency_key: idempotencyKey() })
      notice.value = '订单已通过余额支付，线路仍需按现有流程审核和交付'
      await load()
    } else {
      if (!paymentType.value) throw new Error('支付通道没有配置可用方式')
      const { data } = await payWalletOrderOnline(order.id, { payment_type: paymentType.value, idempotency_key: idempotencyKey() })
      window.location.assign(data.payment_url)
    }
  } catch (err) { error.value = err?.userMessage || err?.message || '订单支付失败' }
  finally { busy.value = '' }
}
async function logout() { await endSession(); router.replace(isSales.value ? '/sales/login' : '/login') }
onMounted(load)
</script>

<template>
  <div class="workspace-shell wallet-shell">
    <aside class="workspace-sidebar" :class="{ open: mobileNav }"><div class="workspace-brand"><BrandLogo eager /><button aria-label="关闭导航" @click="mobileNav = false"><X :size="18" /></button></div><p class="workspace-role">{{ isSales ? '销售工作台' : '客户中心' }}</p><nav><button v-for="[label, path] in nav" :key="path" :class="{ active: route.path === path }" @click="router.push(path); mobileNav = false"><CircleDollarSign v-if="path.includes('wallet')" :size="17" /><ArrowUpRight v-else :size="17" />{{ label }}</button></nav><div class="workspace-user"><span>{{ user.display_name || user.username }}</span><small>{{ isSales ? '销售 / 代理' : '客户账号' }}</small><button @click="logout"><LogOut :size="15" />退出登录</button></div></aside>
    <div class="workspace-main">
      <header class="workspace-header wallet-header"><button class="workspace-menu" aria-label="打开导航" @click="mobileNav = true"><Menu :size="20" /></button><div><h1>{{ isSales ? '我的钱包' : '账户余额' }}</h1><p>余额、充值记录与待支付订单</p></div><button class="icon-button" aria-label="刷新钱包" @click="load"><RefreshCw :size="16" /></button></header>
      <main class="workspace-content wallet-content">
        <div v-if="notice" class="wallet-notice" role="status">{{ notice }}</div>
        <div v-if="error" class="wallet-error" role="alert">{{ error }}</div>
        <div v-if="loading" class="wallet-loading"><span></span><span></span></div>
        <template v-else-if="wallet">
          <section class="wallet-overview"><div><span>可用余额</span><strong>{{ money(wallet.balance_cents) }}</strong><small>{{ isSales ? '销售个人钱包' : '客户个人钱包' }} · CNY</small></div><div class="wallet-mark"><CircleDollarSign :size="25" /></div></section>
          <section class="wallet-section recharge-panel"><div class="wallet-section-title"><div><h2>在线充值</h2><p>到账以支付平台异步通知验签结果为准，支付返回页不会直接增加余额。</p></div><span class="wallet-channel-state" :data-active="wallet.payment_options.online_enabled">{{ wallet.payment_options.online_enabled ? '通道可用' : '通道未启用' }}</span></div>
            <div v-if="wallet.payment_options.online_enabled" class="wallet-recharge-form"><label><span>充值金额（元）</span><div class="wallet-yuan"><i>¥</i><input v-model="amount" inputmode="decimal" placeholder="输入金额" maxlength="11" /></div></label><label><span>支付方式</span><select v-model="paymentType"><option v-for="item in wallet.payment_options.channel_types" :key="item" :value="item">{{ item }}</option></select></label><button class="wallet-primary" :disabled="busy === 'recharge'" @click="startRecharge"><ArrowDownToLine :size="16" />{{ busy === 'recharge' ? '正在创建…' : '前往支付' }}</button></div>
            <div v-else class="wallet-disabled"><ShieldCheck :size="17" /><span>在线充值暂未开放。管理员完成商户配置和沙箱验收后，才会显示支付入口。</span></div>
          </section>
          <section v-if="wallet.pending_orders.length" class="wallet-section"><div class="wallet-section-title"><div><h2>待支付订单</h2><p>余额付款成功只更新本地收款状态，不代表线路已交付。</p></div><span>{{ wallet.pending_orders.length }} 笔</span></div><div class="pending-orders"><article v-for="order in wallet.pending_orders" :key="order.id"><div><b>{{ order.product_name }}</b><small>{{ order.order_no }} · {{ when(order.created_at) }}</small></div><strong>{{ money(order.total_cents) }}</strong><button class="wallet-primary" :disabled="!!busy" @click="payOrder(order, 'balance')">{{ busy === `balance:${order.id}` ? '处理中' : '余额支付' }}</button><button v-if="order.can_pay_online && wallet.payment_options.online_enabled" class="wallet-secondary" :disabled="!!busy" @click="payOrder(order, 'online')">在线支付</button></article></div></section>
          <section class="wallet-section"><div class="wallet-section-title"><div><h2>钱包流水</h2><p>记录入账、订单扣款与退款。</p></div><span>{{ wallet.transactions.length }} 条</span></div><div v-if="wallet.transactions.length" class="wallet-table-wrap"><table><thead><tr><th>时间</th><th>类型</th><th>说明</th><th>变动</th><th>变动后余额</th></tr></thead><tbody><tr v-for="item in wallet.transactions" :key="item.id"><td>{{ when(item.created_at) }}</td><td>{{ ({ offline_recharge: '线下充值', online_recharge: '在线充值', order_payment: '订单付款', order_refund: '订单退款', opening_balance: '期初余额' })[item.type] || item.type }}</td><td>{{ item.remark || (item.order_id ? `订单 #${item.order_id}` : '—') }}</td><td :class="{ positive: item.amount_cents > 0, negative: item.amount_cents < 0 }">{{ item.amount_cents > 0 ? '+' : '' }}{{ money(item.amount_cents) }}</td><td>{{ money(item.balance_after_cents) }}</td></tr></tbody></table></div><div v-else class="wallet-empty">暂无钱包流水</div></section>
          <section class="wallet-section"><div class="wallet-section-title"><div><h2>在线充值记录</h2><p>支付状态由服务端回调和对账状态更新。</p></div><span>{{ wallet.payments.length }} 条</span></div><div v-if="wallet.payments.length" class="wallet-table-wrap"><table><thead><tr><th>充值单号</th><th>金额</th><th>类型</th><th>状态</th><th>创建时间</th></tr></thead><tbody><tr v-for="item in wallet.payments" :key="item.id"><td>{{ item.out_trade_no }}</td><td>{{ money(item.amount_cents) }}</td><td>{{ item.purpose === 'wallet_recharge' ? '钱包充值' : '订单付款' }}</td><td>{{ ({ pending: '待支付', paid: '已到账', failed: '失败', manual_review: '待核查', refunded: '已退款' })[item.status] || item.status }}</td><td>{{ when(item.created_at) }}</td></tr></tbody></table></div><div v-else class="wallet-empty">暂无在线支付记录</div></section>
        </template>
      </main>
    </div>
  </div>
</template>

<style scoped>
.wallet-shell{background:#f2f5f3;color:#20332b}.wallet-shell .workspace-sidebar{background:#fff;color:#20342d;border-right:1px solid #dce5e1}.wallet-shell .workspace-sidebar nav button{color:#526860}.wallet-shell .workspace-sidebar nav button.active,.wallet-shell .workspace-sidebar nav button:hover{background:#e7f4ef;color:#176b58}.wallet-header{background:#fff;justify-content:flex-start}.wallet-header>div{display:grid;gap:3px}.wallet-header p{margin:0;color:#788a82;font-size:12px}.icon-button{margin-left:auto;width:36px;height:36px;display:grid;place-items:center;border:1px solid #d4e0da;border-radius:5px;background:#fff;color:#42665a}.wallet-content{max-width:1200px}.wallet-overview{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;padding:20px 22px;border:1px solid #dce5e1;border-radius:6px;background:#fff}.wallet-overview>div:first-child{display:grid;gap:3px}.wallet-overview span{color:#73847c;font-size:11px}.wallet-overview strong{color:#1d644c;font-size:29px;font-variant-numeric:tabular-nums}.wallet-overview small{color:#839189;font-size:10px}.wallet-mark{width:48px;height:48px;display:grid;place-items:center;border-radius:50%;background:#e6f3ec;color:#287457}.wallet-section{margin-bottom:14px;padding:16px;border:1px solid #dce5e1;border-radius:6px;background:#fff}.wallet-section-title{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:13px}.wallet-section-title h2{margin:0;color:#283c32;font-size:15px}.wallet-section-title p{margin:4px 0 0;color:#76877f;font-size:10px;line-height:1.5}.wallet-section-title>span{color:#788981;font-size:10px;white-space:nowrap}.wallet-channel-state{padding:4px 7px;border-radius:4px;background:#fff1d8;color:#835b1b;font-weight:650}.wallet-channel-state[data-active="true"]{background:#e2f3ea;color:#286b52}.wallet-recharge-form{display:grid;grid-template-columns:1fr 1fr auto;align-items:end;gap:10px}.wallet-recharge-form label{display:grid;gap:5px;color:#61756c;font-size:10px}.wallet-recharge-form input,.wallet-recharge-form select{height:38px;width:100%;padding:0 10px;border:1px solid #cad8d2;border-radius:5px;background:white;color:#283a32;font-size:12px}.wallet-yuan{position:relative}.wallet-yuan i{position:absolute;left:10px;top:9px;color:#73847b;font-style:normal}.wallet-yuan input{padding-left:25px}.wallet-primary,.wallet-secondary{min-height:38px;display:inline-flex;align-items:center;justify-content:center;gap:7px;padding:8px 12px;border-radius:5px;font-size:11px;font-weight:700;white-space:nowrap}.wallet-primary{background:#167c62;color:#fff}.wallet-primary:hover{background:#10684f}.wallet-secondary{border:1px solid #c9d8d1;color:#345b4f;background:#fff}.wallet-primary:disabled,.wallet-secondary:disabled{opacity:.5;cursor:not-allowed}.wallet-disabled{display:flex;align-items:flex-start;gap:8px;padding:11px;border-radius:5px;background:#f5f7f5;color:#74837b;font-size:10px;line-height:1.5}.wallet-disabled svg{flex:none;color:#547265}.pending-orders{display:grid}.pending-orders article{display:grid;grid-template-columns:minmax(0,1fr) auto auto auto;align-items:center;gap:10px;min-height:62px;border-top:1px solid #e6ece8}.pending-orders article>div{display:grid;gap:3px}.pending-orders b{color:#2d4237;font-size:11px}.pending-orders small{color:#829088;font-size:9px}.pending-orders strong{color:#245f48;font-size:12px;font-variant-numeric:tabular-nums}.wallet-table-wrap{width:100%;overflow:auto;border:1px solid #e1e9e5;border-radius:5px}table{width:100%;border-collapse:collapse;text-align:left;white-space:nowrap;font-size:10px}th{height:36px;padding:0 10px;background:#f0f5f2;color:#657970;font-size:10px;font-weight:650}td{height:43px;padding:6px 10px;border-top:1px solid #e7eeea;color:#354a40}td.positive{color:#20724f;font-variant-numeric:tabular-nums}td.negative{color:#9b5142;font-variant-numeric:tabular-nums}.wallet-empty{display:grid;place-items:center;min-height:76px;color:#819088;font-size:11px}.wallet-notice,.wallet-error{margin-bottom:12px;padding:10px 12px;border:1px solid #b9dcc9;border-radius:5px;background:#eef8f1;color:#236648;font-size:11px}.wallet-error{border-color:#e8c7c0;background:#fff2ef;color:#874339}.wallet-loading{display:grid;grid-template-columns:1fr 1fr;gap:12px}.wallet-loading span{height:130px;border-radius:5px;background:#e3ebe7;animation:pulse 1s ease-in-out infinite alternate}@keyframes pulse{to{background:#f2f5f3}}
@media(max-width:800px){.wallet-shell{display:block}.wallet-shell .workspace-sidebar{position:fixed}.wallet-shell .workspace-header{height:76px;padding:0 18px}}
@media(max-width:780px){.wallet-content{padding:18px}.wallet-recharge-form{grid-template-columns:1fr 1fr}.wallet-recharge-form button{grid-column:1/-1}.pending-orders article{grid-template-columns:minmax(0,1fr) auto}.pending-orders article button{grid-row:2}.pending-orders article button:first-of-type{grid-column:1}.pending-orders article button:last-of-type{grid-column:2}}
@media(max-width:520px){.wallet-content{padding:14px}.wallet-header{padding:0 14px}.wallet-header h1{font-size:19px}.wallet-overview{padding:16px}.wallet-overview strong{font-size:25px}.wallet-recharge-form{grid-template-columns:1fr}.wallet-recharge-form button{grid-column:auto}.wallet-section{padding:12px}.pending-orders article{gap:7px}.pending-orders article button{font-size:10px;padding:7px 8px}}
@media(prefers-reduced-motion:reduce){.wallet-loading span{animation:none}}
</style>
