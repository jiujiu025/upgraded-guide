<script setup>
// 购买页的大洲筛选条：国家一多（30+），平铺成一片根本找不到。
// 只负责展示与选中，过滤交给页面——两处购买入口的列表样式不一样，逻辑不要耦合在组件里。
import { computed } from 'vue'

const props = defineProps({
  groups: { type: Array, default: () => [] },
  modelValue: { type: String, default: 'all' },
  total: { type: Number, default: 0 },
})
const emit = defineEmits(['update:modelValue'])

const items = computed(() => [
  { key: 'all', label: '全部', count: props.total },
  ...props.groups,
])

function pick(key) {
  if (key !== props.modelValue) emit('update:modelValue', key)
}
</script>

<template>
  <div class="continent-tabs" role="tablist" aria-label="按大洲筛选国家">
    <button
      v-for="item in items"
      :key="item.key"
      type="button"
      role="tab"
      :aria-selected="modelValue === item.key"
      :class="{ active: modelValue === item.key }"
      @click="pick(item.key)"
    >
      {{ item.label }}<em>{{ item.count }}</em>
    </button>
  </div>
</template>

<style scoped>
.continent-tabs{
  display:flex;align-items:center;gap:28px;
  margin:14px 0 6px;
  border-bottom:1px solid #ece2d9;
  overflow-x:auto;scrollbar-width:none;
}
.continent-tabs::-webkit-scrollbar{display:none}
.continent-tabs button{
  flex:none;display:inline-flex;align-items:center;gap:6px;
  padding:4px 0 10px;margin-bottom:-1px;
  border-bottom:2px solid transparent;
  color:#4a5b52;font-size:13px;font-weight:600;
  background:none;white-space:nowrap;transition:color .16s ease,border-color .16s ease;
}
.continent-tabs button:hover{color:#c9682f}
.continent-tabs button.active{color:#df7a34;border-bottom-color:#df7a34}
.continent-tabs em{
  display:inline-flex;align-items:center;justify-content:center;
  min-width:20px;height:18px;padding:0 6px;
  border-radius:99px;background:#e5893f;color:#fff;
  font-size:11px;font-style:normal;font-weight:600;line-height:1;
}
@media(max-width:640px){
  .continent-tabs{gap:18px}
  .continent-tabs button{font-size:12px}
}
@media(prefers-reduced-motion:reduce){
  .continent-tabs button{transition:none}
}
</style>
