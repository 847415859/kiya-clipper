<template>
  <div :style="css">{{ node.text }}<FxView v-for="(c, i) in node.children || []" :key="i" :node="c" /></div>
</template>

<script setup>
// 把 renderer/src/fakeui.js 的样式树画成 DOM（和成片里的 FxNode.jsx 对应）。
// React 会给数字自动补 px，Vue 不会，所以这里手动补。
import { computed } from 'vue'

const props = defineProps({ node: { type: Object, required: true } })
const UNITLESS = new Set(['fontWeight', 'lineHeight', 'opacity', 'flex', 'zIndex', 'flexGrow', 'flexShrink'])

const css = computed(() => {
  const out = {}
  for (const [k, v] of Object.entries(props.node.style || {})) {
    out[k] = typeof v === 'number' && !UNITLESS.has(k) ? `${v}px` : v
  }
  return out
})
</script>
