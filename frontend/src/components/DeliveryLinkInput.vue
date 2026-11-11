<script setup>
// 交付时录入线路：优先「粘贴分享链接」，也可以切成手动填写。
// 这里只做前端能确定的判断（协议、服务器、端口、传输方式），完整校验以交付接口为准——
// 后端会用同一套解析器再解一次，解析不了直接 422，不会把坏链接发给客户。
import { computed, reactive, ref, watch } from 'vue'
import { ClipboardPaste, Link2, PencilLine, Trash2 } from 'lucide-vue-next'

const props = defineProps({
  modelValue: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])

const PROTOCOLS = [
  { value: 'vless', label: 'VLESS' },
  { value: 'vmess', label: 'VMess' },
  { value: 'trojan', label: 'Trojan' },
  { value: 'ss', label: 'Shadowsocks' },
  { value: 'hysteria2', label: 'Hysteria2' },
  { value: 'tuic', label: 'TUIC' },
]
const TRANSPORTS = ['tcp', 'ws', 'grpc', 'h2']
const TLS_PROTOCOLS = ['trojan', 'hysteria2', 'tuic']

const manual = ref(false)
const manualForm = reactive({
  protocol: 'vless', server: '', port: '', credential: '',
  sni: '', transport: 'tcp', path: '/', name: '',
})

const raw = computed({
  get: () => props.modelValue || '',
  set: (value) => emit('update:modelValue', value),
})

// 前端解析只用于即时回显。vmess 的内容是 base64(JSON)，交给后端解，不在浏览器里做。
function analyze(text) {
  const value = (text || '').trim()
  const match = /^([a-z0-9]+):\/\//i.exec(value)
  if (!match) return null
  let protocol = match[1].toLowerCase()
  if (protocol === 'hy2') protocol = 'hysteria2'
  if (protocol === 'vmess') {
    return { protocol, server: '', port: '', name: 'VMess 链接', transport: '', tls: '' }
  }
  try {
    const url = new URL(value)
    const params = url.searchParams
    return {
      protocol,
      server: url.hostname,
      port: url.port,
      name: decodeURIComponent((url.hash || '').slice(1)),
      transport: params.get('type') || 'tcp',
      tls: params.get('security') || (TLS_PROTOCOLS.includes(protocol) ? 'tls' : 'none'),
      sni: params.get('sni') || params.get('peer') || '',
    }
  } catch {
    return null
  }
}

const parsed = computed(() => analyze(raw.value))
const isKnownProtocol = computed(() => Boolean(parsed.value))

function toBase64(text) {
  const bytes = new TextEncoder().encode(text)
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return btoa(binary)
}

// 手动填写后拼回一条标准链接，粘回输入框，交付时与粘贴走同一条链路。
function buildLink() {
  const form = manualForm
  const server = form.server.trim()
  const credential = form.credential.trim()
  const port = Number(form.port)
  if (!server || !credential || !port) return ''
  const name = form.name.trim()
  if (form.protocol === 'vmess') {
    const payload = {
      v: '2', ps: name, add: server, port: String(port), id: credential, aid: '0',
      net: form.transport, type: 'none', host: form.sni.trim(), path: form.path.trim(),
      tls: form.sni.trim() ? 'tls' : '', sni: form.sni.trim(),
    }
    return `vmess://${toBase64(JSON.stringify(payload))}`
  }
  if (form.protocol === 'ss') {
    return `ss://${toBase64(credential)}@${server}:${port}${name ? `#${encodeURIComponent(name)}` : ''}`
  }
  const params = new URLSearchParams()
  if (form.transport) params.set('type', form.transport)
  if (form.sni.trim()) {
    params.set('security', 'tls')
    params.set('sni', form.sni.trim())
  } else if (TLS_PROTOCOLS.includes(form.protocol)) {
    params.set('security', 'tls')
  }
  if (form.transport === 'ws' && form.path.trim()) params.set('path', form.path.trim())
  const query = params.toString()
  return `${form.protocol}://${encodeURIComponent(credential)}@${server}:${port}${query ? `?${query}` : ''}${name ? `#${encodeURIComponent(name)}` : ''}`
}

function applyManual() {
  const link = buildLink()
  if (!link) return
  raw.value = link
  manual.value = false
}

async function pasteFromClipboard() {
  if (props.disabled) return
  try {
    const text = await navigator.clipboard.readText()
    if (text) raw.value = text.trim()
  } catch {
    // 浏览器不给读剪贴板就让人自己粘贴，不弹错误打断填写
  }
}

function clearLink() {
  raw.value = ''
}

watch(() => props.modelValue, (value) => {
  // 从外部带进来的链接（例如刷新后回填）直接拆到手动表单里，方便微调
  if (!value) return
  const info = analyze(value)
  if (!info || info.protocol === 'vmess') return
  Object.assign(manualForm, {
    protocol: info.protocol, server: info.server, port: info.port,
    sni: info.sni || '', transport: info.transport || 'tcp', name: info.name || '',
  })
}, { immediate: true })
</script>

<template>
  <div class="link-input">
    <div class="link-head">
      <span class="link-title"><Link2 :size="14" /> 节点链接</span>
      <button class="link-switch" type="button" :disabled="disabled" @click="manual = !manual">
        <PencilLine :size="13" />{{ manual ? '改为粘贴链接' : '手动填写' }}
      </button>
    </div>

    <template v-if="!manual">
      <textarea
        v-model="raw"
        class="link-text"
        rows="2"
        maxlength="4000"
        spellcheck="false"
        :disabled="disabled"
        placeholder="粘贴 vless:// / vmess:// / trojan:// / ss:// / hysteria2:// / tuic:// 链接，交付后自动生成二维码与 Clash 订阅"
      />
      <div class="link-actions">
        <button class="link-mini" type="button" :disabled="disabled" @click="pasteFromClipboard">
          <ClipboardPaste :size="13" />读取剪贴板
        </button>
        <button v-if="raw" class="link-mini" type="button" :disabled="disabled" @click="clearLink">
          <Trash2 :size="13" />清空
        </button>
      </div>
      <p v-if="raw && parsed" class="link-ok">
        已识别 <b>{{ parsed.protocol.toUpperCase() }}</b>
        <template v-if="parsed.server"> · {{ parsed.server }}:{{ parsed.port }}</template>
        <template v-if="parsed.transport"> · {{ parsed.transport }}</template>
        <template v-if="parsed.tls && parsed.tls !== 'none'"> · {{ parsed.tls }}</template>
        <template v-if="parsed.name"> · {{ parsed.name }}</template>
      </p>
      <p v-else-if="raw" class="link-bad">没认出这是哪种链接，交付时后端会再校验，大概率会被拒。</p>
    </template>

    <div v-else class="manual-grid">
      <label><span>协议</span>
        <select v-model="manualForm.protocol">
          <option v-for="item in PROTOCOLS" :key="item.value" :value="item.value">{{ item.label }}</option>
        </select>
      </label>
      <label><span>服务器</span><input v-model="manualForm.server" placeholder="us.example.com" /></label>
      <label><span>端口</span><input v-model="manualForm.port" type="number" min="1" max="65535" placeholder="443" /></label>
      <label class="wide"><span>{{ manualForm.protocol === 'ss' ? '加密方式:密码' : 'UUID / 密码' }}</span>
        <input v-model="manualForm.credential" :placeholder="manualForm.protocol === 'ss' ? 'aes-256-gcm:password' : 'uuid 或密码'" />
      </label>
      <label><span>SNI（走 TLS 时填）</span><input v-model="manualForm.sni" placeholder="us.example.com" /></label>
      <label><span>传输</span>
        <select v-model="manualForm.transport">
          <option v-for="item in TRANSPORTS" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <label v-if="manualForm.transport === 'ws'"><span>WS 路径</span><input v-model="manualForm.path" placeholder="/ws" /></label>
      <label><span>备注名</span><input v-model="manualForm.name" placeholder="客户端里显示的名字" /></label>
      <button class="link-mini wide" type="button" :disabled="disabled" @click="applyManual">生成链接</button>
    </div>
    <p class="link-hint">二维码与 Clash 订阅由后端按这条链接生成，不用另填。</p>
  </div>
</template>

<style scoped>
.link-input { display: grid; gap: 7px; }
.link-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.link-title { display: inline-flex; align-items: center; gap: 6px; font-size: 11px; color: #5c7268; font-weight: 600; }
.link-switch { display: inline-flex; align-items: center; gap: 4px; padding: 3px 8px; border: 1px solid #d5e0da; border-radius: 4px; background: #fff; color: #5c7268; font-size: 10px; cursor: pointer; }
.link-switch:hover:not(:disabled) { background: #f2f7f4; }
.link-text { width: 100%; padding: 8px 9px; border: 1px solid #cfdad4; border-radius: 4px; background: #fff; color: #2d4439; font: 11px/1.5 ui-monospace, monospace; resize: vertical; }
.link-actions { display: flex; gap: 6px; }
.link-mini { display: inline-flex; align-items: center; gap: 4px; padding: 4px 9px; border: 1px solid #d5e0da; border-radius: 4px; background: #fff; color: #4f6559; font-size: 10px; cursor: pointer; }
.link-mini:hover:not(:disabled) { background: #f2f7f4; }
.link-mini:disabled { opacity: .55; cursor: not-allowed; }
.link-ok { margin: 0; padding: 6px 8px; border-radius: 4px; background: #e8f5ef; color: #1f6b53; font-size: 10px; }
.link-bad { margin: 0; padding: 6px 8px; border-radius: 4px; background: #fdf0ec; color: #9a4a39; font-size: 10px; }
.link-hint { margin: 0; color: #82918c; font-size: 9px; }
.manual-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 7px; }
.manual-grid > label { display: grid; gap: 4px; font-size: 9px; color: #6b7d75; }
.manual-grid > label.wide { grid-column: 1 / -1; }
.manual-grid input, .manual-grid select { height: 30px; padding: 0 8px; border: 1px solid #cfdad4; border-radius: 4px; background: #fff; color: #2d4439; font: inherit; font-size: 11px; }
.manual-grid .link-mini.wide { grid-column: 1 / -1; justify-content: center; height: 30px; }
@media (max-width: 760px) { .manual-grid { grid-template-columns: 1fr 1fr; } }
</style>
