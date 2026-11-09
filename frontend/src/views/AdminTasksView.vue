<script setup>
import { computed, onMounted, ref } from 'vue'
import { AlertTriangle, Clock3, RefreshCw, Play, Timer, X } from 'lucide-vue-next'
import { getTaskCatalog, getTaskRuns, runTaskNow } from '../api'

const loading = ref(true)
const error = ref('')
const notice = ref('')
const catalog = ref([])
const runs = ref([])
const running = ref('')
const filterTask = ref('')

const statusMeta = {
  success: { label: '成功', tone: 'good' },
  failed: { label: '失败', tone: 'bad' },
  skipped: { label: '跳过', tone: 'muted' },
}
const triggerLabel = { scheduler: '调度', cli: '命令行', manual_api: '手动触发', test: '测试' }

const visibleRuns = computed(() => filterTask.value ? runs.value.filter((item) => item.task_name === filterTask.value) : runs.value)
const lastRunOf = (name) => runs.value.find((item) => item.task_name === name)
const failureCount = computed(() => runs.value.filter((item) => item.status === 'failed').length)

function showNotice(message) {
  notice.value = message
  window.clearTimeout(showNotice.timer)
  showNotice.timer = window.setTimeout(() => { notice.value = '' }, 3600)
}

function formatTime(value) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
}

function durationLabel(value) {
  if (value == null) return '—'
  return value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${value}ms`
}

function summaryPairs(value) {
  if (!value) return []
  try {
    const parsed = JSON.parse(value)
    if (!parsed || typeof parsed !== 'object') return []
    return Object.entries(parsed).slice(0, 6).map(([key, item]) => `${key}=${Array.isArray(item) ? item.join('/') : item}`)
  } catch { return [value] }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [catalogResult, runsResult] = await Promise.all([getTaskCatalog(), getTaskRuns({ limit: 100 })])
    catalog.value = catalogResult.data
    runs.value = runsResult.data
  } catch (loadError) {
    error.value = loadError.response?.data?.detail || loadError.message || '任务流水加载失败'
  } finally {
    loading.value = false
  }
}

// 后端改成 202 + 后台执行（供应商刷新可能几十秒）。这里轮询流水，等到本次新增的
// 那条跑完再报结果，避免"点了没反应"或误以为失败。
async function waitForRun(name, afterId, timeoutMs = 45000) {
  const startedAt = Date.now()
  while (Date.now() - startedAt < timeoutMs) {
    await new Promise((resolve) => setTimeout(resolve, 2000))
    try {
      const { data } = await getTaskRuns({ limit: 50, task_name: name })
      const hit = data.find((item) => item.id > afterId)
      if (hit && hit.status !== 'running') return hit
    } catch { /* 轮询失败继续等下一轮，最终由超时兜底 */ }
  }
  return null
}

async function trigger(name) {
  running.value = name
  try {
    const afterId = runs.value.reduce((max, item) => Math.max(max, item.id), 0)
    await runTaskNow(name)
    showNotice(`${name} 已开始执行，等待结果…`)
    const finished = await waitForRun(name, afterId)
    if (!finished) {
      showNotice(`${name} 仍在执行，稍后刷新流水查看结果`)
    } else if (finished.status === 'failed') {
      showNotice(`${name} 执行失败：${finished.error || '详见流水'}`)
    } else if (finished.status === 'skipped') {
      showNotice(`${name} 被跳过（可能有其他实例正在执行）`)
    } else {
      showNotice(`${name} 执行完成：${finished.summary || '成功'}`)
    }
  } catch (runError) {
    showNotice(runError.response?.data?.detail || '任务触发失败，请查看流水中的错误信息')
  } finally {
    running.value = ''
    await load()
  }
}

onMounted(load)
</script>

<template>
  <section class="page task-page">
    <div class="hero-row">
      <div>
        <p class="eyebrow">系统运维 / 自动任务</p>
        <h2>让该自动的事，<em>不再靠人点按钮。</em></h2>
        <p class="muted">调度进程按间隔执行关单、到期提醒与支付对账；这里只读执行流水，不修改业务数据。</p>
      </div>
      <button class="action" :disabled="loading" @click="load"><RefreshCw :size="15" />刷新流水</button>
    </div>

    <div v-if="notice" class="task-toast">{{ notice }}</div>

    <div class="metric-grid">
      <article><small>已注册任务</small><strong>{{ catalog.length }}</strong><span>由 worker 容器执行</span></article>
      <article><small>近 100 次执行</small><strong>{{ runs.length }}</strong><span>含手动触发</span></article>
      <article><small>失败次数</small><strong :class="failureCount ? 'bad' : 'good'">{{ failureCount }}</strong><span>失败会写错误详情</span></article>
      <article><small>最近执行</small><strong class="time">{{ runs[0] ? formatTime(runs[0].started_at) : '—' }}</strong><span>{{ runs[0]?.task_name || '尚未运行' }}</span></article>
    </div>

    <section v-if="error" class="task-state">
      <AlertTriangle :size="26" />
      <h3>流水暂时无法加载</h3>
      <p>{{ error }}</p>
      <button class="outline-action" @click="load">重新加载</button>
    </section>

    <template v-else>
      <section class="panel">
        <div class="panel-head">
          <div><small class="eyebrow">任务清单</small><h3>当前调度内容</h3></div>
          <span class="badge">{{ loading ? '加载中' : '自动执行' }}</span>
        </div>
        <div class="task-catalog">
          <article v-for="spec in catalog" :key="spec.name">
            <div>
              <h4>{{ spec.title }}</h4>
              <p><Timer :size="13" />每 {{ spec.interval_minutes }} 分钟 · {{ spec.name }}</p>
              <p class="task-last" v-if="lastRunOf(spec.name)">
                上次：{{ formatTime(lastRunOf(spec.name).started_at) }}
                <em :data-tone="statusMeta[lastRunOf(spec.name).status]?.tone">{{ statusMeta[lastRunOf(spec.name).status]?.label || lastRunOf(spec.name).status }}</em>
              </p>
              <p class="task-last" v-else>尚未执行过</p>
            </div>
            <button class="outline-action" :disabled="Boolean(running)" @click="trigger(spec.name)">
              <Play :size="14" />{{ running === spec.name ? '执行中' : '立即运行' }}
            </button>
          </article>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <div><small class="eyebrow">执行流水</small><h3>最近运行记录</h3></div>
          <select v-model="filterTask" class="task-filter">
            <option value="">全部任务</option>
            <option v-for="spec in catalog" :key="spec.name" :value="spec.name">{{ spec.title }}</option>
          </select>
        </div>

        <div v-if="loading" class="task-state"><Clock3 :size="24" /><p>正在读取流水…</p></div>
        <div v-else-if="!visibleRuns.length" class="task-state"><Clock3 :size="24" /><h3>还没有执行记录</h3><p>worker 容器启动后会自动写入，也可以先点上面的「立即运行」。</p></div>
        <div v-else class="task-table">
          <div class="task-table-head"><span>开始时间</span><span>任务</span><span>状态</span><span>耗时</span><span>触发</span><span>结果摘要</span></div>
          <div v-for="run in visibleRuns" :key="run.id" class="task-table-row">
            <span data-label="开始时间">{{ formatTime(run.started_at) }}</span>
            <span data-label="任务"><b>{{ run.task_name }}</b></span>
            <span data-label="状态"><em class="task-status" :data-tone="statusMeta[run.status]?.tone">{{ statusMeta[run.status]?.label || run.status }}</em></span>
            <span data-label="耗时">{{ durationLabel(run.duration_ms) }}</span>
            <span data-label="触发">{{ triggerLabel[run.triggered_by] || run.triggered_by }}</span>
            <span data-label="结果摘要" class="task-summary">
              <template v-if="run.error"><b class="task-error"><X :size="13" />{{ run.error }}</b></template>
              <template v-else><i v-for="pair in summaryPairs(run.summary)" :key="pair">{{ pair }}</i></template>
            </span>
          </div>
        </div>
      </section>
    </template>
  </section>
</template>

<style scoped>
.task-page{display:grid;gap:18px}
.task-toast{position:fixed;right:22px;top:22px;z-index:60;padding:12px 16px;border-radius:8px;background:#123d33;color:#eaf7f2;font-size:13px;box-shadow:0 12px 28px #0d2c2433}
.task-catalog{display:grid;gap:10px}
.task-catalog article{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:14px 16px;border:1px solid #e2ebe7;border-radius:8px;background:#fbfdfc}
.task-catalog h4{margin:0 0 6px;font-size:14px;color:#1f403a}
.task-catalog p{margin:0;display:flex;align-items:center;gap:6px;font-size:12px;color:#6b8078}
.task-last{margin-top:6px!important;gap:8px!important}
.task-last em{font-style:normal;padding:1px 7px;border-radius:999px;font-size:11px;background:#eef4f1;color:#5a726a}
.task-last em[data-tone="good"]{background:#e4f5ec;color:#176b58}
.task-last em[data-tone="bad"]{background:#fdecec;color:#b42318}
.task-filter{height:34px;padding:0 10px;border:1px solid #d8e3dd;border-radius:6px;background:#fff;font-size:12px;color:#405f52}
.task-table{display:grid;gap:2px;overflow-x:auto}
.task-table-head,.task-table-row{display:grid;grid-template-columns:150px 190px 78px 74px 90px minmax(220px,1fr);gap:10px;align-items:center;padding:10px 6px;min-width:880px}
.task-table-head{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#8b9d96;border-bottom:1px solid #e2ebe7}
.task-table-row{border-bottom:1px solid #eff5f2;font-size:12.5px;color:#3d564d}
.task-table-row b{font-weight:650;color:#213f37}
.task-status{font-style:normal;padding:2px 9px;border-radius:999px;font-size:11px;background:#eef4f1;color:#5a726a}
.task-status[data-tone="good"]{background:#e4f5ec;color:#176b58}
.task-status[data-tone="bad"]{background:#fdecec;color:#b42318}
.task-summary{display:flex;flex-wrap:wrap;gap:6px}
.task-summary i{font-style:normal;padding:1px 7px;border-radius:5px;background:#f2f7f4;color:#4c655c;font-size:11px}
.task-error{display:inline-flex;align-items:center;gap:5px;color:#b42318;font-size:11.5px;word-break:break-all}
.task-state{display:grid;place-items:center;gap:6px;padding:34px 16px;text-align:center;color:#6b8078}
.task-state h3{margin:0;font-size:14px;color:#2c4a40}
.task-state p{margin:0;font-size:12.5px}
.metric-grid article strong.time{font-size:16px}
@media(max-width:760px){.task-catalog article{flex-direction:column;align-items:flex-start}.task-table-head{display:none}.task-table-row{grid-template-columns:1fr;gap:4px;padding:12px 4px}.task-table-row>span::before{content:attr(data-label);display:inline-block;min-width:74px;color:#8b9d96;font-size:11px}}
</style>
