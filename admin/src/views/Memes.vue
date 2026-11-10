<template>
  <div class="page" v-loading="loading">
    <div class="toolbar">
      <el-button type="primary" @click="refresh(false)">刷新梗库（抓B站热门）</el-button>
      <el-button @click="refresh(true)">只重建（不联网）</el-button>
      <span class="muted" v-if="lib">更新于 {{ lib.generated_at || '—' }} · 已攒 {{ lib.hot_days || 0 }} 天热门快照</span>
      <span class="spacer" />
      <span class="muted">每天自动抓一次（只取热门标题 / 热搜文字）；新梗要在这里确认含义和尺度后，剪辑初稿才会用</span>
    </div>

    <el-card v-if="jobId" shadow="never" class="card">
      <JobLog :job-id="jobId" @done="load" />
    </el-card>

    <el-alert v-if="lib && !lib.memes.length && !loading" type="info" :closable="false" show-icon class="card"
              title="梗库还是空的：点“刷新梗库”。主播档案里的梗、弹幕 / 转写里发现的梗和B站热门里的高频说法都会进来。" />
    <el-alert v-if="lib && counts.pending" type="warning" :closable="false" show-icon class="card"
              :title="`有 ${counts.pending} 个新梗待确认：看一眼含义和尺度，通过后剪辑初稿才会用（拿不准的就不要通过，宁缺毋滥）。`" />

    <el-card shadow="never" class="card">
      <el-tabs v-model="tab">
        <el-tab-pane v-for="t in tabs" :key="t.key" :name="t.key" :label="`${t.label} ${t.n()}`" />
      </el-tabs>
      <div class="row">
        <el-input v-model="q" placeholder="搜梗" clearable style="width: 180px" />
        <span class="spacer" />
        <el-button @click="openAdd">手动添加一个梗</el-button>
      </div>
      <el-table :data="shown" size="small" :empty-text="'这里还没有梗'" row-key="text">
        <el-table-column label="梗" min-width="150">
          <template #default="{ row }">
            <b>{{ row.text }}</b>
            <el-tag v-if="row.cooling" size="small" type="info" class="tr">降温</el-tag>
            <el-tag v-if="row.low_retention" size="small" type="warning" class="tr">常被删</el-tag>
            <div v-if="row.meaning" class="muted">{{ row.meaning }}</div>
          </template>
        </el-table-column>
        <el-table-column label="来源" min-width="120">
          <template #default="{ row }">{{ (row.sources || []).join(' / ') }}</template>
        </el-table-column>
        <el-table-column label="热度" width="90">
          <template #default="{ row }">{{ row.evergreen ? '常青' : row.heat == null ? '—' : row.heat }}</template>
        </el-table-column>
        <el-table-column label="用过 · 保留" width="110">
          <template #default="{ row }">
            <span v-if="!row.uses" class="muted">—</span>
            <span v-else>{{ row.uses }} 次 · {{ row.retention == null ? '待看' : Math.round(row.retention * 100) + '%' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="尺度" width="76">
          <template #default="{ row }">{{ levelName[row.level] || '大众' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType[row.status]">{{ statusName[row.status] }}</el-tag>
            <div v-if="row.blocked_by" class="muted">命中「{{ row.blocked_by }}」</div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="210">
          <template #default="{ row }">
            <template v-if="row.status !== 'blacklisted'">
              <el-button size="small" link type="primary" @click="openEdit(row)">{{ row.status === 'approved' ? '编辑' : '通过…' }}</el-button>
              <el-button v-if="row.status === 'approved'" size="small" link @click="setStatus(row, 'disabled')">停用</el-button>
              <el-button v-if="row.status === 'disabled'" size="small" link @click="setStatus(row, 'pending')">恢复</el-button>
              <el-button size="small" link type="danger" @click="blacklist(row)">拉黑</el-button>
            </template>
            <el-button v-else-if="!row.blocked_by || isBlackWord(row.blocked_by)" size="small" link @click="unblack(row)">移出黑名单</el-button>
            <span v-else class="muted">禁区话题（精排记忆页管理）</span>
            <el-button v-if="row.decided && row.status !== 'blacklisted'" size="small" link @click="forget(row)">{{ isManual(row) ? '删除' : '还原' }}</el-button>
          </template>
        </el-table-column>
      </el-table>
      <p class="muted hint">
        热度：B站热门里近 7 天的加权日均出现次数（越近越重要）；跌破阈值或弹幕里近 14 天没人提，检索权重 ×0.3。
        「常被删」= 用过 5 次以上、人工终稿里留下的不到 40%，权重 ×0.5。黑名单词按“包含”匹配。
      </p>
    </el-card>

    <el-dialog v-model="dlg.show" :title="dlg.manual ? '手动添加一个梗' : `${dlg.status === 'approved' ? '编辑' : '通过'}：${dlg.text}`" width="560px">
      <el-form label-width="76px">
        <el-form-item v-if="dlg.manual" label="梗"><el-input v-model="dlg.text" maxlength="40" placeholder="梗的原话" /></el-form-item>
        <el-form-item label="含义"><el-input v-model="dlg.meaning" maxlength="80" show-word-limit placeholder="一句话说清楚这是什么意思（必填）" /></el-form-item>
        <el-form-item label="来历"><el-input v-model="dlg.origin" maxlength="100" placeholder="选填，哪来的" /></el-form-item>
        <el-form-item label="怎么用"><el-input v-model="dlg.usage" maxlength="80" placeholder="选填，什么场面适合用（剪辑初稿靠含义 + 用法判断）" /></el-form-item>
        <el-form-item label="尺度">
          <el-radio-group v-model="dlg.level">
            <el-radio :value="1">大众</el-radio>
            <el-radio :value="2">圈层</el-radio>
            <el-radio :value="3">谨慎（不会自动用）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="形式">
          <el-checkbox-group v-model="dlg.forms">
            <el-checkbox value="text">花字</el-checkbox>
            <el-checkbox value="sticker">贴图</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="标签"><el-select v-model="dlg.tags" multiple filterable allow-create default-first-option :multiple-limit="5" placeholder="选填，如：翻车、钓鱼" style="width: 100%" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg.show = false">取消</el-button>
        <el-button type="primary" :disabled="!dlg.meaning.trim() || (dlg.manual && dlg.text.trim().length < 2)" @click="save">{{ dlg.manual ? '添加并启用' : '启用' }}</el-button>
      </template>
    </el-dialog>

    <el-card shadow="never" class="card">
      <template #header><b>黑名单词</b> <span class="muted">（梗里包含这些词的一律不用，也不会再进“待确认”）</span></template>
      <div class="tags">
        <el-tag v-for="w in lib ? lib.blacklist : []" :key="w" type="danger" closable @close="act({ action: 'blacklistRemove', word: w })">{{ w }}</el-tag>
        <span v-if="!lib || !lib.blacklist.length" class="muted">暂无</span>
      </div>
      <div class="row">
        <el-input v-model="newBlack" maxlength="20" placeholder="加一个黑名单词（至少 2 个字）" style="width: 240px" />
        <el-button :disabled="newBlack.trim().length < 2" @click="addBlack">添加</el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import JobLog from '../components/JobLog.vue'

const loading = ref(true)
const lib = ref(null)
const jobId = ref(null)
const tab = ref('pending')
const q = ref('')
const newBlack = ref('')
const levelName = { 1: '大众', 2: '圈层', 3: '谨慎' }
const statusName = { pending: '待确认', approved: '已启用', disabled: '已停用', blacklisted: '黑名单' }
const statusType = { pending: 'warning', approved: 'success', disabled: 'info', blacklisted: 'danger' }
const dlg = reactive({ show: false, manual: false, text: '', status: 'approved', meaning: '', origin: '', usage: '', level: 1, forms: ['text'], tags: [] })

const memes = computed(() => (lib.value ? lib.value.memes : []))
const counts = computed(() => (lib.value ? lib.value.counts : {}))
const tabs = [
  { key: 'pending', label: '待确认', n: () => counts.value.pending || 0 },
  { key: 'approved', label: '已启用', n: () => counts.value.approved || 0 },
  { key: 'off', label: '停用 / 黑名单', n: () => (counts.value.disabled || 0) + (counts.value.blacklisted || 0) },
  { key: 'all', label: '全部', n: () => memes.value.length }
]
const shown = computed(() => {
  const k = q.value.trim().toLowerCase()
  return memes.value.filter((m) => {
    if (k && !m.text.toLowerCase().includes(k)) return false
    if (tab.value === 'all') return true
    if (tab.value === 'off') return m.status === 'disabled' || m.status === 'blacklisted'
    return m.status === tab.value
  })
})

async function load() {
  loading.value = true
  try {
    lib.value = await api.memes()
  } finally {
    loading.value = false
  }
}

async function act(body) {
  await api.memoryAction(body)
  await load()
}

function isBlackWord(w) {
  return lib.value && lib.value.blacklist.some((x) => x.toLowerCase() === String(w).toLowerCase())
}
function isManual(row) {
  return (row.sources || []).includes('手动添加')
}

function fill(row) {
  Object.assign(dlg, {
    show: true, manual: false, text: row.text, status: 'approved', meaning: row.meaning || '', origin: row.origin || '', usage: row.usage || '',
    level: row.level || 1, forms: row.forms && row.forms.length ? [...row.forms] : ['text'], tags: [...(row.tags || [])]
  })
}
function openEdit(row) {
  fill(row)
}
function openAdd() {
  Object.assign(dlg, { show: true, manual: true, text: '', status: 'approved', meaning: '', origin: '', usage: '', level: 1, forms: ['text'], tags: [] })
}
async function save() {
  await act({
    action: 'memeSave', text: dlg.text.trim(), status: 'approved', meaning: dlg.meaning.trim(), origin: dlg.origin.trim(), usage: dlg.usage.trim(),
    level: dlg.level, forms: dlg.forms.length ? dlg.forms : ['text'], tags: dlg.tags, manual: dlg.manual || undefined
  })
  dlg.show = false
  ElMessage.success('已启用，下一次生成剪辑初稿就会用上')
}
function setStatus(row, status) {
  return act({
    action: 'memeSave', text: row.text, status, meaning: row.meaning || '', origin: row.origin || '', usage: row.usage || '',
    level: row.level || 1, forms: row.forms && row.forms.length ? row.forms : ['text'], tags: row.tags || [], manual: isManual(row) || undefined
  })
}
function blacklist(row) {
  return act({ action: 'blacklistAdd', word: row.text })
}
function unblack(row) {
  return act({ action: 'blacklistRemove', word: row.text })
}
function forget(row) {
  return act({ action: 'memeRemove', text: row.text })
}
async function addBlack() {
  await act({ action: 'blacklistAdd', word: newBlack.value.trim() })
  newBlack.value = ''
}

async function refresh(noFetch) {
  const j = await api.submitJob('MEME_REFRESH', null, noFetch ? { noFetch: true } : {})
  jobId.value = j.id
}

onMounted(load)
</script>

<style scoped>
.card { margin-bottom: 16px; }
.toolbar, .row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }
.spacer { flex: 1; }
.muted { color: var(--el-text-color-secondary); font-size: 13px; }
.tags { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
.tr { margin-left: 6px; }
.hint { margin: 10px 0 0; line-height: 1.6; }
</style>
