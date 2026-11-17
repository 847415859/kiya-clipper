<template>
  <el-card shadow="never" class="recovery-card">
    <template #header><b>草稿与历史版本</b><el-button link @click="refresh">刷新记录</el-button></template>
    <p role="status" class="muted">{{ status }}</p>
    <el-collapse>
      <el-collapse-item :title="`可恢复草稿 ${drafts.length} · 保存版本 ${versions.length}`">
        <div v-for="d in drafts" :key="d.id" class="recovery-row">
          <span>草稿 · {{ date(d.updatedAt) }}{{ d.baseRevision !== revision ? '（基于旧版本）' : '' }}</span>
          <el-button size="small" @click="restore(d.state)">载入草稿</el-button>
        </div>
        <div v-for="v in versions" :key="v.id" class="recovery-row">
          <span>{{ v.label }} · {{ date(v.updatedAt) }}</span>
          <el-button size="small" @click="restore({ edit: v.edit, styleId: v.edit.subtitles?.style_id || null })">载入版本</el-button>
        </div>
        <p class="muted">载入后可撤销，检查后再保存。记录仅在点击载入时应用。</p>
      </el-collapse-item>
    </el-collapse>
  </el-card>
</template>

<script setup>
import {computed, onBeforeUnmount, onMounted, ref, watch} from 'vue'
import {api} from '../api'
const props = defineProps({ name: String, rank: Number, state: Object, revision: String, dirty: Boolean, ready: Boolean })
const emit = defineEmits(['restore'])
const newClient = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('')
let client = newClient()
const drafts = ref([]), versions = ref([]), status = ref('自动草稿准备中')
let seq = 0, timer, request = 0, lastStored = ''
const key = computed(() => `${props.name}#${props.rank}`)
const date = value => new Date(value).toLocaleString()
async function refresh() {
  const current = key.value
  try {
    const r = await api.editRecovery(props.name, props.rank)
    if (key.value !== current) return
    drafts.value = r.drafts || []; versions.value = r.versions || []
  } catch { status.value = '读取恢复记录失败，请重试' }
}
async function persist() {
  clearTimeout(timer)
  if (!props.ready || !props.state) return false
  const current = key.value, generation = ++request
  const data = JSON.parse(JSON.stringify({state: props.state, baseRevision: props.revision, seq: ++seq, committed: !props.dirty}))
  const signature = JSON.stringify([data.state, data.baseRevision, data.committed])
  if (signature === lastStored) { status.value = data.committed ? '当前修改已正式保存' : '草稿已自动保存'; return true }
  status.value = '正在保存自动草稿…'
  try {
    await api.editDraft(props.name, props.rank, client, data)
    if (current === key.value && generation === request) {
      lastStored = signature
      status.value = data.committed ? '当前修改已正式保存' : `草稿已自动保存 · ${date(Date.now())}`
    }
    return true
  } catch {
    if (current === key.value && generation === request) status.value = '自动草稿保存失败，请保留页面并手动保存'
    return false
  }
}
async function restore(state) {
  // Preserve the current draft before loading another state. Failed writes must not discard work.
  if (!(await preserve())) return
  emit('restore', JSON.parse(JSON.stringify(state)))
}
async function preserve() {
  const before = JSON.stringify([key.value, props.state, props.revision, props.dirty])
  if (props.dirty && !(await persist())) return false
  if (before !== JSON.stringify([key.value, props.state, props.revision, props.dirty])) {
    status.value = '保存草稿期间又有新修改，请重试载入以保留最新内容'
    return false
  }
  client = newClient(); seq = 0; lastStored = ''; request++
  return true
}
watch([() => props.state, () => props.dirty, () => props.revision, () => props.ready], () => {
  clearTimeout(timer)
  request++
  if (props.ready) {
    status.value = props.dirty ? '等待自动草稿保存…' : '当前修改已正式保存'
    timer = setTimeout(persist, 1200)
  }
}, {deep: true})
watch(key, () => { clearTimeout(timer); request++; lastStored = ''; drafts.value = []; versions.value = []; refresh() })
onMounted(refresh)
onBeforeUnmount(() => clearTimeout(timer))
defineExpose({persist, preserve, refresh})
</script>

<style scoped>
.recovery-card { margin-bottom: 12px; flex: none; }
.recovery-card :deep(.el-card__header) { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.recovery-row { display: flex; justify-content: space-between; gap: 8px; margin: 8px 0; }
.muted { color: #667085; font-size: 12px; }
</style>
