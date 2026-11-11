<script setup>
// 注册图形验证码：九宫格点选。
// 题目由后端出（GET /public/captcha），答案只存在加密 token 里；这里只负责
// 让客户点选并原样把 token + 选中的格子编号交回去，不做任何本地判定。
import { onMounted, ref } from 'vue'
import { Check, RefreshCw } from 'lucide-vue-next'
import { getCaptcha } from '../api'

const challenge = ref(null)
const selected = ref([])
const loading = ref(false)
const failed = ref('')

// SVG 走 data URI 塞进 <img>：比 v-html 更安全（img 里的 SVG 不能执行脚本），
// 也省得给每格画一个容器。
const toDataUri = (svg) => `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`

async function refresh() {
  loading.value = true
  failed.value = ''
  selected.value = []
  try {
    const { data } = await getCaptcha()
    challenge.value = data
  } catch (error) {
    challenge.value = null
    failed.value = error.userMessage || '验证码加载失败，点右上角刷新重试'
  } finally {
    loading.value = false
  }
}

function toggle(index) {
  if (loading.value || !challenge.value) return
  selected.value = selected.value.includes(index)
    ? selected.value.filter((item) => item !== index)
    : [...selected.value, index]
}

// 父组件提交时取这个：没出题或一张没选都返回 null，由调用方提示。
function answer() {
  if (!challenge.value || !selected.value.length) return null
  return { token: challenge.value.token, selection: [...selected.value].sort((a, b) => a - b) }
}

onMounted(refresh)
defineExpose({ answer, refresh })
</script>

<template>
  <div class="captcha" :class="{ 'captcha-busy': loading }">
    <div class="captcha-head">
      <p class="captcha-title">
        安全验证
        <span v-if="challenge">请点选图中所有「{{ challenge.target }}」（共 {{ challenge.count }} 张）</span>
        <span v-else-if="loading">正在生成题目…</span>
      </p>
      <button class="captcha-refresh" type="button" :disabled="loading" aria-label="换一题" @click="refresh">
        <RefreshCw :size="15" />
      </button>
    </div>
    <div v-if="challenge" class="captcha-grid" role="group" :aria-label="`请点选图中所有${challenge.target}`">
      <button
        v-for="tile in challenge.tiles"
        :key="`${challenge.token}-${tile.index}`"
        class="captcha-tile"
        :class="{ picked: selected.includes(tile.index) }"
        type="button"
        :aria-pressed="selected.includes(tile.index)"
        :aria-label="`第 ${tile.index + 1} 张图`"
        @click="toggle(tile.index)"
      >
        <img :src="toDataUri(tile.svg)" alt="" draggable="false" />
        <span v-if="selected.includes(tile.index)" class="captcha-check"><Check :size="13" /></span>
      </button>
    </div>
    <p v-if="failed" class="captcha-error" role="alert">{{ failed }}</p>
    <p class="captcha-hint">
      <template v-if="selected.length">已选 {{ selected.length }} 张，再点一次可取消。</template>
      <template v-else>看不清就点右上角换一题，验证码 3 分钟内有效。</template>
    </p>
  </div>
</template>

<style scoped>
.captcha {
  display: grid;
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: #fbfdfc;
}
.captcha-busy { opacity: .72; }
.captcha-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.captcha-title { margin: 0; font-size: 13px; font-weight: 700; color: var(--ink); }
.captcha-title span { margin-left: 6px; font-weight: 500; color: var(--muted); }
.captcha-refresh { display: grid; place-items: center; width: 28px; height: 28px; border-radius: 6px; color: var(--muted); }
.captcha-refresh:hover { background: var(--paper); color: var(--ink); }
.captcha-refresh:disabled { cursor: wait; opacity: .5; }
.captcha-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.captcha-tile {
  position: relative;
  display: block;
  padding: 0;
  border: 2px solid transparent;
  border-radius: 10px;
  overflow: hidden;
  background: var(--paper);
  transition: border-color .15s, transform .15s;
}
.captcha-tile img { display: block; width: 100%; height: auto; }
.captcha-tile:hover { transform: translateY(-1px); border-color: var(--line); }
.captcha-tile.picked { border-color: #2f7a6d; }
.captcha-check {
  position: absolute;
  right: 4px;
  bottom: 4px;
  display: grid;
  place-items: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #2f7a6d;
  color: #fff;
}
.captcha-error { margin: 0; font-size: 12px; color: #b4402f; }
.captcha-hint { margin: 0; font-size: 12px; color: var(--muted); }
</style>
