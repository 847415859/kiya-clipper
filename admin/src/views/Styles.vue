<template>
  <div class="page">
    <div class="toolbar">
      <el-button type="primary" @click="create">新建样式</el-button>
      <span class="spacer" />
      <span class="muted">默认样式用于：监控自动剪切、命令行 cut.py、剪切时没选样式的情况</span>
    </div>

    <el-table :data="styles" size="small" stripe>
      <el-table-column prop="name" label="名称" min-width="160">
        <template #default="{ row }">
          {{ row.name }} <el-tag v-if="row.isDefault" size="small" type="success">默认</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="code" label="代码" width="140" />
      <el-table-column label="字体 / 字号" min-width="180">
        <template #default="{ row }">{{ row.config.font }} / {{ row.config.font_size }}</template>
      </el-table-column>
      <el-table-column label="特点" min-width="200">
        <template #default="{ row }">
          <el-space size="small" wrap>
            <el-tag v-if="row.config.box" size="small">底框</el-tag>
            <el-tag v-if="row.config.karaoke" size="small">逐字变色</el-tag>
            <el-tag v-if="row.config.highlight" size="small">关键词换色</el-tag>
            <el-tag size="small" type="info">{{ posName[row.config.position] || row.config.position }}</el-tag>
          </el-space>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="260" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="edit(row)">编辑</el-button>
          <el-button link :disabled="row.isDefault" @click="setDefault(row)">设为默认</el-button>
          <el-button link @click="copy(row)">复制</el-button>
          <el-button link type="danger" :disabled="row.isDefault" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialog" :title="editing.id ? `编辑：${editing.name}` : '新建样式'" width="1100px" top="4vh">
      <div class="editor">
        <div class="form-col">
          <el-form v-if="!editing.id" label-width="132px" size="small">
            <el-form-item label="样式代码">
              <el-input v-model="editing.code" placeholder="小写字母 / 数字 / 下划线，如 pink_box" />
            </el-form-item>
          </el-form>
          <StyleForm v-if="schema" v-model="editing.config" :schema="schema" />
        </div>
        <div class="preview-col">
          <div class="toolbar">
            <el-select v-model="pv.session" placeholder="背景：纯色（可选真实画面）" clearable style="width: 210px" @change="loadClips">
              <el-option v-for="s in sessionsWithClips" :key="s.name" :value="s.name" :label="s.name" />
            </el-select>
            <el-select v-model="pv.clip" placeholder="片段" :disabled="!pv.session" style="width: 160px">
              <el-option v-for="c in clips" :key="c" :value="c" :label="c" />
            </el-select>
            <el-input-number v-model="pv.at" :min="0" :max="600" size="small" controls-position="right" style="width: 100px" />
            <span class="muted">秒</span>
          </div>
          <el-button type="primary" plain :loading="previewing" @click="preview">生成预览</el-button>
          <div class="preview-box">
            <img v-if="previewUrl" :src="previewUrl" alt="字幕预览" />
            <div v-else class="muted">点“生成预览”看效果（由服务器上的 worker 实际渲染，和剪出来的一致）</div>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import StyleForm from '../components/StyleForm.vue'

const posName = { bottom: '底部', middle: '中间', top: '顶部' }
const styles = ref([])
const schema = ref(null)
const dialog = ref(false)
const saving = ref(false)
const previewing = ref(false)
const previewUrl = ref('')
const editing = reactive({ id: null, code: '', name: '', config: {} })
const sessions = ref([])
const clips = ref([])
const pv = reactive({ session: '', clip: '', at: 5 })

const sessionsWithClips = computed(() => sessions.value.filter((s) => s.clipCount > 0))

async function load() {
  styles.value = await api.styles()
}

function defaults() {
  const out = {}
  for (const [k, p] of Object.entries(schema.value.properties)) {
    out[k] = JSON.parse(JSON.stringify(p.default))
  }
  return out
}

async function open() {
  if (!schema.value) schema.value = await api.styleSchema()
  if (!sessions.value.length) sessions.value = await api.sessions()
  clearPreview()
  dialog.value = true
}

async function create() {
  await open()
  Object.assign(editing, { id: null, code: '', name: '新样式', config: { ...defaults(), name: '新样式' } })
}

async function edit(row) {
  await open()
  Object.assign(editing, { id: row.id, code: row.code, name: row.name, config: { ...defaults(), ...JSON.parse(JSON.stringify(row.config)) } })
}

async function copy(row) {
  await open()
  Object.assign(editing, {
    id: null, code: row.code + '_copy', name: row.name + ' 副本',
    config: { ...defaults(), ...JSON.parse(JSON.stringify(row.config)), name: row.name + ' 副本' }
  })
}

async function save() {
  saving.value = true
  try {
    if (editing.id) await api.updateStyle(editing.id, editing.config)
    else await api.createStyle(editing.code, editing.config)
    ElMessage.success('已保存')
    dialog.value = false
    load()
  } finally {
    saving.value = false
  }
}

async function setDefault(row) {
  await api.defaultStyle(row.id)
  ElMessage.success(`默认样式改为“${row.name}”，之后的自动剪切都会用它`)
  load()
}

async function remove(row) {
  await ElMessageBox.confirm(`删除样式“${row.name}”？`, '删除', { type: 'warning' })
  await api.deleteStyle(row.id)
  load()
}

async function loadClips() {
  pv.clip = ''
  clips.value = []
  if (!pv.session) return
  const d = await api.candidates(pv.session)
  clips.value = (d.candidates || []).map((c) => c.clip).filter(Boolean)
  pv.clip = clips.value[0] || ''
}

function clearPreview() {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = ''
}

async function preview() {
  previewing.value = true
  try {
    const blob = await api.previewStyle({ config: editing.config, session: pv.session || null, clip: pv.clip || null, at: pv.at })
    clearPreview()
    previewUrl.value = URL.createObjectURL(blob)
  } catch (e) {
    // 出错时后端返回的是 JSON，但 responseType 是 blob，需要自己读出来
    let msg = e.message
    const d = e.response && e.response.data
    if (d instanceof Blob) {
      try { msg = JSON.parse(await d.text()).error || msg } catch (x) { /* 不是 JSON 就用默认提示 */ }
    }
    ElMessage.error(msg)
  } finally {
    previewing.value = false
  }
}

onMounted(load)
onBeforeUnmount(clearPreview)
</script>

<style scoped>
.editor { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.form-col { max-height: 70vh; overflow: auto; padding-right: 8px; }
.preview-box { margin-top: 12px; min-height: 200px; border: 1px dashed var(--el-border-color); border-radius: 8px; display: flex; align-items: center; justify-content: center; padding: 8px; }
.preview-box img { max-width: 100%; border-radius: 6px; }
@media (max-width: 1000px) { .editor { grid-template-columns: 1fr; } }
</style>
