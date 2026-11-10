<template>
  <div class="page">
    <div class="toolbar">
      <el-button type="primary" :loading="scanning" @click="scan">扫描录像</el-button>
      <el-button @click="load">刷新</el-button>
      <span class="spacer" />
      <span class="muted">录像在服务器上只保留 2 天，之后同步到 OSS 并从本地删除；转写和剪切要在这之前做</span>
    </div>

    <el-table :data="rows" size="small" stripe v-loading="loading" empty-text="还没有场次，点“扫描录像”">
      <el-table-column prop="name" label="场次" width="130" fixed />
      <el-table-column label="时长" width="110">
        <template #default="{ row }">{{ fmtDuration(row.durationSeconds) }}</template>
      </el-table-column>
      <el-table-column label="录像" width="110">
        <template #default="{ row }">
          <span v-if="row.segments">{{ row.videosPresent }}/{{ row.segments }} 段
            <el-tooltip v-if="row.videosPresent < row.segments" content="部分录像已不在本地，只能用弹幕分析">
              <el-tag size="small" type="warning">缺</el-tag>
            </el-tooltip>
          </span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="弹幕" width="80">
        <template #default="{ row }">{{ row.danmaku ?? '—' }}</template>
      </el-table-column>
      <el-table-column label="进度" min-width="300">
        <template #default="{ row }">
          <el-space size="small" wrap>
            <el-tag size="small" :type="row.hasTranscript ? 'success' : 'info'">{{ row.hasTranscript ? '已转写' : '未转写' }}</el-tag>
            <el-tag size="small" :type="row.hasCandidates ? 'success' : 'info'">
              {{ row.hasCandidates ? `候选 ${row.candidateCount}` : '未粗筛' }}
            </el-tag>
            <el-tag v-if="row.live" size="small" type="warning">边播边筛·临时</el-tag>
            <el-tag size="small" :type="row.clipCount ? 'success' : 'info'">片段 {{ row.clipCount }}</el-tag>
            <el-tag v-if="row.hasRerank" size="small" type="success">已精排</el-tag>
            <el-tag v-if="row.hasCandidates" size="small" :type="row.reviewed >= row.candidateCount && row.candidateCount ? 'success' : 'warning'">
              已审 {{ row.reviewed }}/{{ row.candidateCount }}，有趣 {{ row.funny }}
            </el-tag>
          </el-space>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="230" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" :disabled="!row.hasCandidates" @click="review(row)">审核</el-button>
          <el-dropdown trigger="click" @command="(c) => run(row, c)">
            <el-button link type="primary">运行 ▾</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="PIPELINE">全流程（转写 → 粗筛 → 剪切）</el-dropdown-item>
                <el-dropdown-item command="TRANSCRIBE" divided>语音转写</el-dropdown-item>
                <el-dropdown-item command="SCREEN">粗筛</el-dropdown-item>
                <el-dropdown-item command="RERANK" :disabled="!row.hasCandidates || !row.hasTranscript">大模型精排</el-dropdown-item>
                <el-dropdown-item command="CUT" :disabled="!row.hasCandidates">剪切</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-button link @click="openTranscript(row)" :disabled="!row.hasTranscript">原话</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialog" :title="`${typeName[form.type]} · ${form.session}`" width="480px">
      <el-form label-width="96px">
        <template v-if="form.type === 'TRANSCRIBE' || form.type === 'PIPELINE'">
          <el-form-item label="识别模型">
            <el-radio-group v-model="form.model">
              <el-radio value="">按配置</el-radio>
              <el-radio value="fun-asr">fun-asr</el-radio>
              <el-radio value="paraformer-v2">paraformer-v2</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="重新转写">
            <el-switch v-model="form.refresh" />
            <span class="muted" style="margin-left: 8px">已转写过的默认跳过</span>
          </el-form-item>
        </template>
        <template v-if="form.type === 'SCREEN' || form.type === 'PIPELINE'">
          <el-form-item label="跳过画面">
            <el-switch v-model="form.noVideo" />
            <span class="muted" style="margin-left: 8px">只用弹幕 + 音频，更快</span>
          </el-form-item>
        </template>
        <template v-if="form.type === 'CUT' || form.type === 'PIPELINE'">
          <el-form-item label="分辨率">
            <el-radio-group v-model="form.height">
              <el-radio :value="480">480p（审核用）</el-radio>
              <el-radio :value="720">720p</el-radio>
              <el-radio :value="1080">1080p</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="字幕">
            <el-select v-model="form.styleId" placeholder="默认样式" clearable :disabled="form.noSubs" style="width: 200px">
              <el-option v-for="s in styles" :key="s.id" :label="s.name + (s.isDefault ? '（默认）' : '')" :value="s.id" />
            </el-select>
            <el-checkbox v-model="form.noSubs" style="margin-left: 12px">不加字幕</el-checkbox>
          </el-form-item>
          <el-form-item v-if="form.type === 'CUT'" label="只剪序号">
            <el-input v-model="form.ranks" placeholder="留空剪全部，例如 1 3 5" />
          </el-form-item>
        </template>
        <template v-if="form.type === 'RERANK'">
          <p class="muted" style="margin: 0 0 12px">
            每个候选抽 8 张截图 + 原话 + 弹幕交给多模态大模型打分、找笑点、修正起止、出标题候选。
            按所选模型的价格计费（任务日志最后会打印 token 用量）；结果有缓存，重跑不重复收费。
          </p>
          <el-form-item label="只精排序号">
            <el-input v-model="form.ranks" placeholder="留空全部，例如 1 3 5" />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" @click="submit">提交</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, fmtDuration } from '../api'

const router = useRouter()
const rows = ref([])
const styles = ref([])
const loading = ref(false)
const scanning = ref(false)
const dialog = ref(false)
const typeName = { PIPELINE: '全流程', TRANSCRIBE: '语音转写', SCREEN: '粗筛', RERANK: '大模型精排', CUT: '剪切' }
const form = reactive({ type: 'CUT', session: '', model: '', refresh: false, noVideo: false, height: 480, styleId: null, noSubs: false, ranks: '' })

async function load() {
  loading.value = true
  try { rows.value = await api.sessions() } finally { loading.value = false }
}

async function scan() {
  scanning.value = true
  try {
    const r = await api.scan()
    ElMessage.success(`扫描到 ${r.count} 场`)
    await load()
  } finally { scanning.value = false }
}

function review(row) {
  router.push(`/sessions/${row.name}/review`)
}

function openTranscript(row) {
  window.open(api.transcriptUrl(row.name), '_blank', 'noopener')
}

async function run(row, type) {
  Object.assign(form, { type, session: row.name, model: '', refresh: false, noVideo: false, height: 480, styleId: null, noSubs: false, ranks: '' })
  if (!styles.value.length) styles.value = await api.styles()
  dialog.value = true
}

async function submit() {
  const p = {}
  if (form.type === 'TRANSCRIBE' || form.type === 'PIPELINE') {
    if (form.model) p.model = form.model
    if (form.refresh) p.refresh = true
  }
  if ((form.type === 'SCREEN' || form.type === 'PIPELINE') && form.noVideo) p.noVideo = true
  if (form.type === 'RERANK') {
    const ranks = String(form.ranks || '').split(/[\s,，]+/).filter(Boolean).map(Number)
    if (ranks.some((n) => !Number.isInteger(n) || n < 1)) return ElMessage.error('序号格式不对')
    if (ranks.length) p.ranks = ranks
  }
  if (form.type === 'CUT' || form.type === 'PIPELINE') {
    p.height = form.height
    if (form.noSubs) p.noSubs = true
    else if (form.styleId) p.styleId = form.styleId
    const ranks = String(form.ranks || '').split(/[\s,，]+/).filter(Boolean).map(Number)
    if (ranks.some((n) => !Number.isInteger(n) || n < 1)) return ElMessage.error('序号格式不对')
    if (ranks.length) p.ranks = ranks
  }
  const j = await api.submitJob(form.type, form.session, p)
  dialog.value = false
  ElMessage.success(`已提交任务 #${j.id}`)
  router.push({ path: '/jobs', query: { open: j.id } })
}

onMounted(load)
</script>
