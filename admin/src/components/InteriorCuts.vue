<template>
  <el-card shadow="never" class="cuts-card">
    <template #header><b>片中删除</b></template>
    <div class="cut-inputs">
      <span>删除</span><el-input-number v-model="start" :min="0" :max="clipLen" :step="0.1" :precision="2" aria-label="片中删除起点" />
      <el-button size="small" @click="start = time">取当前起点</el-button>
      <span>至</span><el-input-number v-model="end" :min="0" :max="clipLen" :step="0.1" :precision="2" aria-label="片中删除终点" />
      <el-button size="small" @click="end = time">取当前终点</el-button>
      <el-button size="small" type="danger" plain @click="add">删除这段</el-button>
    </div>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <div v-for="(q, i) in edit.cuts" :key="`${q.start}:${q.end}`" class="cut-row">
      <span>{{ q.start.toFixed(2) }}–{{ q.end.toFixed(2) }} 秒</span>
      <el-button size="small" link @click="$emit('seek', q.start)">定位接缝</el-button>
      <el-button size="small" link @click="edit.cuts.splice(i, 1)">保留这段</el-button>
    </div>
    <el-checkbox v-model="edit.remove_business">自动删除纯欢迎、谢礼等营业话</el-checkbox>
    <p class="muted">时间按素材计。播放会跳过删除段；跨接缝的校对字幕请复查。前情呼应请用局部试渲染核对。</p>
  </el-card>
</template>

<script setup>
import {ref} from 'vue'
import {mergeCuts} from '../editor-tools'
const props = defineProps({edit: Object, time: Number, clipLen: Number})
defineEmits(['seek'])
const start = ref(0), end = ref(1), error = ref('')
function add() {
  error.value = ''
  if (!Number.isFinite(start.value) || !Number.isFinite(end.value) || end.value <= start.value) { error.value = '终点须大于起点'; return }
  if (props.edit.cuts.length >= 100) { error.value = '最多删除 100 段'; return }
  const cuts = mergeCuts([...props.edit.cuts, {start: start.value, end: end.value}], 0, props.clipLen)
  const trim = props.edit.trim
  const removed = cuts.reduce((n, q) => n + Math.max(0, Math.min(trim.end, q.end) - Math.max(trim.start, q.start)), 0)
  if (trim.end - trim.start - removed < 1) { error.value = '删除后至少保留 1 秒'; return }
  props.edit.cuts = cuts
}
</script>
<style scoped>
.cuts-card { margin: 12px 0; }
.cut-inputs, .cut-row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin: 8px 0; }
.cut-inputs :deep(.el-input-number) { width: 120px; }
.muted { color: #667085; font-size: 12px; }.error { color: #c43; }
</style>
