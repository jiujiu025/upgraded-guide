<script setup>
// 教程 / 资源列表。搜索框真正生效（之前输入没有任何反馈）。
import { computed, ref } from 'vue'
import { ArrowRight, Clock3, Search } from 'lucide-vue-next'
import { useRouter } from 'vue-router'
import PageIntro from './PageIntro.vue'
import Footer from './Footer.vue'

const props = defineProps({ title: String, desc: String, items: { type: Array, default: () => [] }, label: String })
const router = useRouter()
const keyword = ref('')

const filtered = computed(() => {
  const query = keyword.value.trim().toLowerCase()
  if (!query) return props.items
  return props.items.filter((item) => [item.title, item.desc, item.category, item.tag]
    .filter(Boolean)
    .some((value) => String(value).toLowerCase().includes(query)))
})

const basePath = computed(() => (props.label === '教程' ? '/help/' : '/guide/'))
</script>

<template>
  <div>
    <PageIntro :title="title" :desc="desc" />
    <section class="section light content-list">
      <div class="content-toolbar">
        <span>{{ filtered.length }} / {{ items.length }} 篇内容</span>
        <label class="search-box">
          <Search :size="16" />
          <input v-model="keyword" placeholder="搜索标题或关键词" />
        </label>
      </div>
      <div v-if="!filtered.length" class="content-empty">没有匹配的内容，换个关键词试试。</div>
      <article v-for="item in filtered" :key="item.slug" class="content-row" @click="router.push(basePath + item.slug)">
        <div>
          <span class="tag">{{ item.category || item.tag }}</span>
          <h2>{{ item.title }}</h2>
          <p>{{ item.desc || item.intro || '围绕跨境业务网络选择、交付和运营的实用内容。' }}</p>
        </div>
        <div class="row-meta">
          <span><Clock3 :size="14" />{{ item.time || item.date }}</span>
          <ArrowRight :size="18" />
        </div>
      </article>
    </section>
    <Footer />
  </div>
</template>

<style scoped>
.content-empty{padding:40px 0;color:#7c8d86;font-size:13px;text-align:center}
</style>
