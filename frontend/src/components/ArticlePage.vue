<script setup>
// 文章页：按路由 slug 取真实条目，不再"所有链接同一篇"。
// 内容在 src/content/articles.js；业务数据（产品/价格）不在这里。
import { computed } from 'vue'
import { ArrowLeft, ArrowRight, BookOpen } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import Footer from './Footer.vue'
import { articleNeighbours, findArticle } from '../content/articles'

const route = useRoute()
const router = useRouter()
const article = computed(() => findArticle(String(route.params.slug || '')))
const neighbours = computed(() => articleNeighbours(String(route.params.slug || '')))
const isHelp = computed(() => ['clash', 'v2ray', 'nekobox', 'import'].includes(article.value?.slug))
const backPath = computed(() => (isHelp.value ? '/help' : '/resources'))
</script>

<template>
  <div>
    <section v-if="!article" class="article-hero">
      <p class="eyebrow">GUIDE / NOT FOUND</p>
      <h1>这篇内容还在整理</h1>
      <p>你可以先回到教程或资源中心，看已经发布的内容。</p>
      <button class="button" @click="router.push('/help')">返回教程 <ArrowRight :size="15" /></button>
    </section>
    <template v-else>
      <section class="article-hero">
        <p class="eyebrow">GUIDE / {{ article.category || article.tag }}</p>
        <h1>{{ article.title }}</h1>
        <p>{{ article.desc || article.intro }}</p>
      </section>
      <section class="article-layout">
        <aside>
          <div class="toc-title"><BookOpen :size="15" />本文目录</div>
          <a v-for="section in article.sections" :key="section.id" :href="'#' + section.id">{{ section.heading }}</a>
        </aside>
        <article class="article-body">
          <div class="article-meta">{{ article.category || article.tag }}<template v-if="article.date"> · {{ article.date }}</template><template v-if="article.time"> · {{ article.time }}</template></div>
          <p v-if="article.intro" class="article-intro">{{ article.intro }}</p>
          <template v-for="section in article.sections" :key="section.id">
            <h2 :id="section.id">{{ section.heading }}</h2>
            <p v-for="(paragraph, index) in section.body" :key="index">{{ paragraph }}</p>
            <ul v-if="section.bullets">
              <li v-for="item in section.bullets" :key="item">{{ item }}</li>
            </ul>
          </template>
          <div class="article-nav">
            <button @click="router.push(backPath)"><ArrowLeft :size="15" />返回{{ isHelp ? '教程' : '资源中心' }}</button>
            <button v-if="neighbours.next" @click="router.push('/' + (isHelp ? 'help/' : 'guide/') + neighbours.next.slug)">
              下一篇：{{ neighbours.next.title }} <ArrowRight :size="15" />
            </button>
          </div>
        </article>
      </section>
    </template>
    <Footer />
  </div>
</template>
