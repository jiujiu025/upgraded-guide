<script setup>
import BrandLogo from '../components/BrandLogo.vue'
import { computed, onMounted, reactive, ref } from 'vue'
import { AlertTriangle, ArrowDownToLine, Check, CircleDollarSign, ClipboardCheck, CreditCard, Gauge, LogOut, Menu, PackageSearch, RefreshCw, Search, ShieldCheck, Users, X } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import { checkPaymentApi, createOfflineRecharge, getFinanceSummary, getPaymentConfig, logout as endSession, recordOrderRefund, searchFinanceAccounts, updatePaymentConfig } from '../api'
import { session } from '../api/session'
import { getAdminNavigation } from '../navigation/adminNavigation'

const route = useRoute()
const router = useRouter()
const user = computed(() => session.user || {})
const loading = ref(true)
const saving = ref(false)
const testing = ref(false)
const error = ref('')
const notice = ref('')
const tab = ref('overview')
const summary = ref(null)
const accountQuery = ref('')
const accountResults = ref([])
const selectedAccount = ref(null)
const accountSearching = ref(false)
const rechargeAmount = ref('')
const rechargeRemark = ref('')
const rechargeConfirm = ref(false)
const rechargeAttempt = ref(null)
const refundTarget = ref(null)
const refundReason = ref('')
const refundReference = ref('')
const refundConfirmed = ref(false)
const refundError = ref('')
const mobileNav = ref(false)
const paymentStatus = ref('')
const configLoaded = ref(false)
const form = reactive({ merchant_id: '', secret: '', channel_types: 'alipay,wxpay', min_amount: '1.00', max_amount: '100000.00', online_enabled: false })
const dateRange = reactive({ from: '', to: '' })
const apiHealth = ref(null)
const nav = computed(() => getAdminNavigation(user.value.role))

const canConfigurePayment = computed(() => user.value.role === 'super_admin')
const currentTotals = computed(() => summary.value?.today || {})
const totalLabel = (cents) => cents == null ? '¥0.00' : `¥${(Number(cents) / 100).toFixed(2)}`
const dateLabel = (value) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
const statusLabel = (value) => ({ paid: '已完成', pending: '待支付', failed: '失败', expired: '已过期', manual_review: '待核查', refunded: '已退款' })[value] || value
const statusTone = (value) => ['paid'].includes(value) ? 'good' : ['pending', 'manual_review'].includes(value) ? 'warn' : ['refunded', 'failed', 'expired'].includes(value) ? 'muted' : 'info'

function showNotice(message) {
  notice.value = message
  window.clearTimeout(showNotice.timer)
  showNotice.timer = window.setTimeout(() => { notice.value = '' }, 3600)
}
function userError(err, fallback) { return err?.userMessage || fallback }
function makeIdempotencyKey() { return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}` }
async function logout() { await endSession(); router.replace('/admin/login') }
async function loadSummary() {
  const params = {}
  if (dateRange.from) params.date_from = dateRange.from
  if (dateRange.to) params.date_to = dateRange.to
  summary.value = (await getFinanceSummary(params)).data
}
async function loadConfig() {
  if (!canConfigurePayment.value) return
  const { data } = await getPaymentConfig()
  form.merchant_id = data.merchant_id || ''
  form.secret = ''
  form.channel_types = (data.channel_types || []).join(',') || 'alipay,wxpay'
  form.min_amount = (data.min_amount_cents / 100).toFixed(2)
  form.max_amount = (data.max_amount_cents / 100).toFixed(2)
  form.online_enabled = data.online_enabled
  paymentStatus.value = data.secret_configured ? '支付密钥已加密保存，不会回显' : '尚未配置支付密钥'
  configLoaded.value = true
}
async function load() {
  loading.value = true
  error.value = ''
  try { await loadSummary() } catch (err) { error.value = userError(err, '财务数据暂时无法加载') }
  finally { loading.value = false }
}
async function switchTab(nextTab) {
  tab.value = nextTab
  error.value = ''
  if (nextTab === 'payment' && !configLoaded.value) {
    try { await loadConfig() } catch (err) { error.value = userError(err, '支付配置读取失败') }
  }
}
async function searchAccounts() {
  if (accountQuery.value.trim().length < 1) { accountResults.value = []; return }
  accountSearching.value = true
  error.value = ''
  try { accountResults.value = (await searchFinanceAccounts(accountQuery.value.trim())).data }
  catch (err) { error.value = userError(err, '查找账号失败') }
  finally { accountSearching.value = false }
}
function centsFromYuan(value) {
  const text = String(value).trim()
  if (!/^\d{1,8}(\.\d{1,2})?$/.test(text)) return null
  const [whole, fraction = ''] = text.split('.')
  return Number(whole) * 100 + Number((fraction + '00').slice(0, 2))
}
function beginRecharge() {
  error.value = ''
  const cents = centsFromYuan(rechargeAmount.value)
  if (!selectedAccount.value) { error.value = '请先选择充值账号'; return }
  if (!cents || cents > 10000000) { error.value = '充值金额需大于 0，且不超过 ¥100,000.00'; return }
  if (rechargeRemark.value.trim().length < 2) { error.value = '请填写线下收款凭证或备注'; return }
  const fingerprint = `${selectedAccount.value.user_id}:${cents}:${rechargeRemark.value.trim()}`
  if (!rechargeAttempt.value || rechargeAttempt.value.fingerprint !== fingerprint) {
    rechargeAttempt.value = { fingerprint, key: makeIdempotencyKey() }
  }
  rechargeConfirm.value = true
}
async function submitRecharge() {
  if (saving.value || !selectedAccount.value) return
  saving.value = true
  error.value = ''
  try {
    await createOfflineRecharge({ user_id: selectedAccount.value.user_id, amount_cents: centsFromYuan(rechargeAmount.value), idempotency_key: rechargeAttempt.value.key, remark: rechargeRemark.value.trim() })
    rechargeConfirm.value = false
    selectedAccount.value = null
    accountQuery.value = ''
    accountResults.value = []
    rechargeAmount.value = ''
    rechargeRemark.value = ''
    rechargeAttempt.value = null
    await loadSummary()
    showNotice('线下充值已入账，并写入财务流水')
  } catch (err) { error.value = userError(err, '线下充值失败，请核对账号和凭证') }
  finally { saving.value = false }
}
async function submitRefund() {
  if (!refundTarget.value || saving.value) return
  if (!refundConfirmed.value || refundReason.value.trim().length < 2) { refundError.value = '请确认退款已实际完成并填写原因'; return }
  if (refundTarget.value.channel !== 'balance' && refundReference.value.trim().length < 2) { refundError.value = '请填写外部退款凭证号'; return }
  saving.value = true
  refundError.value = ''
  try {
    await recordOrderRefund(refundTarget.value.order_id, {
      refund_already_completed: true, reason: refundReason.value.trim(),
      external_reference: refundReference.value.trim() || null,
    })
    closeRefund()
    await loadSummary()
    showNotice('退款完成已登记，销售佣金已冲销')
  } catch (err) { refundError.value = userError(err, '退款登记失败') }
  finally { saving.value = false }
}
function openRefund(item) {
  refundTarget.value = item
  refundReason.value = ''
  refundReference.value = ''
  refundConfirmed.value = false
  refundError.value = ''
}
function closeRefund() {
  if (saving.value) return
  refundTarget.value = null
  refundReason.value = ''
  refundReference.value = ''
  refundConfirmed.value = false
  refundError.value = ''
}
async function saveConfig() {
  if (saving.value) return
  const minimum = centsFromYuan(form.min_amount)
  const maximum = centsFromYuan(form.max_amount)
  const channels = form.channel_types.split(',').map((value) => value.trim()).filter(Boolean)
  if (!minimum || !maximum || maximum < minimum || !channels.length) { error.value = '请检查支付方式和金额范围'; return }
  saving.value = true
  error.value = ''
  try {
    const payload = {
      merchant_id: form.merchant_id.trim(), secret: form.secret || null, channel_types: channels,
      min_amount_cents: minimum, max_amount_cents: maximum,
      online_enabled: form.online_enabled,
    }
    await updatePaymentConfig(payload)
    form.secret = ''
    await loadConfig()
    showNotice('支付配置已保存')
  } catch (err) { error.value = userError(err, '支付配置保存失败') }
  finally { saving.value = false }
}
async function testPaymentApi() {
  testing.value = true
  error.value = ''
  try { apiHealth.value = (await checkPaymentApi()).data }
  catch (err) { apiHealth.value = { status: 'error', message: userError(err, '支付 API 连通性检查失败') } }
  finally { testing.value = false }
}
onMounted(load)
</script>

<template>
  <div class="workspace-shell finance-shell">
      <aside class="workspace-sidebar" :class="{ open: mobileNav }">
      <div class="workspace-brand"><BrandLogo eager /></div>
      <p class="workspace-role">管理后台</p>
      <nav><button v-for="[label, path, icon] in nav" :key="path" :class="{ active: route.path === path }" @click="router.push(path)"><component :is="icon" :size="17" />{{ label }}</button></nav>
      <div class="workspace-user"><span>{{ user.display_name || user.username }}</span><small>{{ user.role }}</small><button @click="logout"><LogOut :size="15" />退出登录</button></div>
    </aside>
    <div class="workspace-main">
      <header class="workspace-header finance-header"><button class="workspace-menu" aria-label="打开导航" @click="mobileNav = true"><Menu :size="20" /></button><div><h1>财务中心</h1><p>账户资金、订单收款与销售业绩</p></div><button class="icon-button" aria-label="刷新财务数据" title="刷新" @click="load"><RefreshCw :size="16" /></button></header>
      <main class="workspace-content finance-content">
        <div v-if="notice" class="finance-toast" role="status">{{ notice }}</div>
        <div v-if="error" class="finance-alert" role="alert"><AlertTriangle :size="17" /><span>{{ error }}</span><button @click="error = ''"><X :size="15" /></button></div>
        <div class="finance-tabs" role="tablist" aria-label="财务中心视图">
          <button role="tab" :aria-selected="tab === 'overview'" :class="{ active: tab === 'overview' }" @click="switchTab('overview')"><Gauge :size="15" />业绩统计</button>
          <button role="tab" :aria-selected="tab === 'recharge'" :class="{ active: tab === 'recharge' }" @click="switchTab('recharge')"><ArrowDownToLine :size="15" />线下充值</button>
          <button v-if="canConfigurePayment" role="tab" :aria-selected="tab === 'payment'" :class="{ active: tab === 'payment' }" @click="switchTab('payment')"><CreditCard :size="15" />支付管理</button>
        </div>

        <div v-if="loading" class="finance-loading" aria-label="财务数据加载中"><span v-for="n in 5" :key="n"></span></div>
        <template v-else-if="tab === 'overview'">
          <section class="finance-summary" aria-label="今日财务概览">
            <article><span>今日实收</span><strong>{{ totalLabel(currentTotals.cash_received_cents) }}</strong><small>在线及线下到账，不含余额内部划转</small></article>
            <article><span>今日销售额</span><strong>{{ totalLabel(currentTotals.sales_cents) }}</strong><small>已收订单金额，扣除已登记退款</small></article>
            <article><span>今日新增客户</span><strong>{{ currentTotals.new_customers || 0 }}</strong><small>已注册客户账号</small></article>
            <article><span>今日新增销售</span><strong>{{ currentTotals.new_sales || 0 }}</strong><small>销售 / 代理账号</small></article>
          </section>
          <section class="finance-section">
            <div class="section-head"><div><h2>每日收支与业绩</h2><p>客户完成订单付款后按订单金额计提 15%；退款完成登记后冲销对应佣金。</p></div><button class="secondary-button" @click="loadSummary"><RefreshCw :size="14" />更新</button></div>
            <div class="date-filters"><label>开始日期<input v-model="dateRange.from" type="date" /></label><label>结束日期<input v-model="dateRange.to" type="date" /></label><button class="primary-button" @click="loadSummary">查询</button></div>
            <div class="finance-table-wrap"><table><thead><tr><th>日期</th><th>实收</th><th>充值入账</th><th>销售额</th><th>退款</th><th>佣金净额</th><th>新增客户</th><th>新增销售</th></tr></thead><tbody><tr v-for="row in summary?.daily || []" :key="row.date"><td>{{ row.date }}</td><td>{{ totalLabel(row.cash_received_cents) }}</td><td>{{ totalLabel(row.recharge_cents) }}</td><td>{{ totalLabel(row.sales_cents) }}</td><td>{{ totalLabel(row.refund_cents) }}</td><td>{{ totalLabel(row.commission_cents) }}</td><td>{{ row.new_customers }}</td><td>{{ row.new_sales }}</td></tr></tbody></table></div>
          </section>
          <section class="finance-section">
            <div class="section-head"><div><h2>销售佣金累计</h2><p>金额按实收订单的 15% 记录，退款以负向流水冲回。</p></div></div>
            <div v-if="summary?.sales_commissions?.length" class="commission-list"><div v-for="item in summary.sales_commissions" :key="item.sales_id"><span>{{ item.display_name }} <small>销售账号 #{{ item.sales_id }}</small></span><strong>{{ totalLabel(item.net_cents) }}</strong></div></div>
            <div v-else class="finance-empty"><Users :size="21" /><b>暂无销售佣金记录</b><span>客户订单实际收款后才会产生佣金。</span></div>
          </section>
          <section class="finance-section">
            <div class="section-head"><div><h2>最近资金记录</h2><p>展示充值、订单收款及退款状态。</p></div><span>{{ summary?.recent_payments?.length || 0 }} 条</span></div>
            <div v-if="summary?.recent_payments?.length" class="finance-table-wrap"><table><thead><tr><th>流水号</th><th>账号</th><th>类型</th><th>金额</th><th>渠道</th><th>状态</th><th>时间</th><th>退款</th></tr></thead><tbody><tr v-for="item in summary.recent_payments" :key="item.id"><td>{{ item.out_trade_no }}</td><td>{{ item.customer_name }} · #{{ item.user_id }}</td><td>{{ item.purpose === 'wallet_recharge' ? '钱包充值' : '订单收款' }}</td><td>{{ totalLabel(item.amount_cents) }}</td><td>{{ item.channel === 'offline' ? '线下' : item.channel === 'balance' ? '余额' : '在线' }}</td><td><span class="finance-status" :data-tone="statusTone(item.status)">{{ statusLabel(item.status) }}</span></td><td>{{ dateLabel(item.paid_at || item.created_at) }}</td><td><button v-if="item.purpose === 'order_payment' && item.status === 'paid'" class="refund-link" @click="openRefund(item)">登记已完成退款</button><span v-else>—</span></td></tr></tbody></table></div>
            <div v-else class="finance-empty">暂无资金记录</div>
          </section>
        </template>

        <template v-else-if="tab === 'recharge'">
          <section class="finance-section recharge-section">
            <div class="section-head"><div><h2>登记线下充值</h2><p>确认款项已实际到账后，为客户或销售 / 代理钱包入账。</p></div><span class="manual-tag"><ClipboardCheck :size="14" />人工核对</span></div>
            <div class="recharge-form">
              <label class="search-field"><span>搜索账号</span><div><input v-model="accountQuery" maxlength="120" placeholder="输入昵称、手机号、邮箱或登录名" @keydown.enter.prevent="searchAccounts" /><button class="primary-button" :disabled="accountSearching" @click="searchAccounts"><Search :size="15" />{{ accountSearching ? '查找中' : '查找' }}</button></div></label>
              <div v-if="accountResults.length" class="account-results"><button v-for="item in accountResults" :key="item.user_id" type="button" :class="{ selected: selectedAccount?.user_id === item.user_id }" @click="selectedAccount = item"><span><b>{{ item.display_name }}</b><small>{{ item.role_label }} · {{ item.username }}<template v-if="item.phone"> · {{ item.phone }}</template></small></span><span class="account-balance">余额 {{ totalLabel(item.balance_cents) }}</span><Check v-if="selectedAccount?.user_id === item.user_id" :size="17" /></button></div>
              <div v-else-if="accountQuery && !accountSearching" class="inline-hint">没有匹配账号，请检查昵称或手机号。</div>
              <div class="recharge-fields"><label><span>充值金额（元）</span><div class="yuan-input"><i>¥</i><input v-model="rechargeAmount" inputmode="decimal" placeholder="0.00" maxlength="11" /></div></label><label><span>收款凭证 / 备注</span><input v-model="rechargeRemark" maxlength="500" placeholder="转账单号或经办说明" /></label></div>
              <button class="primary-button recharge-action" :disabled="saving" @click="beginRecharge"><ArrowDownToLine :size="16" />确认线下充值</button>
              <p class="caution-note"><ShieldCheck :size="15" />提交后立即增加账户余额并写入审计流水。请先核实实际收款，不能用此入口记录未到账款项。</p>
            </div>
          </section>
        </template>

        <template v-else-if="tab === 'payment' && canConfigurePayment">
          <section class="payment-lock-note"><ShieldCheck :size="18" /><span><b>商户密钥仅保存在后端密文中</b><small>这里只配置裕汇通 V1。正式启用前需要完成沙箱或受控小额支付、通知验签和金额核对；当前没有经确认的沙箱。</small></span></section>
          <section class="finance-section payment-section">
            <div class="section-head"><div><h2>支付通道配置</h2><p>接口地址固定为官方 HTTPS；密钥保存后不回显。</p></div><span class="finance-status" :data-tone="form.online_enabled ? 'good' : 'warn'">{{ form.online_enabled ? '在线收款已启用' : '在线收款关闭' }}</span></div>
            <div class="payment-form">
              <label><span>接口版本</span><input value="裕汇通 V1 · MD5" disabled /></label>
              <label><span>商户 ID（pid）</span><input v-model="form.merchant_id" maxlength="80" autocomplete="off" placeholder="输入商户 ID" /></label>
              <label class="field-wide"><span>V1 MD5 密钥</span><input v-model="form.secret" type="password" maxlength="500" autocomplete="new-password" placeholder="留空表示不更换已保存密钥" /><small>不会写入前端存储或审计日志；接口响应不会返回密钥。</small></label>
              <label><span>已开通的支付类型</span><input v-model="form.channel_types" maxlength="240" placeholder="以逗号分隔，例如 alipay,wxpay" /><small>只填写商户后台确认已开通的 type 编码。</small></label>
              <label><span>单笔最低金额（元）</span><input v-model="form.min_amount" inputmode="decimal" /></label>
              <label><span>单笔最高金额（元）</span><input v-model="form.max_amount" inputmode="decimal" /></label>
              <div class="inline-hint field-wide">在线收款当前由后端保持关闭。API 连通性检查不验证商户密钥；完成真实沙箱或受控小额支付、回调验签和重复通知核对后，需通过受控发布流程启用。</div>
              <div class="payment-actions field-wide"><button class="primary-button" :disabled="saving" @click="saveConfig"><Check :size="15" />{{ saving ? '保存中' : '保存配置' }}</button><button class="secondary-button" :disabled="testing" @click="testPaymentApi"><RefreshCw :size="15" :class="{ rotate: testing }" />{{ testing ? '检查中' : '检查 API 连通性' }}</button></div>
              <div v-if="paymentStatus" class="inline-hint field-wide">{{ paymentStatus }}</div>
              <div v-if="apiHealth" class="api-result field-wide" :data-state="apiHealth.status"><b>{{ apiHealth.status === 'reachable' ? '域名可连接' : apiHealth.status === 'not_configured' ? '未配置' : '连接异常' }}</b><span>{{ apiHealth.message }}</span><small v-if="apiHealth.http_status">HTTP {{ apiHealth.http_status }}</small></div>
            </div>
          </section>
        </template>
      </main>
    </div>

    <div v-if="rechargeConfirm" class="confirm-overlay" @click.self="rechargeConfirm = false">
      <section class="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="recharge-title"><button class="close-dialog" aria-label="关闭" @click="rechargeConfirm = false"><X :size="18" /></button><div class="confirm-icon"><CircleDollarSign :size="22" /></div><h2 id="recharge-title">确认线下充值</h2><dl><div><dt>账号</dt><dd>{{ selectedAccount?.display_name }} · {{ selectedAccount?.role_label }}</dd></div><div><dt>登录名</dt><dd>{{ selectedAccount?.username }}</dd></div><div><dt>入账金额</dt><dd>{{ totalLabel(centsFromYuan(rechargeAmount)) }}</dd></div><div><dt>凭证备注</dt><dd>{{ rechargeRemark }}</dd></div></dl><p>确认之前请核实外部款项已经到账。提交后余额立即增加。</p><div class="dialog-actions"><button class="secondary-button" :disabled="saving" @click="rechargeConfirm = false">返回检查</button><button class="primary-button" :disabled="saving" @click="submitRecharge">{{ saving ? '入账中…' : '确认入账' }}</button></div></section>
    </div>
    <div v-if="refundTarget" class="confirm-overlay" @click.self="closeRefund">
      <section class="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="refund-title"><button class="close-dialog" aria-label="关闭" :disabled="saving" @click="closeRefund"><X :size="18" /></button><div class="confirm-icon"><ArrowDownToLine :size="22" /></div><h2 id="refund-title">登记订单退款</h2><dl><div><dt>订单</dt><dd>#{{ refundTarget.order_id }} · {{ refundTarget.out_trade_no }}</dd></div><div><dt>原收金额</dt><dd>{{ totalLabel(refundTarget.amount_cents) }}</dd></div><div><dt>退款渠道</dt><dd>{{ refundTarget.channel === 'balance' ? '余额原路退回' : refundTarget.channel === 'offline' ? '线下转账' : '在线支付平台' }}</dd></div></dl><label class="dialog-field"><span>退款原因</span><input v-model="refundReason" maxlength="500" placeholder="说明退款原因" /></label><label v-if="refundTarget.channel !== 'balance'" class="dialog-field"><span>外部退款凭证号</span><input v-model="refundReference" maxlength="120" placeholder="已完成退款的交易凭证" /></label><label class="refund-confirm"><input v-model="refundConfirmed" type="checkbox" /><span>我确认退款已在对应渠道实际完成</span></label><p>提交后，余额渠道将退款记回原钱包；其他渠道必须先在支付平台或银行实际退款。本登记会冲销对应销售佣金，不会执行外部退款。</p><div v-if="refundError" class="finance-alert" role="alert"><AlertTriangle :size="16" /><span>{{ refundError }}</span></div><div class="dialog-actions"><button class="secondary-button" :disabled="saving" @click="closeRefund">返回</button><button class="primary-button" :disabled="saving" @click="submitRefund">{{ saving ? '登记中…' : '确认登记退款' }}</button></div></section>
    </div>
  </div>
</template>

<style scoped>
.finance-shell{background:#f2f5f3;color:#192824}
.finance-shell .workspace-sidebar{background:#fff;color:#21332e;border-right:1px solid #dce5e1;box-shadow:2px 0 12px #182d2508}
.finance-shell .workspace-role{color:#7c9189}
.finance-shell .workspace-sidebar nav button{color:#526860}
.finance-shell .workspace-sidebar nav button:hover,.finance-shell .workspace-sidebar nav button.active{background:#e7f4ef;color:#176b58}
.finance-shell .workspace-sidebar nav button.active svg{color:#17856b}
.finance-shell .workspace-user{border-color:#e1e9e5}
.finance-shell .workspace-user small,.finance-shell .workspace-user button{color:#71857d}
.finance-shell .workspace-brand button{color:#253932}
.finance-header{justify-content:flex-start;background:#fff}
.finance-header>div{display:grid;gap:4px}
.finance-header p{margin:0;color:#748780;font-size:12px;font-family:inherit}
.finance-header .icon-button{margin-left:auto}
.finance-content{max-width:1600px}
.finance-toast{position:fixed;right:22px;bottom:22px;z-index:30;padding:11px 15px;border:1px solid #b9dcc9;border-radius:5px;background:#eef8f1;color:#236648;font-size:11px;box-shadow:0 8px 24px #203d3018}
.finance-alert{display:flex;align-items:center;gap:9px;margin-bottom:13px;padding:10px 12px;border:1px solid #e6c2bc;border-radius:5px;background:#fff2ef;color:#8e3e32;font-size:11px;line-height:1.5}
.finance-alert span{flex:1;min-width:0;overflow-wrap:anywhere}
.finance-alert button{display:grid;place-items:center;flex:none}
.finance-tabs{display:flex;gap:4px;margin-bottom:14px;border-bottom:1px solid #d9e3df;overflow-x:auto}
.finance-tabs button{display:flex;align-items:center;justify-content:center;gap:7px;min-height:44px;padding:0 14px;border-bottom:2px solid transparent;color:#667970;font-size:11px;font-weight:650;white-space:nowrap;transition:color .18s ease,border-color .18s ease}
.finance-tabs button:hover{color:#176c58}
.finance-tabs button.active{border-color:#168267;color:#126d56}
.finance-loading{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.finance-loading span{height:104px;border-radius:5px;background:#dfe8e4;animation:finance-skeleton 1.1s ease-in-out infinite alternate}
.finance-loading span:nth-child(n+5){grid-column:span 2;height:260px}
@keyframes finance-skeleton{to{background:#edf2ef}}
.finance-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));margin-bottom:14px;border:1px solid #dce5e1;border-radius:5px;background:#fff}
.finance-summary article{display:grid;gap:5px;min-width:0;padding:14px 16px;border-right:1px solid #e6ece9}
.finance-summary article:last-child{border:0}
.finance-summary article>span{color:#71847c;font-size:10px}
.finance-summary strong{color:#245f48;font-size:21px;font-variant-numeric:tabular-nums}
.finance-summary small{color:#819087;font-size:9px;line-height:1.45}
.finance-section{min-width:0;margin-bottom:14px;padding:15px;border:1px solid #dce5e1;border-radius:5px;background:#fff}
.section-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:13px}
.section-head h2{margin:0;color:#22352e;font-size:15px;line-height:1.35}
.section-head p{margin:4px 0 0;color:#71837b;font-size:10px;line-height:1.5}
.section-head>span{color:#71847c;font-size:10px;white-space:nowrap}
.primary-button,.secondary-button{display:inline-flex;align-items:center;justify-content:center;gap:7px;min-height:36px;padding:7px 12px;border:1px solid transparent;border-radius:5px;font-size:11px;font-weight:700;white-space:nowrap;transition:background .18s ease,color .18s ease,border-color .18s ease}
.primary-button{background:#167c62;color:#fff}
.primary-button:hover:not(:disabled){background:#10684f}
.secondary-button{border-color:#cbd9d3;background:#fff;color:#456358}
.secondary-button:hover:not(:disabled){border-color:#86b6a4;background:#f4faf6}
.primary-button:disabled,.secondary-button:disabled{opacity:.5;cursor:not-allowed}
.date-filters{display:flex;align-items:end;gap:9px;margin-bottom:12px}
.date-filters label{display:grid;gap:4px;color:#71847c;font-size:10px}
.date-filters input{height:36px;padding:0 8px;border:1px solid #cbd9d3;border-radius:5px;background:#fff;color:#293d34;font-size:11px}
.finance-table-wrap{max-width:100%;overflow:auto;border:1px solid #e0e8e4;border-radius:5px}
.finance-table-wrap table{width:100%;border-collapse:collapse;text-align:left;white-space:nowrap;font-size:10px}
.finance-table-wrap th{height:36px;padding:0 10px;background:#f0f5f2;color:#657a70;font-size:9px;font-weight:650}
.finance-table-wrap td{height:43px;padding:6px 10px;border-top:1px solid #e8eeeb;color:#374d44}
.finance-table-wrap tbody tr:hover{background:#f8fbf9}
.finance-status{display:inline-flex;align-items:center;min-height:21px;padding:3px 7px;border-radius:4px;background:#e8f0ec;color:#496158;font-size:9px;font-weight:650}
.finance-status[data-tone="good"]{background:#e1f3ea;color:#21684e}
.finance-status[data-tone="warn"]{background:#fff1d8;color:#805917}
.finance-status[data-tone="info"]{background:#e4eefb;color:#355e94}
.finance-status[data-tone="muted"]{background:#edf0ee;color:#697870}
.commission-list{display:grid}
.commission-list>div{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:46px;border-top:1px solid #e6ece9;color:#344a40;font-size:11px}
.commission-list small{display:block;margin-top:3px;color:#819087;font-size:9px}
.commission-list strong{color:#245f48;font-size:12px;font-variant-numeric:tabular-nums}
.finance-empty{min-height:115px;display:grid;justify-items:center;align-content:center;gap:6px;color:#657971;text-align:center;font-size:10px}
.finance-empty b{font-size:11px}.finance-empty span{color:#819087}
.manual-tag{display:inline-flex;align-items:center;gap:5px;padding:5px 8px;border-radius:4px;background:#fff5e5;color:#805917;font-size:9px;font-weight:700}
.recharge-form{display:grid;gap:12px}
.recharge-form label,.payment-form label{display:grid;gap:5px;min-width:0;color:#576d63;font-size:10px;font-weight:650}
.search-field>div{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px}
.recharge-form input,.payment-form input{width:100%;min-width:0;height:37px;padding:0 10px;border:1px solid #cbd9d3;border-radius:5px;background:#fff;color:#243831;font-size:11px}
.recharge-form input:focus,.payment-form input:focus,.date-filters input:focus{outline:2px solid #48a58b;outline-offset:1px}
.account-results{display:grid;border:1px solid #e0e8e4;border-radius:5px;overflow:hidden}
.account-results>button{display:grid;grid-template-columns:minmax(0,1fr) auto 18px;align-items:center;gap:10px;min-height:57px;padding:8px 11px;border-bottom:1px solid #e6ece9;text-align:left;transition:background .15s ease}
.account-results>button:last-child{border-bottom:0}
.account-results>button:hover,.account-results>button.selected{background:#f0f8f4}
.account-results>button>span:first-child{display:grid;gap:3px;min-width:0}
.account-results b{color:#2d4339;font-size:11px;overflow:hidden;text-overflow:ellipsis}
.account-results small{color:#7b8c84;font-size:9px;overflow-wrap:anywhere}
.account-results svg{color:#167c62}
.account-balance{color:#426458;font-size:10px;white-space:nowrap}
.inline-hint{padding:9px 10px;border-radius:5px;background:#f3f7f5;color:#718179;font-size:10px;line-height:1.5}
.recharge-fields{display:grid;grid-template-columns:minmax(150px,.7fr) minmax(0,1.3fr);gap:10px}
.yuan-input{position:relative}.yuan-input i{position:absolute;left:10px;top:10px;color:#74857d;font-size:11px;font-style:normal}.yuan-input input{padding-left:25px}
.recharge-action{justify-self:start;min-height:39px}
.caution-note{display:flex;align-items:flex-start;gap:7px;margin:0;color:#8a663b;font-size:9px;line-height:1.5}
.caution-note svg{flex:none;color:#94713a}
.payment-lock-note{display:flex;align-items:flex-start;gap:10px;margin-bottom:13px;padding:11px 13px;border:1px solid #e8d3ab;border-radius:5px;background:#fff8eb;color:#755316}
.payment-lock-note>span{display:grid;gap:3px}.payment-lock-note b{font-size:11px}.payment-lock-note small{color:#806b43;font-size:9px;line-height:1.5}
.payment-section .section-head{align-items:center}
.payment-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.payment-form label small{color:#7e8d85;font-size:9px;line-height:1.45;font-weight:400}
.field-wide{grid-column:1/-1}
.payment-actions{display:flex;flex-wrap:wrap;gap:8px}
.api-result{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:9px;padding:10px;border:1px solid #c9dfd3;border-radius:5px;background:#f1f8f3;color:#315e49;font-size:10px}
.api-result[data-state="timeout"],.api-result[data-state="connection_failed"],.api-result[data-state="provider_error"]{border-color:#e5c4bd;background:#fff3f0;color:#8e493d}
.api-result[data-state="not_configured"]{border-color:#e8d3ab;background:#fff8eb;color:#755316}
.api-result span{min-width:0;line-height:1.5}.api-result small{white-space:nowrap}
.rotate{animation:finance-rotate .8s linear infinite}@keyframes finance-rotate{to{transform:rotate(360deg)}}
.confirm-overlay{position:fixed;inset:0;z-index:50;display:grid;place-items:center;padding:18px;background:#12251f66}
.confirm-dialog{position:relative;width:min(100%,460px);max-height:min(88vh,760px);overflow:auto;padding:23px;border:1px solid #dce5e1;border-radius:7px;background:#fff;box-shadow:0 16px 48px #10251e35;color:#263b32;animation:finance-dialog-in .18s ease-out}
@keyframes finance-dialog-in{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:translateY(0)}}
.close-dialog{position:absolute;top:13px;right:13px;width:32px;height:32px;display:grid;place-items:center;border:1px solid #dce5e1;border-radius:5px;background:#fff;color:#61766c}
.close-dialog:disabled{opacity:.5;cursor:not-allowed}
.confirm-icon{width:42px;height:42px;display:grid;place-items:center;border-radius:50%;background:#e6f3ec;color:#287457}
.confirm-dialog h2{margin:13px 0;color:#263b32;font-size:18px}
.confirm-dialog dl{display:grid;gap:0;margin:12px 0;padding:10px 12px;border-radius:5px;background:#f4f8f5}
.confirm-dialog dl>div{display:grid;grid-template-columns:90px minmax(0,1fr);gap:10px;padding:7px 0;border-bottom:1px solid #e4ebe7}
.confirm-dialog dl>div:last-child{border-bottom:0}
.confirm-dialog dt{color:#74857d;font-size:10px}.confirm-dialog dd{min-width:0;margin:0;color:#354a40;font-size:10px;overflow-wrap:anywhere}
.confirm-dialog>p{color:#74857d;font-size:10px;line-height:1.55}
.dialog-field{display:grid;gap:5px;margin-top:10px;color:#61756c;font-size:10px}
.dialog-field input{height:36px;width:100%;padding:0 9px;border:1px solid #cad8d2;border-radius:5px;background:#fff;color:#273a32;font-size:12px}
.refund-confirm{display:flex;align-items:center;gap:8px;margin-top:12px;color:#536a5e;font-size:10px}
.refund-confirm input{width:16px;height:16px;accent-color:#167c62}
.dialog-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:15px}
.refund-link{color:#176b58;font-size:10px;text-decoration:underline;text-underline-offset:2px}.refund-link:hover{color:#0b4938}
@media(max-width:800px){.finance-shell{display:block}.finance-shell .workspace-sidebar{position:fixed}.finance-shell .workspace-header{height:76px;padding:0 18px}}
@media(max-width:900px){.finance-summary{grid-template-columns:repeat(2,minmax(0,1fr))}.finance-summary article:nth-child(2){border-right:0}.finance-summary article:nth-child(n+3){border-top:1px solid #e5ece8}.finance-content{padding:18px}}
@media(max-width:560px){.finance-content{padding:14px}.finance-header{padding:0 14px}.finance-header h1{font-size:19px}.finance-tabs button{padding:0 9px;font-size:10px}.finance-summary strong{font-size:19px}.finance-summary article{padding:12px 10px}.finance-summary small{font-size:9px}.finance-section{padding:12px}.section-head h2{font-size:14px}.date-filters{display:grid;grid-template-columns:1fr 1fr}.date-filters label,.date-filters input{width:100%;min-width:0}.date-filters button{grid-column:1/-1}.recharge-fields,.payment-form{grid-template-columns:1fr}.payment-form .field-wide{grid-column:auto}.account-results>button{grid-template-columns:minmax(0,1fr) auto;gap:7px}.account-results>button svg{display:none}.account-balance{font-size:9px}.payment-actions{flex-wrap:wrap}.section-head>span{white-space:normal}.confirm-dialog{padding:19px}.dialog-actions>*{flex:1}.finance-table-wrap{max-width:calc(100vw - 58px)}.api-result{grid-template-columns:1fr;gap:4px}}
@media(prefers-reduced-motion:reduce){.finance-loading span,.rotate,.confirm-dialog{animation:none}.finance-tabs button,.primary-button,.secondary-button{transition:none}}
</style>
