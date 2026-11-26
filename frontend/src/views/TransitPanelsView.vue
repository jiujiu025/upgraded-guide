<script setup>
// 中转面板管理：录入 3x-ui 面板、检测连通、维护国家映射、处理待配中转。
// 运营加完一台机器，社媒/直播订单付款后就会自动在这台面板上建入站 + socks 出站 + 路由，
// 所以这里第一件事是"能不能连上"，而不是先让客户买到一张配不出线路的单。
import { computed, onMounted, reactive, ref } from 'vue'
import {
  CheckCircle2, CircleAlert, Plus, RefreshCw, Server, X,
} from 'lucide-vue-next'
import {
  createTransitPanel, getTransitPanels, getTransitProvisions, getTransitRoutes, getTransitSummary,
  retryTransitOrder, saveTransitRoute, testTransitPanel, updateTransitPanel,
} from '../api'
import TransitBackupPanel from './TransitBackupPanel.vue'

const TABS = [
  { key: 'panels', label: '面板' },
  { key: 'routes', label: '国家映射' },
  { key: 'provisions', label: '待配中转' },
  { key: 'backups', label: '备份与迁移' },
]
const PROVISION_LABELS = {
  pending: '待开始',
  inbound_created: '入站已建，待出站',
  configured: '已配置',
  failed: '失败',
}
const PROVISION_TONE = { pending: 'wait', inbound_created: 'warn', configured: 'ok', failed: 'bad' }

const activeTab = ref('panels')
const panels = ref([])
const loading = ref(true)
const error = ref('')
const notice = ref('')
const summary = ref({ auto_procurement_enabled: false, panels: 0, enabled_panels: 0, pending_transit_orders: 0 })

const routes = ref([])
const routesLoading = ref(false)
const routeSaving = ref('')

const provisions = ref([])
const provisionsLoading = ref(false)
const retryingId = ref(0)

const dialogOpen = ref(false)
const editingId = ref(0)
const saving = ref(false)
const formError = ref('')
const form = reactive(blankForm())

const testingId = ref(0)
const testResult = ref(null)

const groupOptions = computed(() => {
  const set = new Set(['us', 'hk'])
  panels.value.forEach((item) => item.group_code && set.add(item.group_code))
  return [...set]
})
const enabledCount = computed(() => panels.value.filter((item) => item.is_enabled).length)

function blankForm() {
  return {
    name: '', group_code: 'us', base_url: '', username: '', password: '', api_token: '',
    connect_host: '', reality_private_key: '', reality_public_key: '', reality_short_id: 'd816',
    sni: 'yahoo.com', dest: 'yahoo.com:443', fingerprint: 'random',
    port_start: 52000, port_end: 52999, max_inbounds: 200, is_enabled: true, note: '',
  }
}

function resetForm(values = {}) {
  Object.assign(form, blankForm(), values)
}

function flash(message) {
  notice.value = message
  window.setTimeout(() => { if (notice.value === message) notice.value = '' }, 4000)
}

async function loadPanels() {
  loading.value = true
  error.value = ''
  try {
    const [{ data }, summaryResult] = await Promise.all([
      getTransitPanels(),
      getTransitSummary().catch(() => ({ data: summary.value })),
    ])
    panels.value = data || []
    summary.value = summaryResult.data || summary.value
  } catch (err) {
    error.value = err.userMessage || '面板列表加载失败'
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = 0
  resetForm()
  formError.value = ''
  dialogOpen.value = true
}

function openEdit(panel) {
  editingId.value = panel.id
  resetForm({
    name: panel.name,
    group_code: panel.group_code,
    base_url: panel.base_url,
    username: panel.username || '',
    password: '',
    api_token: '',
    connect_host: panel.connect_host,
    reality_private_key: '',
    reality_public_key: panel.reality_public_key || '',
    reality_short_id: '',
    sni: panel.sni,
    dest: panel.dest,
    fingerprint: panel.fingerprint,
    port_start: panel.port_start,
    port_end: panel.port_end,
    max_inbounds: panel.max_inbounds,
    is_enabled: panel.is_enabled,
    note: panel.note || '',
  })
  formError.value = ''
  dialogOpen.value = true
}

function validate() {
  if (!form.name.trim()) return '请填写面板名称，如 US-01'
  if (!/^https?:\/\//.test(form.base_url.trim())) return '面板地址要以 http:// 或 https:// 开头'
  if (!form.connect_host.trim()) return '请填写客户连接的地址（面板 IP 或中转入口域名）'
  if (!form.port_start || !form.port_end || form.port_start > form.port_end) return '端口段起始不能大于结束'
  if (editingId.value) return ''
  if (!form.api_token && !(form.username.trim() && form.password)) return '请填写面板账号与密码，或 API Token'
  return ''
}

function payloadOf() {
  const payload = {
    ...form,
    name: form.name.trim(),
    base_url: form.base_url.trim(),
    connect_host: form.connect_host.trim(),
    username: form.username.trim() || null,
    note: form.note.trim() || null,
    port_start: Number(form.port_start),
    port_end: Number(form.port_end),
    max_inbounds: Number(form.max_inbounds),
  }
  // 名称是唯一标识，创建后不允许改：后端的编辑接口没有这个字段，
  // 传了会被 extra="forbid" 直接拒掉（就是那句 Extra inputs are not permitted）
  if (editingId.value) delete payload.name
  // 编辑时留空 = 不改凭据；后端按"没传这个字段"处理，传空串会被当成清除
  if (!form.password) delete payload.password
  if (!form.api_token) delete payload.api_token
  if (!form.reality_private_key) delete payload.reality_private_key
  if (!form.reality_short_id) delete payload.reality_short_id
  return payload
}

async function submit() {
  formError.value = validate()
  if (formError.value) return
  saving.value = true
  try {
    if (editingId.value) {
      await updateTransitPanel(editingId.value, payloadOf())
      flash('面板已更新')
    } else {
      await createTransitPanel(payloadOf())
      flash('面板已添加，建议立刻点「检测」确认能连上')
    }
    dialogOpen.value = false
    await loadPanels()
  } catch (err) {
    formError.value = err.userMessage || '保存失败'
  } finally {
    saving.value = false
  }
}

function otherMachinesInGroup(panel) {
  return panels.value.filter(
    (item) => item.group_code === panel.group_code && item.id !== panel.id && item.is_enabled,
  ).length
}

async function toggleEnabled(panel) {
  const used = panel.used_inbounds || 0
  if (panel.is_enabled) {
    // 停用只影响新单，已有客户继续用；但同组没别的机器时新单会直接卡住，必须先讲清楚
    const warnings = []
    if (used) warnings.push(`这台上已有 ${used} 条线路，停用后它们照常使用，不受影响`)
    if (!otherMachinesInGroup(panel)) {
      warnings.push(`${panel.group_code} 组没有其它启用中的面板，停用后新订单会停在「待配中转」`)
    }
    const message = [`停用 ${panel.name}？`, ...warnings].join('\n')
    if (!window.confirm(message)) return
  }
  try {
    await updateTransitPanel(panel.id, { is_enabled: !panel.is_enabled })
    if (panel.is_enabled && !otherMachinesInGroup(panel)) {
      flash(`${panel.name} 已停用；${panel.group_code} 组暂无可用面板，新单会停在待配中转`)
    } else {
      flash(panel.is_enabled ? `${panel.name} 已停用，新订单不再分配给它` : `${panel.name} 已启用，可以接新单了`)
    }
    await loadPanels()
  } catch (err) {
    error.value = err.userMessage || '状态修改失败'
  }
}

async function runTest(panel) {
  testingId.value = panel.id
  testResult.value = null
  try {
    const { data } = await testTransitPanel(panel.id)
    testResult.value = {
      id: panel.id,
      ok: true,
      message: `连接成功：${data.inbound_count} 个入站、${data.socks_outbounds} 条 socks 出站、${data.routing_rules} 条路由规则，下一个可用端口 ${data.next_free_port || '—'}`,
    }
  } catch (err) {
    testResult.value = { id: panel.id, ok: false, message: `连接失败：${err.userMessage || '未知原因'}` }
  } finally {
    testingId.value = 0
  }
}

async function loadRoutes() {
  routesLoading.value = true
  try {
    const { data } = await getTransitRoutes()
    routes.value = data || []
  } catch (err) {
    error.value = err.userMessage || '国家映射加载失败'
  } finally {
    routesLoading.value = false
  }
}

async function changeRoute(route, groupCode) {
  routeSaving.value = route.country_code
  const previous = route.group_code
  route.group_code = groupCode
  try {
    await saveTransitRoute(route.country_code, {
      group_code: groupCode, is_enabled: route.is_enabled, note: route.note || null,
    })
    flash(`${route.country_code} 已改为走 ${groupCode} 组`)
  } catch (err) {
    route.group_code = previous
    error.value = err.userMessage || '映射保存失败'
  } finally {
    routeSaving.value = ''
  }
}

async function loadProvisions() {
  provisionsLoading.value = true
  try {
    const { data } = await getTransitProvisions({ limit: 100 })
    provisions.value = data || []
  } catch (err) {
    error.value = err.userMessage || '待配中转加载失败'
  } finally {
    provisionsLoading.value = false
  }
}

async function retry(provision) {
  retryingId.value = provision.order_id
  try {
    await retryTransitOrder(provision.order_id)
    flash(`订单 ${provision.order_no} 中转已配好并完成交付`)
    await Promise.all([loadProvisions(), loadPanels()])
  } catch (err) {
    error.value = err.userMessage || '重试失败，请查看面板日志'
  } finally {
    retryingId.value = 0
  }
}

function switchTab(key) {
  activeTab.value = key
  error.value = ''
  if (key === 'routes' && !routes.value.length) loadRoutes()
  if (key === 'provisions' && !provisions.value.length) loadProvisions()
}

onMounted(loadPanels)
</script>

<template>
  <section class="tp-page">
    <header class="tp-head">
      <div>
        <p class="tp-eyebrow"><Server :size="13" /> 中转面板</p>
        <h1>社媒 / 直播专线的落地机器</h1>
        <p class="tp-desc">
          客户付款后会在这台 3x-ui 面板上自动建 vless+reality 入站、加 socks5 出站、绑定路由，
          再把链接发给客户。先保证「检测」通过，再让它接单。
        </p>
      </div>
      <button class="tp-ghost" type="button" :disabled="loading" @click="loadPanels">
        <RefreshCw :size="14" />{{ loading ? '加载中…' : '刷新' }}
      </button>
    </header>

    <p v-if="notice" class="tp-notice"><CheckCircle2 :size="14" />{{ notice }}</p>
    <p v-if="error" class="tp-error"><CircleAlert :size="14" />{{ error }}</p>

    <div class="tp-status" :data-tone="summary.auto_procurement_enabled ? 'ok' : 'warn'">
      <CheckCircle2 v-if="summary.auto_procurement_enabled" :size="15" />
      <CircleAlert v-else :size="15" />
      <div>
        <strong>{{ summary.auto_procurement_enabled ? '自动开通已开启' : '自动开通当前关闭' }}</strong>
        <span v-if="summary.auto_procurement_enabled">
          客户付款后会先采购住宅 IP，再在下面这些面板上建入站、加 socks 出站并交付链接。
        </span>
        <span v-else>
          社媒/直播订单付款后只会采购 IP，不会走面板，订单停在「待交付」。开关在「资源池」页，
          属于资金开关，这里不代开。
        </span>
        <em v-if="summary.pending_transit_orders">还有 {{ summary.pending_transit_orders }} 张订单待配中转</em>
      </div>
    </div>

    <nav class="tp-tabs">
      <button
        v-for="tab in TABS" :key="tab.key" type="button"
        :class="{ active: activeTab === tab.key }" @click="switchTab(tab.key)"
      >{{ tab.label }}</button>
      <span class="tp-tabs-meta">已启用 {{ enabledCount }} / {{ panels.length }} 台</span>
    </nav>

    <template v-if="activeTab === 'panels'">
      <div class="tp-toolbar">
        <p>区域组决定这台机器接哪些国家：<code>us</code> 默认接北美/欧洲，<code>hk</code> 默认接东南亚/南非。</p>
        <button class="tp-primary" type="button" @click="openCreate"><Plus :size="14" />添加面板</button>
      </div>

      <p v-if="loading" class="tp-empty">正在读取面板…</p>
      <p v-else-if="!panels.length" class="tp-empty">还没有面板。添加一台后，社媒专线才有地方落地。</p>

      <div v-else class="tp-cards">
        <article v-for="panel in panels" :key="panel.id" class="tp-card" :class="{ off: !panel.is_enabled }">
          <div class="tp-card-top">
            <div>
              <h2>{{ panel.name }}<span class="tp-group">{{ panel.group_code }}</span></h2>
              <p class="tp-url">{{ panel.base_url }}</p>
            </div>
            <span class="tp-state" :data-tone="panel.is_enabled ? 'ok' : 'off'">
              {{ panel.is_enabled ? '启用中' : '已停用' }}
            </span>
          </div>

          <dl class="tp-facts">
            <div><dt>客户连接</dt><dd>{{ panel.connect_host }}</dd></div>
            <div><dt>端口段</dt><dd>{{ panel.port_start }}–{{ panel.port_end }}</dd></div>
            <div><dt>已用 / 上限</dt><dd>{{ panel.used_inbounds || 0 }} / {{ panel.max_inbounds }} 条</dd></div>
            <div><dt>凭据</dt><dd>{{ panel.has_api_token ? 'API Token' : panel.has_password ? '账号密码' : '未配置' }}</dd></div>
          </dl>

          <p v-if="testResult && testResult.id === panel.id" class="tp-test" :data-tone="testResult.ok ? 'ok' : 'bad'">
            <CheckCircle2 v-if="testResult.ok" :size="14" />
            <CircleAlert v-else :size="14" />
            {{ testResult.message }}
          </p>

          <div class="tp-card-actions">
            <button class="tp-ghost" type="button" :disabled="testingId === panel.id" @click="runTest(panel)">
              <RefreshCw :size="13" :class="{ spin: testingId === panel.id }" />
              {{ testingId === panel.id ? '检测中…' : '检测' }}
            </button>
            <button class="tp-ghost" type="button" @click="openEdit(panel)">编辑</button>
            <button class="tp-ghost" :class="{ danger: panel.is_enabled }" type="button" @click="toggleEnabled(panel)">
              {{ panel.is_enabled ? '停用' : '启用' }}
            </button>
          </div>
        </article>
      </div>
    </template>

    <template v-else-if="activeTab === 'routes'">
      <div class="tp-toolbar">
        <p>客户买的国家走哪组机器。改完立即生效，下一单就按新映射选机。</p>
        <button class="tp-ghost" type="button" :disabled="routesLoading" @click="loadRoutes">
          <RefreshCw :size="14" />{{ routesLoading ? '加载中…' : '刷新' }}
        </button>
      </div>
      <p v-if="routesLoading" class="tp-empty">正在读取国家映射…</p>
      <div v-else class="tp-table">
        <div class="tp-tr tp-th"><span>国家</span><span>走哪组</span><span>状态</span><span>说明</span></div>
        <div v-for="route in routes" :key="route.country_code" class="tp-tr">
          <span class="tp-country">{{ route.country_code }}</span>
          <span>
            <select
              :value="route.group_code" :disabled="routeSaving === route.country_code"
              @change="changeRoute(route, $event.target.value)"
            >
              <option v-for="group in groupOptions" :key="group" :value="group">{{ group }}</option>
            </select>
          </span>
          <span>{{ route.is_enabled ? '启用' : '停用' }}</span>
          <span class="tp-note">{{ route.note || '—' }}</span>
        </div>
      </div>
    </template>

    <template v-else-if="activeTab === 'provisions'">
      <div class="tp-toolbar">
        <p>付款后 IP 已买到、但中转没配完的订单。修好面板后在这里重试，不会重复买 IP。</p>
        <button class="tp-ghost" type="button" :disabled="provisionsLoading" @click="loadProvisions">
          <RefreshCw :size="14" />{{ provisionsLoading ? '加载中…' : '刷新' }}
        </button>
      </div>
      <p v-if="provisionsLoading" class="tp-empty">正在读取中转记录…</p>
      <p v-else-if="!provisions.length" class="tp-empty">没有待配中转的订单。</p>
      <div v-else class="tp-table wide">
        <div class="tp-tr tp-th"><span>订单</span><span>线路</span><span>面板</span><span>出口 IP</span><span>状态</span><span>原因</span><span>操作</span></div>
        <div v-for="item in provisions" :key="item.id" class="tp-tr">
          <span class="tp-country">{{ item.order_no || `#${item.order_id}` }}</span>
          <span>第 {{ item.line_index }} 条</span>
          <span>{{ item.vps_name || '—' }}<template v-if="item.inbound_port"> · {{ item.inbound_port }}</template></span>
          <span>{{ item.exit_ip || '—' }}</span>
          <span><em class="tp-badge" :data-tone="PROVISION_TONE[item.status] || 'wait'">{{ PROVISION_LABELS[item.status] || item.status }}</em></span>
          <span class="tp-note">{{ item.error_message || '—' }}</span>
          <span>
            <button
              v-if="item.status !== 'configured'" class="tp-primary small" type="button"
              :disabled="retryingId === item.order_id" @click="retry(item)"
            >{{ retryingId === item.order_id ? '处理中…' : '重试配置' }}</button>
            <span v-else class="tp-note">已交付</span>
          </span>
        </div>
      </div>
    </template>

    <template v-else>
      <TransitBackupPanel :panels="panels" />
    </template>

    <div v-if="dialogOpen" class="tp-dialog" role="dialog" aria-modal="true" aria-label="中转面板">
      <div class="tp-dialog-card">
        <header>
          <div>
            <h2>{{ editingId ? '编辑面板' : '添加中转面板' }}</h2>
            <p>地址要带面板路径；密码与 Token 只写不读，保存后不再显示。</p>
          </div>
          <button class="tp-close" type="button" aria-label="关闭" @click="dialogOpen = false"><X :size="18" /></button>
        </header>

        <div class="tp-form">
          <label>面板名称
            <input v-model="form.name" :disabled="Boolean(editingId)" placeholder="如 US-01 / HK-02" />
            <small v-if="editingId" class="tp-field-note">名称是唯一标识，创建后不可修改</small>
          </label>
          <label>区域组
            <select v-model="form.group_code">
              <option v-for="group in groupOptions" :key="group" :value="group">{{ group }}</option>
            </select>
          </label>
          <label class="span2">登录地址<input v-model="form.base_url" placeholder="https://1.2.3.4:54321/panelpath" /></label>
          <label>面板账号<input v-model="form.username" autocomplete="off" placeholder="admin" /></label>
          <label>面板密码
            <input
              v-model="form.password" type="password" autocomplete="new-password"
              :placeholder="editingId ? '留空表示不修改' : '面板登录密码'"
            />
          </label>
          <label class="span2">API Token（可选，填了优先用 Token）
            <input
              v-model="form.api_token" type="password" autocomplete="new-password"
              :placeholder="editingId ? '留空表示不修改' : '面板 设置 → 安全 → 创建 Token'"
            />
          </label>
          <label class="span2">客户连接地址<input v-model="form.connect_host" placeholder="客户实际连接的 IP 或域名" /></label>
          <label class="span2">reality 私钥（留空 = 每条入站自动生成）
            <input
              v-model="form.reality_private_key"
              :placeholder="editingId ? '留空表示不修改' : '不用填；除非要所有入站共用一把'"
            />
          </label>
          <label class="span2">reality 公钥（留空 = 自动推导）
            <input v-model="form.reality_public_key" placeholder="填了私钥可不填；客户链接里的 pbk" />
          </label>
          <label>shortId（留空 = 随机）
            <input
              v-model="form.reality_short_id"
              :placeholder="editingId ? '留空表示不修改' : '如 d816'"
            />
          </label>
          <label>fingerprint<input v-model="form.fingerprint" placeholder="random / chrome" /></label>
          <label>SNI<input v-model="form.sni" placeholder="yahoo.com" /></label>
          <label>dest<input v-model="form.dest" placeholder="yahoo.com:443" /></label>
          <label>起始端口<input v-model.number="form.port_start" type="number" min="1024" max="65535" /></label>
          <label>结束端口<input v-model.number="form.port_end" type="number" min="1024" max="65535" /></label>
          <label>单机承载上限<input v-model.number="form.max_inbounds" type="number" min="1" max="2000" /></label>
          <label class="check span2">
            <input v-model="form.is_enabled" type="checkbox" />启用（停用的面板不会被选来接新单）
          </label>
          <label class="span2">备注<input v-model="form.note" placeholder="如：洛杉矶，1Gbps，2026-11 新增" /></label>
        </div>

        <p v-if="formError" class="tp-error"><CircleAlert :size="14" />{{ formError }}</p>

        <footer>
          <button class="tp-ghost" type="button" @click="dialogOpen = false">取消</button>
          <button class="tp-primary" type="button" :disabled="saving" @click="submit">
            {{ saving ? '保存中…' : '保存' }}
          </button>
        </footer>
      </div>
    </div>
  </section>
</template>

<style scoped>
.tp-page{min-height:100vh;padding:28px 32px 48px;background:#f2f5f3;color:#20342d}
.tp-head{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;margin-bottom:16px}
.tp-eyebrow{margin:0 0 6px;display:flex;align-items:center;gap:6px;color:#17856b;font-size:11px;font-weight:650;letter-spacing:.04em}
.tp-head h1{margin:0;font-size:22px;font-weight:680}
.tp-desc{max-width:660px;margin:8px 0 0;color:#5f7469;font-size:12px;line-height:1.7}
.tp-notice,.tp-error{display:flex;align-items:center;gap:7px;margin:10px 0;padding:9px 12px;border-radius:6px;font-size:12px}
.tp-notice{background:#e8f5ee;color:#176b58;border:1px solid #c6e4d5}
.tp-error{background:#fcecec;color:#a23232;border:1px solid #f2cfcf}
.tp-status{display:flex;align-items:flex-start;gap:9px;margin:12px 0 0;padding:11px 14px;border-radius:7px;font-size:12px;line-height:1.65}
.tp-status[data-tone="ok"]{background:#eef8f2;color:#176b58;border:1px solid #cde8da}
.tp-status[data-tone="warn"]{background:#fdf6e9;color:#8a5a13;border:1px solid #f0ddbc}
.tp-status strong{display:block;font-size:12px;font-weight:680}
.tp-status em{display:block;margin-top:2px;font-style:normal;font-size:11px;opacity:.85}
.tp-tabs{display:flex;align-items:center;gap:4px;margin:14px 0 16px;border-bottom:1px solid #dbe5e0}
.tp-tabs button{min-height:38px;padding:0 14px;border-bottom:2px solid transparent;color:#5f7469;font-size:12px;font-weight:650}
.tp-tabs button.active{color:#176b58;border-bottom-color:#17856b}
.tp-tabs-meta{margin-left:auto;color:#7c8f86;font-size:11px}
.tp-toolbar{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:14px}
.tp-toolbar p{margin:0;color:#5f7469;font-size:12px}
.tp-toolbar code{padding:1px 5px;border-radius:4px;background:#e6efe9;color:#176b58;font-size:11px}
.tp-primary{display:inline-flex;align-items:center;gap:6px;min-height:34px;padding:0 13px;border-radius:5px;background:#17795f;color:#fff;font-size:12px;font-weight:650}
.tp-primary:disabled{opacity:.6;cursor:wait}
.tp-primary.small{min-height:28px;padding:0 10px;font-size:11px}
.tp-ghost{display:inline-flex;align-items:center;gap:6px;min-height:34px;padding:0 12px;border:1px solid #d3e0d9;border-radius:5px;background:#fff;color:#3f6353;font-size:12px;font-weight:600}
.tp-ghost:disabled{opacity:.6;cursor:wait}
.tp-empty{margin:26px 0;color:#7c8f86;font-size:12px}
.tp-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:14px}
.tp-card{padding:16px 18px;border:1px solid #dbe5e0;border-radius:8px;background:#fff}
.tp-card.off{background:#fafbfa}
.tp-card-top{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
.tp-card h2{margin:0;display:flex;align-items:center;gap:8px;font-size:14px;font-weight:680}
.tp-group{padding:1px 6px;border-radius:4px;background:#e7f3ee;color:#17856b;font-size:10px;font-weight:650;text-transform:uppercase}
.tp-url{margin:5px 0 0;color:#7c8f86;font-size:11px;word-break:break-all}
.tp-state{padding:2px 8px;border-radius:99px;font-size:10px;font-weight:650;white-space:nowrap}
.tp-state[data-tone="ok"]{background:#e7f6ee;color:#176b58}
.tp-state[data-tone="off"]{background:#eef1ef;color:#7c8f86}
.tp-facts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 14px;margin:14px 0 0}
.tp-facts div{min-width:0}
.tp-facts dt{color:#7c8f86;font-size:10px}
.tp-facts dd{margin:2px 0 0;font-size:12px;font-weight:600;word-break:break-all}
.tp-test{display:flex;align-items:flex-start;gap:6px;margin:12px 0 0;padding:8px 10px;border-radius:5px;font-size:11px;line-height:1.6}
.tp-test[data-tone="ok"]{background:#eef8f2;color:#176b58}
.tp-test[data-tone="bad"]{background:#fcecec;color:#a23232}
.tp-card-actions{display:flex;gap:8px;margin-top:14px;padding-top:12px;border-top:1px solid #edf2ef}
.tp-card-actions .tp-ghost{min-height:30px;padding:0 10px;font-size:11px}
.tp-ghost.danger{color:#a23232;border-color:#eccfcf}
.spin{animation:tp-spin 1s linear infinite}
@keyframes tp-spin{to{transform:rotate(360deg)}}
.tp-table{border:1px solid #dbe5e0;border-radius:8px;background:#fff;overflow:hidden}
.tp-tr{display:grid;grid-template-columns:120px 140px 90px minmax(0,1fr);gap:12px;align-items:center;padding:10px 16px;border-top:1px solid #eef3f0;font-size:12px}
.tp-table.wide .tp-tr{grid-template-columns:150px 70px 150px 120px 150px minmax(0,1fr) 110px}
.tp-tr:first-child{border-top:0}
.tp-th{background:#f7faf8;color:#7c8f86;font-size:10px;font-weight:650;letter-spacing:.04em}
.tp-country{font-weight:650}
.tp-note{color:#7c8f86;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tp-tr select{min-height:30px;padding:0 8px;border:1px solid #d3e0d9;border-radius:5px;background:#fff;font-size:12px}
.tp-badge{padding:2px 7px;border-radius:99px;font-size:10px;font-style:normal;font-weight:650;white-space:nowrap}
.tp-badge[data-tone="ok"]{background:#e7f6ee;color:#176b58}
.tp-badge[data-tone="warn"]{background:#fdf2e2;color:#9a6415}
.tp-badge[data-tone="bad"]{background:#fcecec;color:#a23232}
.tp-badge[data-tone="wait"]{background:#eef1ef;color:#6d8077}
.tp-dialog{position:fixed;inset:0;z-index:60;display:grid;place-items:center;padding:24px;background:#10231d66;overflow:auto}
.tp-dialog-card{width:min(720px,100%);max-height:calc(100vh - 48px);overflow:auto;padding:20px 22px;border-radius:10px;background:#fff;box-shadow:0 18px 48px #0f2a1f2e}
.tp-dialog-card header{display:flex;align-items:flex-start;justify-content:space-between;gap:14px}
.tp-dialog-card h2{margin:0;font-size:16px;font-weight:680}
.tp-dialog-card header p{margin:6px 0 0;color:#7c8f86;font-size:11px}
.tp-close{width:32px;height:32px;display:grid;place-items:center;border-radius:5px;color:#5f7469}
.tp-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 14px;margin:18px 0 6px}
.tp-form label{display:grid;gap:5px;color:#5f7469;font-size:11px;font-weight:600}
.tp-form input,.tp-form select{min-height:34px;padding:0 10px;border:1px solid #d3e0d9;border-radius:5px;background:#fff;color:#20342d;font-size:12px;font-weight:400}
.tp-form .span2{grid-column:span 2}
.tp-form .check{display:flex;align-items:center;gap:8px;font-weight:400}
.tp-form .check input{min-height:0;width:15px;height:15px}
.tp-form input:disabled{background:#f2f5f3;color:#7c8f86;cursor:not-allowed}
.tp-field-note{color:#93a49c;font-size:10px;font-weight:400}
.tp-dialog-card footer{display:flex;justify-content:flex-end;gap:10px;margin-top:16px;padding-top:14px;border-top:1px solid #edf2ef}
@media(max-width:900px){
  .tp-page{padding:22px 16px 40px}
  .tp-cards{grid-template-columns:1fr}
  .tp-tr{grid-template-columns:1fr 1fr}
  .tp-table.wide .tp-tr{grid-template-columns:1fr 1fr}
  .tp-th{display:none}
  .tp-form{grid-template-columns:1fr}
  .tp-form .span2{grid-column:span 1}
}
</style>
