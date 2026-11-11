<script setup>
/**
 * 城市与 IP 段选择器（产品级的第二层选择）。
 *
 * 交互按业务的真实说法来组织：先按「美国 — 华盛顿 / 美国 — 纽约」这种城市卡片选一个，
 * 选中后再在号段里筛选（输入 23.94 只看这个前缀的段）。
 *
 * 不选城市 = 自动分配，后端不会拒绝；选了就按所选交付，所以服务端会二次校验
 * （见 docs/城市与IP段选择设计.md）。后台没配城市时整个组件不渲染。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { Loader2, MapPin, Search } from 'lucide-vue-next'
import { getCatalogCities } from '../api'

const props = defineProps({
  productId: { type: Number, default: null },
  modelValue: { type: Object, default: () => ({ cityCode: '', ipSegment: '' }) },
  disabled: { type: Boolean, default: false },
  // 传了标题就自己带卡片样式，方便直接放进下单页的一个板块里
  heading: { type: String, default: '' },
  // 外层已有板块标题时用：不再重复渲染内部小标题
  plain: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'ready'])

const loading = ref(false)
const options = ref({ supported: false, items: [], auto_label: '自动分配（不指定城市）', country_name: '' })
const segmentQuery = ref('')
let requestSeq = 0

const cities = computed(() => options.value.items || [])
const selected = computed(() => cities.value.find((item) => item.city_code === props.modelValue.cityCode) || null)
const segments = computed(() => selected.value?.segments || [])
const filteredSegments = computed(() => {
  const keyword = segmentQuery.value.trim().toUpperCase()
  if (!keyword) return segments.value
  return segments.value.filter((segment) => segment.toUpperCase().includes(keyword))
})
// 筛选把已选段过滤掉了也要显示出来，否则用户会以为选择丢了
const pinnedSegment = computed(() => {
  const chosen = props.modelValue.ipSegment
  return chosen && !filteredSegments.value.includes(chosen) ? chosen : ''
})
const cityPrefix = computed(() => options.value.country_name || '')

function update(patch) {
  emit('update:modelValue', { cityCode: '', ipSegment: '', ...props.modelValue, ...patch })
}

function pickCity(code) {
  // 换城市必须清掉号段：旧号段不属于新城市，留着会被后端拒单。
  if (code !== props.modelValue.cityCode) segmentQuery.value = ''
  update({ cityCode: code || '', ipSegment: '' })
}

function pickSegment(segment) {
  update({ ipSegment: segment || '' })
}

async function load() {
  const productId = props.productId
  if (!productId) {
    options.value = { supported: false, items: [], auto_label: '自动分配（不指定城市）', country_name: '' }
    emit('ready', { supported: false, items: [] })
    return
  }
  const seq = ++requestSeq
  loading.value = true
  try {
    const { data } = await getCatalogCities(productId)
    if (seq !== requestSeq) return
    options.value = data
    emit('ready', data)
    // 商品换了以后旧选择可能已不存在：清空，避免提交一个不可售的城市。
    const stillValid = (data.items || []).some((item) => item.city_code === props.modelValue.cityCode)
    if (!stillValid && (props.modelValue.cityCode || props.modelValue.ipSegment)) {
      emit('update:modelValue', { cityCode: '', ipSegment: '' })
    }
  } catch {
    if (seq === requestSeq) {
      options.value = { supported: false, items: [], auto_label: '自动分配（不指定城市）', country_name: '' }
      emit('ready', { supported: false, items: [] })
    }
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

watch(() => props.productId, load)
onMounted(load)
</script>

<template>
  <div v-if="loading" class="city-picker city-loading">
    <Loader2 :size="14" class="spin" /> 正在读取可售城市…
  </div>
  <section v-else-if="options.supported" class="city-picker" :class="{ 'city-picker-block': heading }">
    <h3 v-if="heading" class="city-heading">{{ heading }}</h3>
    <p v-if="heading" class="city-hint">选城市再选号段；不选则由系统按库存自动分配。</p>
    <div v-else-if="!plain" class="city-head">
      <span class="city-title"><MapPin :size="13" /> 指定城市 / IP 段（可选）</span>
      <small>不选则由系统按库存自动分配</small>
    </div>
    <p v-else class="city-hint">不选则由系统按库存自动分配；选了就按所选城市 / 号段交付。</p>

    <div class="city-cards" role="listbox" aria-label="选择城市">
      <button
        type="button"
        class="city-card auto"
        :class="{ selected: !modelValue.cityCode }"
        :disabled="disabled"
        @click="pickCity('')"
      >
        <b>自动分配</b>
        <span class="city-card-code">不限城市</span>
        <small>按库存最优城市交付</small>
      </button>
      <button
        v-for="item in cities"
        :key="item.city_code"
        type="button"
        class="city-card"
        :class="{ selected: modelValue.cityCode === item.city_code }"
        :disabled="disabled"
        @click="pickCity(item.city_code)"
      >
        <b>{{ cityPrefix ? `${cityPrefix} — ${item.city_name}` : item.city_name }}</b>
        <span class="city-card-code">{{ item.city_code }}</span>
        <small>{{ (item.segments || []).length ? `可指定 ${item.segments.length} 个号段` : '不限号段' }}</small>
      </button>
    </div>

    <div v-if="selected" class="segment-panel">
      <div v-if="segments.length" class="segment-toolbar">
        <label class="segment-search">
          <Search :size="13" />
          <input v-model="segmentQuery" type="search" placeholder="筛选号段，如 23.94" maxlength="24" :disabled="disabled" />
        </label>
        <span class="segment-count">显示 {{ filteredSegments.length }} / {{ segments.length }}</span>
      </div>
      <div v-if="segments.length" class="segment-list">
        <span class="segment-label">IP 段</span>
        <button type="button" :class="{ on: !modelValue.ipSegment }" :disabled="disabled" @click="pickSegment('')">不限</button>
        <button
          v-if="pinnedSegment"
          type="button"
          class="pinned"
          :class="{ on: true }"
          :disabled="disabled"
          @click="pickSegment(pinnedSegment)"
        >{{ pinnedSegment }} · 已选</button>
        <button
          v-for="segment in filteredSegments"
          :key="segment"
          type="button"
          :class="{ on: modelValue.ipSegment === segment }"
          :disabled="disabled"
          @click="pickSegment(segment)"
        >{{ segment }}</button>
        <span v-if="!filteredSegments.length && !pinnedSegment" class="segment-empty">没有匹配的号段，换个前缀试试</span>
      </div>
      <p v-else class="city-hint">该城市暂未开放指定号段，交付时自动分配。</p>
    </div>
  </section>
</template>

<style scoped>
.city-picker{display:grid;gap:9px;padding:12px;border:1px solid #dbe4e1;border-radius:6px;background:#fff}
.city-picker-block{padding:16px 18px;background:#f9fbfa;border-color:#dbe3e0}
.city-heading{margin:0 0 2px;font-size:14px;color:#2c4338}
.city-loading{display:flex;align-items:center;gap:7px;color:#7a8b83;font-size:11px}
.city-head{display:flex;align-items:baseline;justify-content:space-between;gap:10px;flex-wrap:wrap}
.city-title{display:inline-flex;align-items:center;gap:5px;color:#2c4338;font-size:11px;font-weight:650}
.city-head small{color:#829188;font-size:10px}
.city-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.city-card{display:grid;gap:3px;padding:10px 12px;border:1px solid #dbe4e1;border-radius:6px;background:#fff;text-align:left;cursor:pointer}
.city-card b{font-size:12px;color:#1d322b;line-height:1.35}
.city-card-code{color:#6d8177;font-size:10px;font-variant-numeric:tabular-nums;letter-spacing:.02em}
.city-card small{color:#829188;font-size:10px}
.city-card.selected{border-color:#2c9b88;background:#e7f6f1;box-shadow:inset 0 0 0 1px #2c9b88}
.city-card.selected small,.city-card.selected .city-card-code{color:#2c6f60}
.city-card:disabled{opacity:.55;cursor:not-allowed}
.city-card.auto b{color:#3c6a5c}
.segment-panel{display:grid;gap:7px;padding-top:2px}
.segment-toolbar{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.segment-search{display:inline-flex;align-items:center;gap:6px;min-height:30px;padding:0 9px;border:1px solid #d6e1db;border-radius:5px;background:#fff;color:#7b8c84;min-width:200px}
.segment-search input{width:100%;border:0;outline:0;font:inherit;font-size:11px;color:#20332d;background:transparent}
.segment-search:focus-within{border-color:#449377;box-shadow:0 0 0 3px #44937718}
.segment-count{color:#8a9a92;font-size:10px;font-variant-numeric:tabular-nums}
.segment-list{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.segment-label{color:#5e7771;font-size:10px}
.segment-list button{min-height:26px;padding:0 9px;border:1px solid #d6e1db;border-radius:4px;background:#fff;color:#3c6a5c;font-size:10px;font-variant-numeric:tabular-nums;cursor:pointer}
.segment-list button.on{border-color:#2c9b88;background:#e7f6f1;color:#1f6b53;font-weight:700}
.segment-list button.pinned{border-style:dashed}
.segment-list button:disabled{opacity:.55;cursor:not-allowed}
.segment-empty{color:#8a9a92;font-size:10px}
.city-hint{margin:0;color:#8a9a92;font-size:10px}
.spin{animation:city-spin .8s linear infinite}
@keyframes city-spin{to{transform:rotate(360deg)}}
@media(prefers-reduced-motion:reduce){.spin{animation:none}}
</style>
