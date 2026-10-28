<script setup>
import { ArrowUpRight } from 'lucide-vue-next'
import { useRouter } from 'vue-router'
import BrandLogo from './BrandLogo.vue'
const router = useRouter()
</script>
<template><footer class="footer"><div class="footer-main"><div><button class="brand footer-brand" aria-label="返回首页" @click="router.push('/')"><BrandLogo /></button><p>全球网络线路，连接你的跨境业务。</p></div><div class="footer-cols"><div><b>导航</b><button @click="router.push('/products')">产品服务</button><button @click="router.push('/regions')">覆盖地区</button><button @click="router.push('/help')">用户教程</button></div><div><b>联系</b><button @click="router.push('/contact')">联系销售</button><button @click="router.push('/contact')">企业微信</button><button @click="router.push('/contact')">Telegram <ArrowUpRight :size="12" /></button></div></div></div><div class="footer-bottom"><span>© 2026 玖玖IP 99IP</span><span>产品与订单数据由业务系统提供</span></div></footer></template>
