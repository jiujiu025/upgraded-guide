<script setup>
// 面板备份与迁移：备份的是「能原样重建」的东西——每条入站的端口、UUID、
// reality 私钥、shortId，加 Xray 的出站与路由。恢复后客户链接不用换，
// 换机器只要把域名解析指到新 IP。
import { computed, onMounted, reactive, ref } from 'vue'
import {
  Archive, CheckCircle2, CircleAlert, Download, RefreshCw, RotateCcw, Trash2, X,
} from 'lucide-vue-next'
import {
  createTransitBackup, deleteTransitBackup, downloadTransitBackup, getTransitBackups,
  restoreTransitBackup,
} from '../api'

const props = defineProps({
  panels: { type: Array, default: () => [] },
})

const STATUS_LABELS = { ready: '可用', restored: '已恢复', failed: '恢复失败' }
const STATUS_TONE = { ready: 'ok', restored: 'info', failed: 'bad' }

const backups = ref([])
const loading = ref(true)
const error = ref('')
const notice = ref('')
const sourceVpsId = ref('')
const creating = ref(false)
const busyId = ref(0)

const restoreOpen = ref(false)
const restoreTarget = ref('')
const restoreBackup = ref(null)
const plan = ref(null)
const planning = ref(false)
const restoring = ref(false)
const restoreError = ref('')

const enabledPanels = computed(() => props.panels || [])
const targetPanels = computed(() => enabledPanels.value.filter((item) => item.id !== restoreBackup.value?.vps_id))

function flash(message) {
  notice.value = message
  window.setTimeout(() => { if (notice.value === message) notice.value = '' }, 5000)
}

function formatTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (number) => String(number).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function formatSize(bytes) {
  if (!bytes) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await getTransitBackups({ limit: 100 })
    backups.value = data || []
    if (!sourceVpsId.value && enabledPanels.value.length) sourceVpsId.value = String(enabledPanels.value[0].id)
  } catch (err) {
    error.value = err.userMessage || '备份记录加载失败'
  } finally {
    loading.value = false
  }
}

async function createBackup() {
  if (!sourceVpsId.value) return
  creating.value = true
  error.value = ''
  try {
    const { data } = await createTransitBackup(Number(sourceVpsId.value))
    flash(`已备份：${data.inbound_count} 条入站，${formatSize(data.size_bytes)}`)
    await load()
  } catch (err) {
    error.value = err.userMessage || '备份失败'
  } finally {
    creating.value = false
  }
}

async function download(item) {
  busyId.value = item.id
  try {
    const { data } = await downloadTransitBackup(item.id)
    const url = URL.createObjectURL(data)
    const link = document.createElement('a')
    link.href = url
    link.download = item.filename
    link.click()
    URL.revokeObjectURL(url)
  } catch (err) {
    error.value = err.userMessage || '下载失败'
  } finally {
    busyId.value = 0
  }
}

async function remove(item) {
  if (!window.confirm(`删除备份 ${item.filename}？文件会从服务器上移除，不可恢复。`)) return
  busyId.value = item.id
  try {
    await deleteTransitBackup(item.id)
    flash('备份已删除')
    await load()
  } catch (err) {
    error.value = err.userMessage || '删除失败'
  } finally {
    busyId.value = 0
  }
}

function openRestore(item) {
  restoreBackup.value = item
  restoreTarget.value = ''
  plan.value = null
  restoreError.value = ''
  restoreOpen.value = true
}

async function runPlan() {
  if (!restoreTarget.value) { restoreError.value = '请选择目标面板'; return }
  planning.value = true
  restoreError.value = ''
  plan.value = null
  try {
    const { data } = await restoreTransitBackup(restoreBackup.value.id, {
      target_vps_id: Number(restoreTarget.value), dry_run: true,
    })
    plan.value = data
  } catch (err) {
    restoreError.value = err.userMessage || '预检失败'
  } finally {
    planning.value = false
  }
}

async function runRestore() {
  if (!plan.value) return
  restoring.value = true
  restoreError.value = ''
  try {
    const { data } = await restoreTransitBackup(restoreBackup.value.id, {
      target_vps_id: Number(restoreTarget.value), dry_run: false,
    })
    plan.value = data
    flash(`恢复完成：新建 ${data.created?.length || 0} 条，跳过 ${data.skipped?.length || 0} 条`)
    await load()
  } catch (err) {
    restoreError.value = err.userMessage || '恢复失败'
  } finally {
    restoring.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="tb-wrap">
    <div class="tb-toolbar">
      <p>
        备份包含每条入站的端口、UUID、reality 密钥与 Xray 出站/路由。恢复到新面板后
        <b>客户链接不用换</b>，把域名解析指到新机器即可。
      </p>
      <div class="tb-create">
        <select v-model="sourceVpsId">
          <option value="">选择面板</option>
          <option v-for="panel in enabledPanels" :key="panel.id" :value="String(panel.id)">
            {{ panel.name }}（{{ panel.group_code }}）
          </option>
        </select>
        <button class="tb-primary" type="button" :disabled="creating || !sourceVpsId" @click="createBackup">
          <Archive :size="14" />{{ creating ? '备份中…' : '立即备份' }}
        </button>
        <button class="tb-ghost" type="button" :disabled="loading" @click="load">
          <RefreshCw :size="14" />{{ loading ? '加载中…' : '刷新' }}
        </button>
      </div>
    </div>

    <p v-if="notice" class="tb-notice"><CheckCircle2 :size="14" />{{ notice }}</p>
    <p v-if="error" class="tb-error"><CircleAlert :size="14" />{{ error }}</p>

    <p v-if="loading" class="tb-empty">正在读取备份记录…</p>
    <p v-else-if="!backups.length" class="tb-empty">
      还没有备份。选一台面板点「立即备份」，文件会存到服务器本地。
    </p>

    <div v-else class="tb-table">
      <div class="tb-tr tb-th"><span>时间</span><span>面板</span><span>内容</span><span>大小</span><span>状态</span><span>操作</span></div>
      <div v-for="item in backups" :key="item.id" class="tb-tr">
        <span>{{ formatTime(item.created_at) }}<small>{{ item.filename }}</small></span>
        <span><b>{{ item.vps_name }}</b><small>#{{ item.vps_id }}</small></span>
        <span>{{ item.inbound_count }} 入站<small>{{ item.outbound_count }} 出站 · {{ item.rule_count }} 路由</small></span>
        <span>{{ formatSize(item.size_bytes) }}</span>
        <span>
          <em class="tb-badge" :data-tone="STATUS_TONE[item.status] || 'wait'">
            {{ STATUS_LABELS[item.status] || item.status }}
          </em>
          <small v-if="item.restored_at">→ {{ formatTime(item.restored_at) }}</small>
        </span>
        <span class="tb-actions">
          <button type="button" :disabled="busyId === item.id" @click="download(item)" title="下载备份">
            <Download :size="13" />下载
          </button>
          <button type="button" @click="openRestore(item)" title="恢复到另一台面板">
            <RotateCcw :size="13" />恢复
          </button>
          <button type="button" class="danger" :disabled="busyId === item.id" @click="remove(item)">
            <Trash2 :size="13" />
          </button>
        </span>
      </div>
    </div>

    <p class="tb-hint">
      备份文件含 reality 私钥与客户 UUID，权限 600，只允许超级管理员下载。
      恢复前先确认目标面板是空的：同端口已占用会被跳过，不会覆盖已有业务。
    </p>

    <div v-if="restoreOpen" class="tb-dialog" role="dialog" aria-modal="true">
      <div class="tb-dialog-card">
        <header>
          <div>
            <h2>恢复备份到另一台面板</h2>
            <p>{{ restoreBackup?.vps_name }} · {{ restoreBackup?.filename }}</p>
          </div>
          <button class="tb-close" type="button" aria-label="关闭" @click="restoreOpen = false"><X :size="18" /></button>
        </header>

        <div class="tb-field">
          <label>目标面板</label>
          <select v-model="restoreTarget" :disabled="Boolean(plan?.dry_run === false)">
            <option value="">选择一台空面板</option>
            <option v-for="panel in targetPanels" :key="panel.id" :value="String(panel.id)">
              {{ panel.name }}（{{ panel.group_code }} · {{ panel.connect_host }}）
            </option>
          </select>
        </div>

        <p v-if="restoreError" class="tb-error"><CircleAlert :size="14" />{{ restoreError }}</p>

        <div v-if="plan" class="tb-plan">
          <div><span>备份入站</span><b>{{ plan.total_inbounds }}</b></div>
          <div><span>将新建</span><b class="ok">{{ plan.will_create }}</b></div>
          <div><span>端口冲突</span><b :class="{ bad: plan.conflicts?.length }">{{ plan.conflicts?.length || 0 }}</b></div>
          <div><span>出站 / 路由</span><b>{{ plan.outbounds_to_add }} / {{ plan.rules_to_add }}</b></div>
          <p v-if="plan.conflicts?.length" class="tb-warn">
            端口 {{ plan.conflicts.join('、') }} 在目标面板上已被占用，恢复时会跳过这些入站。
          </p>
          <template v-if="plan.created">
            <p class="tb-result">
              已新建 {{ plan.created.length }} 条；跳过 {{ plan.skipped?.length || 0 }} 条；
              失败 {{ plan.failed?.length || 0 }} 条。
            </p>
            <p v-if="plan.failed?.length" class="tb-warn">
              {{ plan.failed.map((item) => `${item.port}：${item.reason}`).join('；') }}
            </p>
          </template>
        </div>

        <footer>
          <button class="tb-ghost" type="button" @click="restoreOpen = false">关闭</button>
          <button v-if="!plan || plan.dry_run" class="tb-ghost" type="button" :disabled="planning" @click="runPlan">
            {{ planning ? '预检中…' : '预检冲突' }}
          </button>
          <button
            v-if="plan && plan.dry_run" class="tb-primary danger"
            type="button" :disabled="restoring" @click="runRestore"
          >{{ restoring ? '恢复中…' : `确认恢复 ${plan.will_create} 条` }}</button>
        </footer>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tb-wrap{display:grid;gap:14px}
.tb-toolbar{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}
.tb-toolbar p{margin:0;color:#5f7469;font-size:12px;line-height:1.7;max-width:620px}
.tb-create{display:flex;gap:8px;flex:none}
.tb-create select{min-height:34px;min-width:170px;padding:0 9px;border:1px solid #d3e0d9;border-radius:5px;background:#fff;font-size:12px}
.tb-primary{display:inline-flex;align-items:center;gap:6px;min-height:34px;padding:0 13px;border-radius:5px;background:#17795f;color:#fff;font-size:12px;font-weight:650}
.tb-primary.danger{background:#b03a35}
.tb-primary:disabled{opacity:.6;cursor:wait}
.tb-ghost{display:inline-flex;align-items:center;gap:6px;min-height:34px;padding:0 12px;border:1px solid #d3e0d9;border-radius:5px;background:#fff;color:#3f6353;font-size:12px;font-weight:600}
.tb-ghost:disabled{opacity:.6;cursor:wait}
.tb-notice{display:flex;align-items:center;gap:7px;margin:0;padding:9px 12px;border:1px solid #c6e4d5;border-radius:6px;background:#e8f5ee;color:#176b58;font-size:12px}
.tb-error{display:flex;align-items:center;gap:7px;margin:0;padding:9px 12px;border:1px solid #f2cfcf;border-radius:6px;background:#fcecec;color:#a23232;font-size:12px}
.tb-empty{margin:20px 0;color:#7c8f86;font-size:12px}
.tb-table{border:1px solid #dbe5e0;border-radius:8px;background:#fff;overflow:hidden}
.tb-tr{display:grid;grid-template-columns:150px 150px 130px 80px 120px minmax(0,1fr);gap:10px;align-items:center;padding:10px 14px;border-top:1px solid #eef3f0;font-size:12px}
.tb-tr:first-child{border-top:0}
.tb-th{background:#f7faf8;color:#7c8f86;font-size:10px;font-weight:650}
.tb-tr b{display:block;font-size:12px}
.tb-tr small{display:block;color:#83958c;font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tb-badge{display:inline-block;padding:2px 7px;border-radius:99px;font-size:10px;font-style:normal;font-weight:650}
.tb-badge[data-tone="ok"]{background:#e7f6ee;color:#176b58}
.tb-badge[data-tone="info"]{background:#e8f1f5;color:#2c6577}
.tb-badge[data-tone="bad"]{background:#fcecec;color:#a23232}
.tb-badge[data-tone="wait"]{background:#eef1ef;color:#6d8077}
.tb-actions{display:flex;gap:6px;justify-content:flex-end}
.tb-actions button{display:inline-flex;align-items:center;gap:4px;min-height:26px;padding:0 8px;border:1px solid #d3e0d9;border-radius:5px;background:#fff;color:#3f6353;font-size:11px;font-weight:600}
.tb-actions button.danger{color:#a23232;border-color:#eccfcf}
.tb-actions button:disabled{opacity:.5;cursor:wait}
.tb-hint{margin:0;color:#83958c;font-size:11px;line-height:1.7}
.tb-dialog{position:fixed;inset:0;z-index:60;display:grid;place-items:center;padding:24px;background:#10231d66}
.tb-dialog-card{width:min(620px,100%);max-height:calc(100vh - 48px);overflow:auto;padding:20px 22px;border-radius:10px;background:#fff;box-shadow:0 18px 48px #0f2a1f2e}
.tb-dialog-card header{display:flex;align-items:flex-start;justify-content:space-between;gap:14px}
.tb-dialog-card h2{margin:0;font-size:16px;font-weight:680}
.tb-dialog-card header p{margin:6px 0 0;color:#7c8f86;font-size:11px}
.tb-close{width:32px;height:32px;display:grid;place-items:center;border-radius:5px;color:#5f7469}
.tb-field{display:grid;gap:6px;margin:18px 0 12px}
.tb-field label{color:#5f7469;font-size:11px;font-weight:600}
.tb-field select{min-height:36px;padding:0 10px;border:1px solid #d3e0d9;border-radius:5px;background:#fff;font-size:12px}
.tb-plan{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:14px 0;padding:14px;border:1px solid #e0e9e5;border-radius:8px;background:#f7faf8}
.tb-plan div{display:grid;gap:4px}
.tb-plan span{color:#7c8f86;font-size:10px}
.tb-plan b{font-size:18px;font-weight:700}
.tb-plan b.ok{color:#176b58}
.tb-plan b.bad{color:#b03a35}
.tb-warn{grid-column:1/-1;margin:4px 0 0;color:#8a5a13;font-size:11px;line-height:1.6}
.tb-result{grid-column:1/-1;margin:6px 0 0;color:#176b58;font-size:11px}
.tb-dialog-card footer{display:flex;justify-content:flex-end;gap:10px;margin-top:16px;padding-top:14px;border-top:1px solid #edf2ef}
@media(max-width:1000px){
  .tb-tr{grid-template-columns:1fr 1fr}
  .tb-th{display:none}
  .tb-toolbar{flex-direction:column}
  .tb-plan{grid-template-columns:1fr 1fr}
}
</style>
