<script setup>
/**
 * 自绘下拉，替代原生 <select>。
 *
 * 为什么不用原生 select：它的展开层由浏览器绘制，不是 DOM 元素，CSS 管不到。
 * 一旦触发元素在 overflow:auto 容器里（比如表格 .table-scroll），页面一滚动弹层就
 * 重新定位，用户看到的是"选中的窗口一直在动"。价格页那个问题就是它。
 *
 * 这里的选项层是真实 DOM + position:fixed，坐标在打开时按可视区域算，
 * 并在滚动/缩放时重算，彻底不受父级 overflow 影响。
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps({
  modelValue: { type: [String, Number, Boolean], default: '' },
  options: { type: Array, default: () => [] }, // [{ value, label, disabled? }]
  placeholder: { type: String, default: '请选择' },
  disabled: { type: Boolean, default: false },
  compact: { type: Boolean, default: false },
  ariaLabel: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'change'])

const trigger = ref(null)
const panel = ref(null)
const open = ref(false)
const activeIndex = ref(-1)
const position = ref({ top: 0, left: 0, width: 0, maxHeight: 260, openUp: false })

const current = computed(() => props.options.find((item) => item.value === props.modelValue) || null)
const label = computed(() => (current.value ? current.value.label : props.placeholder))

/** 打开时先按触发元素位置估一个坐标，拿到面板真实高度后再校正（见 placePanel）。 */
function anchor() {
  const el = trigger.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  const below = window.innerHeight - rect.bottom
  const above = rect.top
  position.value = {
    top: rect.bottom + 6,
    left: rect.left,
    width: Math.max(rect.width, 140),
    maxHeight: Math.max(120, Math.min(260, below - 12)),
    openUp: below < 180 && above > below,
  }
}

/** 面板渲染出来后按真实高度决定向上还是向下，保证不出视口。 */
function placePanel() {
  const el = panel.value
  if (!el || !trigger.value) return
  const rect = trigger.value.getBoundingClientRect()
  const height = el.offsetHeight
  const below = window.innerHeight - rect.bottom - 12
  const above = rect.top - 12
  const openUp = below < height && above > below
  const maxHeight = Math.max(120, Math.min(260, openUp ? above : below))
  position.value = {
    top: openUp ? Math.max(8, rect.top - Math.min(height, maxHeight) - 6) : rect.bottom + 6,
    left: rect.left,
    width: Math.max(rect.width, 140),
    maxHeight,
    openUp,
  }
}

function openPanel() {
  if (props.disabled || !props.options.length) return
  anchor()
  open.value = true
  const index = props.options.findIndex((item) => item.value === props.modelValue)
  activeIndex.value = index >= 0 ? index : props.options.findIndex((item) => !item.disabled)
  nextTick(placePanel)
}

function closePanel() {
  open.value = false
  activeIndex.value = -1
}

function toggle() {
  if (open.value) closePanel()
  else openPanel()
}

function pick(option) {
  if (!option || option.disabled) return
  if (option.value !== props.modelValue) {
    emit('update:modelValue', option.value)
    emit('change', option.value)
  }
  closePanel()
  nextTick(() => trigger.value?.focus())
}

function move(step) {
  if (!props.options.length) return
  let next = activeIndex.value
  for (let i = 0; i < props.options.length; i += 1) {
    next = (next + step + props.options.length) % props.options.length
    if (!props.options[next].disabled) break
  }
  activeIndex.value = next
}

function onKeydown(event) {
  if (props.disabled) return
  if (event.key === 'Enter' || event.key === ' ' || event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    if (!open.value) {
      openPanel()
      return
    }
    if (event.key === 'ArrowDown') move(1)
    else if (event.key === 'ArrowUp') move(-1)
    else pick(props.options[activeIndex.value])
    return
  }
  if (event.key === 'Escape' && open.value) {
    event.preventDefault()
    closePanel()
  }
  if (event.key === 'Tab' && open.value) closePanel()
}

/** 滚动与缩放都要重算：父级滚动容器里的任何滚动都算。 */
function onViewportChange() {
  if (!open.value) return
  placePanel()
}

function onDocumentPointer(event) {
  if (!open.value) return
  if (trigger.value?.contains(event.target) || panel.value?.contains(event.target)) return
  closePanel()
}

watch(open, (value) => {
  if (typeof window === 'undefined') return
  if (value) {
    window.addEventListener('scroll', onViewportChange, true)
    window.addEventListener('resize', onViewportChange)
    document.addEventListener('pointerdown', onDocumentPointer, true)
  } else {
    window.removeEventListener('scroll', onViewportChange, true)
    window.removeEventListener('resize', onViewportChange)
    document.removeEventListener('pointerdown', onDocumentPointer, true)
  }
})

watch(() => props.disabled, (value) => { if (value) closePanel() })

onBeforeUnmount(() => {
  window.removeEventListener('scroll', onViewportChange, true)
  window.removeEventListener('resize', onViewportChange)
  document.removeEventListener('pointerdown', onDocumentPointer, true)
})
</script>

<template>
  <div class="app-select" :class="{ open, disabled, compact }">
    <button
      ref="trigger"
      type="button"
      class="app-select-trigger"
      :disabled="disabled"
      :aria-label="ariaLabel || placeholder"
      aria-haspopup="listbox"
      :aria-expanded="open"
      @click="toggle"
      @keydown="onKeydown"
    >
      <span class="app-select-label" :class="{ placeholder: !current }">{{ label }}</span>
      <svg class="app-select-caret" viewBox="0 0 12 8" aria-hidden="true">
        <path d="M1 1.5 6 6.5l5-5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
      </svg>
    </button>

    <Teleport to="body">
      <div v-if="open" class="app-select-backdrop" @pointerdown.prevent></div>
      <div
        v-if="open"
        ref="panel"
        class="app-select-panel"
        :class="{ 'open-up': position.openUp, compact }"
        role="listbox"
        :style="{
          top: `${position.top}px`,
          left: `${position.left}px`,
          minWidth: `${position.width}px`,
          maxHeight: `${position.maxHeight}px`,
        }"
      >
        <button
          v-for="(option, index) in options"
          :key="String(option.value)"
          type="button"
          class="app-select-option"
          role="option"
          :class="{ active: index === activeIndex, selected: option.value === modelValue, muted: option.disabled }"
          :aria-selected="option.value === modelValue"
          :disabled="option.disabled"
          @click="pick(option)"
          @mouseenter="activeIndex = index"
        >
          <span>{{ option.label }}</span>
          <svg v-if="option.value === modelValue" viewBox="0 0 12 10" aria-hidden="true">
            <path d="M1 5.2 4.2 8.4 11 1.6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </button>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.app-select { position: relative; min-width: 0; }
.app-select-trigger {
  width: 100%; min-height: 38px; display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 0 10px; border: 1px solid #cfdad4; border-radius: 5px; background: #fff;
  color: #334a40; font: inherit; font-size: 12px; text-align: left; cursor: pointer;
}
.app-select.compact .app-select-trigger { min-height: 32px; padding: 0 7px; font-size: 10px; border-radius: 4px; }
.app-select-trigger:hover { border-color: #a8c7b8; }
.app-select.open .app-select-trigger { border-color: #2c8167; box-shadow: 0 0 0 2px rgba(44, 129, 103, .16); }
.app-select.disabled .app-select-trigger { opacity: .5; cursor: not-allowed; }
.app-select-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.app-select-label.placeholder { color: #93a49c; }
.app-select-caret { flex: none; width: 10px; height: 7px; color: #7b8c84; transition: transform .16s ease; }
.app-select.open .app-select-caret { transform: rotate(180deg); }
</style>

<style>
/* 面板挂在 body 上（Teleport），所以不能用 scoped。 */
.app-select-backdrop { position: fixed; inset: 0; z-index: 70; }
.app-select-panel {
  position: fixed; z-index: 71; overflow-y: auto; padding: 4px;
  border: 1px solid #cfdad4; border-radius: 6px; background: #fff;
  box-shadow: 0 14px 34px rgba(16, 33, 36, .16);
  font-size: 12px; color: #334a40;
}
.app-select-panel.compact { font-size: 10px; padding: 3px; }
.app-select-option {
  width: 100%; display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 8px 9px; border-radius: 4px; background: transparent; color: inherit;
  font: inherit; text-align: left; cursor: pointer; white-space: nowrap;
}
.app-select-panel.compact .app-select-option { padding: 6px 7px; }
.app-select-option.active { background: #eef5f1; }
.app-select-option.selected { color: #1f6b53; font-weight: 700; }
.app-select-option.muted { opacity: .45; cursor: not-allowed; }
.app-select-option svg { flex: none; width: 11px; height: 9px; }
@media (prefers-reduced-motion: reduce) { .app-select-caret { transition: none; } }
</style>
