<script setup>
import { ArrowRight, Clock3, Search } from 'lucide-vue-next'
import { useRouter } from 'vue-router'
import PageIntro from './PageIntro.vue'
import Footer from './Footer.vue'
defineProps({ title: String, desc: String, items: Array, label: String }); const router = useRouter()
</script>
<template><div><PageIntro :title="title" :desc="desc" /><section class="section light content-list"><div class="content-toolbar"><span>{{ items.length }} 篇内容</span><label class="search-box"><Search :size="16" /><input placeholder="搜索文章" /></label></div><article v-for="item in items" :key="item.slug" class="content-row" @click="router.push('/' + (label === '教程' ? 'help/' : 'guide/') + item.slug)"><div><span class="tag">{{ item.category || item.tag }}</span><h2>{{ item.title }}</h2><p>{{ item.desc || '围绕跨境业务网络选择、交付和运营的实用内容。' }}</p></div><div class="row-meta"><span><Clock3 :size="14" />{{ item.time || item.date }}</span><ArrowRight :size="18" /></div></article></section><Footer /></div></template>
