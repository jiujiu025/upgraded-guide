<script setup>
// 管理员的通知渠道管理：企微应用消息（推个人）/ 企微群机器人（推群）。
// 渠道密钥加密落库，页面只在创建/更新时提交，绝不回显。
import { computed, onMounted, reactive, ref } from 'vue'
import { Bell, Plus, RefreshCw, Send, Trash2 } from 'lucide-vue-next'
import {
  createAdminNotificationChannel, deleteAdminChannelBinding, getAdminChannelBindings,
  getAdminNotificationChannels, testAdminNotificationChannel, updateAdminNotificationChannel,
  createAdminChannelBinding, getAdminNotificationDeliveries,
} from '../api'

const channels = ref([])
const deliveries = ref([])
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const selectedId = ref(null)
const bindings = ref([])
const bindingForm = reactive({ user_id: '', target: '' })
const createForm = reactive({ channel_type: 'wecom_webhook', name: '', webhook_url: '', corpid: '', agent_id: '', secret: '' })
const appConfigTemplate = { corpid: '', agent_id: '', secret: '' }
const showCreate = ref(false)

const selected = computed(() => channels.value.find((item) => item.id === selectedId.value) || null)

function showNotice(message) {
  notice.value = message
  window.clearTimeout(showNotice.timer)
  showNotice.timer = window.setTimeout(() => { notice.value = '' }, 3200)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [channelResponse, deliveryResponse] = await Promise.all([
      getAdminNotificationChannels(),
      getAdminNotificationDeliveries({ limit: 30 }).catch(() => ({ data: [] })),
    ])
    channels.value = channelResponse.data
    deliveries.value = deliveryResponse.data
    if (!channels.value.some((item) => item.id === selectedId.value)) {
      selectedId.value = channels.value[0]?.id || null
    }
    if (selectedId.value) await loadBindings()
  } catch (requestError) {
    error.value = requestError.userMessage || '通知渠道加载失败'
  } finally {
    loading.value = false
  }
}

async function loadBindings() {
  if (!selectedId.value) { bindings.value = []; return }
  try {
    bindings.value = (await getAdminChannelBindings(selectedId.value)).data
  } catch {
    bindings.value = []
  }
}

function selectChannel(id) {
  selectedId.value = id
  bindingForm.user_id = ''
  bindingForm.target = ''
  loadBindings()
}

async function createChannel() {
  if (busy.value) return
  if (!createForm.name.trim()) { error.value = '请填写渠道名称'; return }
  const config = createForm.channel_type === 'wecom_app'
    ? { corpid: createForm.corpid.trim(), agent_id: createForm.agent_id.trim(), secret: createForm.secret.trim() }
    : { webhook_url: createForm.webhook_url.trim() }
  busy.value = true
  error.value = ''
  try {
    const { data } = await createAdminNotificationChannel({
      channel_type: createForm.channel_type,
      name: createForm.name.trim(),
      config,
    })
    showCreate.value = false
    Object.assign(createForm, { channel_type: 'wecom_webhook', name: '', webhook_url: '', corpid: '', agent_id: '', secret: '' })
    await load()
    selectedId.value = data.id
    showNotice(`渠道「${data.name}」已创建`)
  } catch (requestError) {
    error.value = requestError.userMessage || requestError.message || '创建渠道失败，请检查配置'
  } finally {
    busy.value = false
  }
}

async function toggleChannel(channel) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await updateAdminNotificationChannel(channel.id, { is_enabled: !channel.is_enabled })
    channel.is_enabled = !channel.is_enabled
    showNotice(channel.is_enabled ? '渠道已启用' : '渠道已停用（存量待发投递会标记跳过）')
  } catch (requestError) {
    error.value = requestError.userMessage || '操作失败'
  } finally {
    busy.value = false
  }
}

async function testChannel(channel) {
  if (busy.value) return
  const target = channel.needs_binding ? window.prompt('发给哪个目标？（企微 userid）') : 'group'
  if (!target) return
  busy.value = true
  error.value = ''
  try {
    await testAdminNotificationChannel(channel.id, { target })
    showNotice('测试消息已发出，请到对应企微确认')
  } catch (requestError) {
    error.value = requestError.userMessage || requestError.message || '测试发送失败'
  } finally {
    busy.value = false
  }
}

async function addBinding() {
  if (busy.value || !selected.value) return
  if (!bindingForm.user_id.trim() || !bindingForm.target.trim()) { error.value = '请填写用户 ID 和推送目标'; return }
  busy.value = true
  error.value = ''
  try {
    await createAdminChannelBinding(selected.value.id, {
      user_id: Number(bindingForm.user_id.trim()),
      target: bindingForm.target.trim(),
    })
    bindingForm.user_id = ''
    bindingForm.target = ''
    await loadBindings()
    showNotice('绑定已添加')
  } catch (requestError) {
    error.value = requestError.userMessage || requestError.message || '绑定失败'
  } finally {
    busy.value = false
  }
}

async function removeBinding(binding) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await deleteAdminChannelBinding(selected.value.id, binding.id)
    await loadBindings()
    showNotice(`已解绑 ${binding.display_name}`)
  } catch (requestError) {
    error.value = requestError.userMessage || '解绑失败'
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="channel-console">
    <header class="channel-head">
      <div>
        <h1><Bell :size="22" /> 通知渠道</h1>
        <p>站内信照旧保留；这里配置把哪些通知额外推到企业微信。分发由定时任务每分钟执行，发送失败不影响业务。</p>
      </div>
      <div class="head-actions">
        <button class="refresh-button" type="button" :disabled="loading" @click="load"><RefreshCw :size="16" :class="{ spin: loading }" /></button>
        <button class="create-button" type="button" @click="showCreate = !showCreate"><Plus :size="15" /> 新建渠道</button>
      </div>
    </header>

    <p v-if="notice" class="console-notice">{{ notice }}</p>
    <p v-if="error" class="console-error" role="alert">{{ error }}</p>

    <section v-if="showCreate" class="channel-create">
      <h2>新建通知渠道</h2>
      <div class="create-grid">
        <label>渠道类型
          <select v-model="createForm.channel_type">
            <option value="wecom_webhook">企业微信群机器人（推群，无需绑定）</option>
            <option value="wecom_app">企业微信应用消息（推个人，需绑定 userid）</option>
          </select>
        </label>
        <label>渠道名称<input v-model="createForm.name" maxlength="60" placeholder="如：运维告警群" /></label>
        <template v-if="createForm.channel_type === 'wecom_webhook'">
          <label class="wide">群机器人 Webhook 地址<input v-model="createForm.webhook_url" placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..." /></label>
        </template>
        <template v-else>
          <label>企业 ID（corpid）<input v-model="createForm.corpid" placeholder="ww 开头" /></label>
          <label>应用 ID（agentid）<input v-model="createForm.agent_id" placeholder="如 1000002" /></label>
          <label class="wide">应用 Secret<input v-model="createForm.secret" type="password" placeholder="仅提交时上传，落库加密、永不回显" /></label>
        </template>
      </div>
      <div class="create-actions">
        <button class="create-button" type="button" :disabled="busy" @click="createChannel">{{ busy ? '创建中…' : '创建渠道' }}</button>
        <button type="button" @click="showCreate = false">取消</button>
      </div>
    </section>

    <div class="channel-layout">
      <section class="channel-list" aria-label="渠道列表">
        <div v-if="!channels.length && !loading" class="channel-empty">
          还没有通知渠道。先建一个群机器人最简单：企微群里右键添加机器人，把 webhook 地址填进来即可。
        </div>
        <button v-for="channel in channels" :key="channel.id" type="button" class="channel-row" :class="{ selected: channel.id === selectedId }" @click="selectChannel(channel.id)">
          <span class="channel-main">
            <b>{{ channel.name }}</b>
            <small>{{ channel.channel_type_label }}<template v-if="channel.push_categories"> · 只推 {{ channel.push_categories }}</template><template v-else> · 推全部分类</template></small>
          </span>
          <span class="channel-side" :data-state="channel.is_enabled ? 'active' : 'disabled'">{{ channel.is_enabled ? '启用' : '停用' }}</span>
        </button>
      </section>

      <section v-if="selected" class="channel-detail">
        <div class="detail-head">
          <div><h2>{{ selected.name }}</h2><p>{{ selected.channel_type_label }} · 每 1 分钟分发一次，只补发 24 小时内的通知</p></div>
          <div class="detail-actions">
            <button type="button" class="outline-button" :disabled="busy" @click="testChannel(selected)"><Send :size="14" /> 发测试消息</button>
            <button type="button" :class="selected.is_enabled ? 'danger-button' : 'outline-button'" :disabled="busy" @click="toggleChannel(selected)">{{ selected.is_enabled ? '停用' : '启用' }}</button>
          </div>
        </div>

        <div v-if="selected.needs_binding" class="binding-block">
          <h3>推送绑定</h3>
          <p class="binding-hint">绑定系统账号与企微 userid（通常同名）。销售收到的是自己客户相关的通知。</p>
          <div v-if="!bindings.length" class="channel-empty small">还没有绑定，通知不会推给任何人。</div>
          <div v-for="binding in bindings" :key="binding.id" class="binding-row">
            <span><b>{{ binding.display_name }}</b><small>{{ binding.username }} → {{ binding.target }}</small></span>
            <button type="button" class="danger-button" :disabled="busy" @click="removeBinding(binding)"><Trash2 :size="13" /> 解绑</button>
          </div>
          <form class="binding-form" @submit.prevent="addBinding">
            <input v-model="bindingForm.user_id" placeholder="用户 ID（数字）" inputmode="numeric" />
            <input v-model="bindingForm.target" placeholder="企微 userid" maxlength="120" />
            <button class="outline-button" type="submit" :disabled="busy">添加绑定</button>
          </form>
        </div>
        <div v-else class="binding-block">
          <h3>推送目标</h3>
          <p class="binding-hint">群机器人直接推整个群，无需绑定。发测试消息即可验证 webhook 是否可用。</p>
        </div>

        <div class="delivery-block">
          <h3>最近投递</h3>
          <div v-if="!deliveries.length" class="channel-empty small">还没有投递记录——有新通知后约 1 分钟内会出现。</div>
          <div v-for="delivery in deliveries.filter((item) => item.channel_id === selected.id)" :key="delivery.id" class="delivery-row">
            <span class="delivery-status" :data-status="delivery.status">{{ { sent: '已送达', failed: '失败', pending: '待发', skipped: '跳过' }[delivery.status] || delivery.status }}</span>
            <span class="delivery-target">{{ delivery.target }}</span>
            <small class="delivery-error" v-if="delivery.last_error" :title="delivery.last_error">{{ delivery.last_error }}</small>
            <small class="delivery-meta">#{{ delivery.notification_id }} · 尝试 {{ delivery.attempts }} 次</small>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.channel-console{min-height:100vh;padding:30px clamp(18px,3vw,42px) 40px;background:#f2f5f3;color:#253b33}
.channel-head{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-bottom:20px}
.channel-head h1{margin:0;font-size:24px;display:flex;align-items:center;gap:9px}
.channel-head p{margin:6px 0 0;color:#71827b;font-size:12px;max-width:640px}
.head-actions{display:flex;gap:8px}
.create-button,.outline-button,.danger-button,.refresh-button{min-height:36px;display:inline-flex;align-items:center;justify-content:center;gap:7px;padding:0 12px;border:1px solid #cbd8d2;border-radius:5px;background:#fff;color:#35594c;font-size:11px;font-weight:700}
.create-button{border-color:#277d68;background:#277d68;color:#fff}
.create-button:hover{background:#1d6b58}
.outline-button:hover,.refresh-button:hover{border-color:#75a996;background:#f6faf8}
.danger-button{border-color:#dbb8b3;color:#a54b42}
.danger-button:hover{background:#fff3f1}
.refresh-button{width:36px;padding:0}
button:disabled{opacity:.5;cursor:not-allowed}
.console-notice,.console-error{display:flex;align-items:center;gap:8px;margin:0 0 12px;padding:10px 12px;border:1px solid #b9dbcb;border-radius:5px;background:#edf8f1;color:#286c50;font-size:11px}
.console-error{border-color:#e3bdb7;background:#fff2ef;color:#9a493f}
.channel-create{border:1px solid #d9e3de;border-radius:6px;background:#fff;padding:16px;margin-bottom:14px}
.channel-create h2{margin:0;font-size:14px}
.create-grid{display:grid;grid-template-columns:repeat(2,minmax(180px,1fr));gap:10px;margin-top:12px}
.create-grid label{display:grid;gap:5px;color:#5c7068;font-size:10px}
.create-grid label.wide{grid-column:1/-1}
.create-grid input,.create-grid select{height:35px;padding:0 9px;border:1px solid #cbd8d2;border-radius:4px;background:#fff;color:#2b4138;font-size:11px}
.create-actions{display:flex;gap:8px;margin-top:12px}
.channel-layout{display:grid;grid-template-columns:minmax(280px,360px) minmax(0,1fr);gap:14px;align-items:start}
.channel-list{border:1px solid #d9e3de;border-radius:6px;background:#fff;overflow:hidden}
.channel-row{width:100%;display:flex;align-items:center;gap:10px;padding:12px;border-top:1px solid #edf1ef;text-align:left}
.channel-row:first-child{border-top:0}
.channel-row:hover{background:#f7faf8}
.channel-row.selected{background:#edf6f1}
.channel-main{min-width:0;display:grid;gap:4px}
.channel-main b{font-size:12px}
.channel-main small{color:#788980;font-size:10px}
.channel-side{margin-left:auto;font-size:10px;font-weight:700}
.channel-side[data-state="active"]{color:#2b8268}
.channel-side[data-state="disabled"]{color:#a64f44}
.channel-empty{padding:22px;color:#7d8c85;font-size:11px;line-height:1.7}
.channel-empty.small{padding:12px 0}
.channel-detail{border:1px solid #d9e3de;border-radius:6px;background:#fff;padding:17px 18px;min-width:0}
.detail-head{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.detail-head h2{margin:0;font-size:17px}
.detail-head p{margin:4px 0 0;color:#71827a;font-size:10px}
.detail-actions{display:flex;gap:8px}
.binding-block,.delivery-block{margin-top:18px;padding-top:16px;border-top:1px solid #e6ece9}
.binding-block h3,.delivery-block h3{margin:0 0 6px;font-size:13px}
.binding-hint{margin:0 0 10px;color:#77877f;font-size:10px}
.binding-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:9px 0;border-bottom:1px solid #eef2f0}
.binding-row b{font-size:11px}
.binding-row small{display:block;color:#788980;font-size:9px}
.binding-form{display:grid;grid-template-columns:1fr 1fr auto;gap:8px;margin-top:12px}
.binding-form input{height:34px;padding:0 9px;border:1px solid #cbd8d2;border-radius:4px;background:#fff;font-size:11px}
.delivery-row{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid #eef2f0;font-size:10px}
.delivery-status{min-width:48px;font-weight:700}
.delivery-status[data-status="sent"]{color:#2b8268}
.delivery-status[data-status="failed"]{color:#a64f44}
.delivery-status[data-status="pending"]{color:#8a6321}
.delivery-status[data-status="skipped"]{color:#7c8b86}
.delivery-target{font-family:ui-monospace,monospace}
.delivery-error{color:#a64f44;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:280px}
.delivery-meta{margin-left:auto;color:#839189;white-space:nowrap}
.spin{animation:channel-spin .8s linear infinite}
@keyframes channel-spin{to{transform:rotate(360deg)}}
@media(max-width:900px){.channel-layout{grid-template-columns:1fr}.binding-form{grid-template-columns:1fr}}
@media(prefers-reduced-motion:reduce){.spin{animation:none}}
</style>
