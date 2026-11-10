<script setup>
/**
 * 可双击编辑的价格单元格。
 *
 * 只读态是纯文本，双击进入编辑；点 ✓ 或按 Enter 保存，Esc 或点 ✕ 取消。
 * 这样改一个价只需"双击 → 输入 → Enter"三步，不用再去表格里找保存按钮。
 *
 * 两个刻意的选择：
 * 1. 失焦 = 取消，不是保存。价格会直接给客户看，误触不该生效。
 * 2. ✓ 按钮用 mousedown.prevent 保持焦点，否则点它的瞬间先触发失焦取消，
 *    click 就落空了（这是行内编辑最常见的 bug）。
 */
import { computed, nextTick, ref, watch } from 'vue'

const props = defineProps({
  value: { type: [Number, String], default: 0 },
  locked: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  min: { type: Number, default: 0.01 },
  max: { type: Number, default: 99999999 },
  ariaLabel: { type: String, default: '价格' },
  lockHint: { type: String, default: '该价格由价目表联动，不能单独修改' },
})
const emit = defineEmits(['save'])

const editing = ref(false)
const draft = ref('')
const error = ref('')
const inputRef = ref(null)

/** 整数不补零（¥79），带小数保留两位（¥139.30）——避免整张表全是 .00。 */
const display = computed(() => {
  const number = Number(props.value)
  if (!Number.isFinite(number)) return '—'
  return Number.isInteger(number) ? `¥${number}` : `¥${number.toFixed(2)}`
})

function startEdit() {
  if (props.locked || props.disabled || props.busy) return
  draft.value = String(Number(props.value))
  error.value = ''
  editing.value = true
  nextTick(() => {
    inputRef.value?.focus()
    inputRef.value?.select()
  })
}

function cancel() {
  editing.value = false
  error.value = ''
  draft.value = ''
}

function validate() {
  const raw = String(draft.value).trim()
  if (!raw) return '请输入价格'
  const number = Number(raw)
  if (!Number.isFinite(number)) return '价格必须是数字'
  if (number < props.min) return `价格不能低于 ${props.min}`
  if (number > props.max) return '价格超出上限'
  if (number === Number(props.value)) return ''
  return ''
}

function commit() {
  if (props.busy) return
  const message = validate()
  if (message) {
    error.value = message
    inputRef.value?.focus()
    return
  }
  const number = Number(draft.value)
  if (number === Number(props.value)) {
    cancel()
    return
  }
  emit('save', number)
}

function onKeydown(event) {
  if (event.key === 'Enter') {
    event.preventDefault()
    commit()
  } else if (event.key === 'Escape') {
    event.preventDefault()
    cancel()
  } else if (event.key === 'Tab') {
    cancel()
  }
}

// 外部更新了值（保存成功、重新加载）就退出编辑，避免输入框里留着旧数字。
watch(() => props.value, () => { if (!props.busy) cancel() })
</script>

<template>
  <div class="price-cell" :class="{ editing, locked, busy }">
    <template v-if="!editing">
      <button
        type="button"
        class="price-cell-read"
        :disabled="disabled"
        :title="locked ? lockHint : '双击可修改价格'"
        :aria-label="`${ariaLabel}：${display}${locked ? '（不可修改）' : '，双击编辑'}`"
        @dblclick="startEdit"
      >
        {{ display }}
      </button>
    </template>

    <template v-else>
      <div class="price-cell-edit" :class="{ invalid: error }">
        <span class="price-cell-symbol">¥</span>
        <input
          ref="inputRef"
          v-model="draft"
          type="number"
          :min="min"
          :max="max"
          step="0.01"
          inputmode="decimal"
          :aria-label="ariaLabel"
          @keydown="onKeydown"
          @blur="cancel"
        />
      </div>
      <button
        type="button"
        class="price-cell-confirm"
        :disabled="busy"
        title="确认修改（Enter）"
        aria-label="确认修改价格"
        @mousedown.prevent
        @click="commit"
      >
        <svg viewBox="0 0 12 10" aria-hidden="true"><path d="M1 5.2 4.2 8.4 11 1.6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" /></svg>
      </button>
      <button
        type="button"
        class="price-cell-cancel"
        title="取消（Esc）"
        aria-label="取消修改"
        @mousedown.prevent
        @click="cancel"
      >
        <svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2 2l8 8M10 2l-8 8" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" /></svg>
      </button>
    </template>
    <small v-if="error" class="price-cell-error" role="alert">{{ error }}</small>
  </div>
</template>

<style scoped>
.price-cell { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; min-width: 0; }
.price-cell-read {
  min-height: 30px; padding: 0 6px; border: 1px dashed transparent; border-radius: 4px;
  background: transparent; color: #2f4b3c; font: inherit; font-size: 11px;
  font-variant-numeric: tabular-nums; cursor: text;
}
.price-cell-read:hover { border-color: #b9d2c6; background: #f6faf8; }
.price-cell.locked .price-cell-read { cursor: not-allowed; color: #73867c; }
.price-cell-edit {
  display: flex; align-items: center; gap: 3px; width: 100px; height: 30px; padding: 0 7px;
  border: 1px solid #448d70; border-radius: 4px; background: #fff; color: #73867c;
}
.price-cell-edit.invalid { border-color: #c4695a; box-shadow: 0 0 0 2px rgba(196, 105, 90, .16); }
.price-cell-symbol { font-size: 10px; }
.price-cell-edit input {
  width: 100%; min-width: 0; border: 0; outline: 0; background: transparent;
  color: #2f4b3c; font: inherit; font-size: 11px; font-variant-numeric: tabular-nums;
}
.price-cell-confirm,
.price-cell-cancel {
  width: 26px; height: 26px; display: grid; place-items: center; border-radius: 4px;
  border: 1px solid #cfdbd5; background: #fff; color: #476459; cursor: pointer;
}
.price-cell-confirm { border-color: #26785f; background: #26785f; color: #fff; }
.price-cell-confirm:disabled { opacity: .5; cursor: progress; }
.price-cell-confirm:hover:not(:disabled) { background: #1e694f; }
.price-cell-cancel:hover { background: #f1f7f3; }
.price-cell-confirm svg { width: 10px; height: 9px; }
.price-cell-cancel svg { width: 9px; height: 9px; }
.price-cell-error { flex-basis: 100%; color: #b04d3f; font-size: 9px; line-height: 1.4; }
</style>
