<template>
  <el-card shadow="never">
    <template #header><b>待采用 AI 草稿</b> <el-button size="small" @click="refresh">刷新草稿</el-button></template>
    <p v-if="!drafts.length">暂无待采用草稿。生成完成后刷新，不会自动替换当前编辑。</p>
    <div v-for="draft in drafts" :key="draft.id">
      <p>{{ draft.generated_at }} · {{ draft.prompt }} · {{ draft.model }}</p>
      <div v-for="variant in draft.variants" :key="variant.key">
        <el-button size="small" @click="selected = variant">比较{{ variant.label }}版</el-button>
        <el-button size="small" :loading="previewing" @click="preview(draft, variant)">预览前10秒</el-button>
        <el-button size="small" :disabled="dirty || draft.base_revision !== revision" @click="adopt(draft, variant)">采用{{ variant.label }}版</el-button>
      </div>
      <p v-if="draft.base_revision !== revision">生成后编辑版本已变化，请重新比较并生成；当前编辑保持不变。</p>
    </div>
    <p v-if="dirty">请先保存当前修改，再采用草稿。</p>
    <EditComparison v-if="selected" :edit="edit" :ai="selected.edit" :candidate-start="candidateStart" />
    <p v-if="previewError">{{ previewError }}</p>
    <video v-if="previewUrl" :src="previewUrl" controls style="width:100%;max-height:360px" />
  </el-card>
</template>
<script setup>
import { ref, onMounted, watch } from 'vue'
import { api } from '../api'
import EditComparison from './EditComparison.vue'
const props = defineProps({ session: String, rank: Number, revision: String, dirty: Boolean, edit: Object, candidateStart: Number })
const emit = defineEmits(['adopted'])
const drafts = ref([]), selected = ref(null)
const previewing = ref(false), previewUrl = ref(''), previewError = ref('')
async function preview(draft, variant) {
  previewing.value = true
  previewError.value = ''
  const session = props.session, rank = props.rank
  try {
    const job = await api.submitJob('RENDER', session, {rank, height:480, draftId:draft.id, draftStyle:variant.key, previewStart:0, previewEnd:Math.min(10,variant.edit.trim.end-variant.edit.trim.start)})
    let state
    do {
      await new Promise(resolve => setTimeout(resolve,2000))
      state = await api.job(job.id)
    } while (['PENDING','RUNNING'].includes(state.status))
    if (state.status !== 'SUCCESS') throw new Error(state.summary || '草稿预览失败')
    if (session !== props.session || rank !== props.rank) return
    const info = await api.getEdit(session,rank)
    const file = info.renderPreviews?.find(p => p.file.includes(`_draft_${draft.id}_${variant.key}_`))?.file
    if (!file) throw new Error('未找到本草稿的预览文件')
    previewUrl.value = api.renderPreviewUrl(session,file)
  } catch (error) { previewError.value = error.message }
  finally { previewing.value = false }
}
async function refresh() { drafts.value = await api.pendingAi(props.session, props.rank) }
async function adopt(draft, variant) {
  if (props.dirty || draft.base_revision !== props.revision) return
  await api.adoptAi(props.session, props.rank, {id: draft.id, key: variant.key, expectedRevision: props.revision})
  selected.value = null
  emit('adopted')
  await refresh()
}
onMounted(refresh)
watch(() => [props.session, props.rank], refresh)
</script>
