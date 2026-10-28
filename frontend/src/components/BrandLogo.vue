<script setup>
defineProps({
  full: { type: Boolean, default: false },
  eager: { type: Boolean, default: false },
})
</script>

<template>
  <img
    class="brand-logo"
    :class="{ 'brand-logo-full': full }"
    :src="full ? '/brand/99ip-logo-full.png' : '/brand/99ip-logo.png'"
    alt="99IP 玖玖IP"
    :loading="eager ? 'eager' : 'lazy'"
    decoding="async"
  />
</template>
