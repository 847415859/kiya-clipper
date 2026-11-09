<template>
  <div>
    <div class="toolbar">
      <el-tag :type="(STATUS[status] || {}).type" size="small">{{ (STATUS[status] || {}).text || status }}</el-tag>
      <span class="muted">任务 #{{ jobId }}</span>
      <span class="spacer" />
      <el-button v-if="status === 'RUNNING' || status === 'PENDING'" size="small" type="danger" plain @click="cancel">取消任务</el-button>
    </div>
    <pre ref="box" class="log">{{ text || (status === 'PENDING' ? '排队中，前面的任务完成后开始…' : '（暂无输出）') }}</pre>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import { api, STATUS } from '../api'

const props = defineProps({ jobId: { type: Number, required: true } })
const emit = defineEmits(['done'])
const text = ref('')
const status = ref('PENDING')
const box = ref(null)
let offset = 0
let timer = null
let stopped = false

async function poll() {
  if (stopped) return
  try {
    const r = await api.jobLog(props.jobId, offset)
    status.value = r.status
    if (r.text) {
      const atBottom = box.value && box.value.scrollHeight - box.value.scrollTop - box.value.clientHeight < 40
      text.value += r.text
      offset = r.offset
      await nextTick()
      if (box.value && atBottom !== false) box.value.scrollTop = box.value.scrollHeight
    }
    if (r.done) {
      emit('done', r.status)
      return
    }
  } catch (e) {
    // 网络抖动：下一轮再试
  }
  timer = setTimeout(poll, 2000)
}

function reset() {
  clearTimeout(timer)
  text.value = ''
  offset = 0
  status.value = 'PENDING'
  poll()
}

async function cancel() {
  await ElMessageBox.confirm('确定取消这个任务吗？', '取消任务', { type: 'warning' })
  await api.cancelJob(props.jobId)
}

watch(() => props.jobId, reset, { immediate: true })
onBeforeUnmount(() => { stopped = true; clearTimeout(timer) })
</script>
