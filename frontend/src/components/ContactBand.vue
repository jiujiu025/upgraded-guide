<script setup>
import { ArrowUpRight, Mail, MessageCircle, PhoneCall } from 'lucide-vue-next'
import { useRouter } from 'vue-router'
defineProps({ full: Boolean }); const router = useRouter()
</script>
<template><section class="contact-band" :class="{ full }"><div><p class="eyebrow">TALK TO A ROUTE SPECIALIST</p><h2>准备好连接下一站了吗？</h2><p>告诉我们你的地区、业务场景和使用周期，销售团队会给出适合的线路建议。</p></div><div class="contact-actions"><button @click="router.push('/contact')"><MessageCircle :size="17" />在线咨询 <ArrowUpRight :size="14" /></button><button @click="router.push('/contact')"><PhoneCall :size="17" />联系销售 <ArrowUpRight :size="14" /></button><small><Mail :size="14" /> service@99ip.cn</small></div></section></template>
