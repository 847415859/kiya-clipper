<template>
  <div class="page">
    <div class="toolbar">
      <el-button @click="load">刷新</el-button>
      <el-button @click="run('HOTWORDS_SYNC')">同步热词表</el-button>
      <el-button @click="run('EVALUATE')">运行评测</el-button>
      <span class="spacer" />
      <span class="muted">同一时间只运行一个任务，其余排队（服务器只有 2 核）</span>
    </div>
    <el-table :data="jobs" size="small" stripe>
      <el-table-column prop="id" label="#" width="60" />
      <el-table-column prop="typeName" label="任务" min-width="150" />
      <el-table-column prop="session" label="场次" width="130" />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="(STATUS[row.status] || {}).type" size="small">{{ (STATUS[row.status] || {}).text || row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="提交时间" width="160">
        <template #default="{ row }">{{ fmtTime(row.createdAt) }}</template>
      </el-table-column>
      <el-table-column label="用时" width="90">
        <template #default="{ row }">{{ took(row) }}</template>
      </el-table-column>
      <el-table-column prop="summary" label="结果" min-width="260" show-overflow-tooltip />
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openLog(row)">日志</el-button>
          <el-button v-if="row.status === 'PENDING' || row.status === 'RUNNING'" link type="danger" @click="cancel(row)">取消</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-drawer v-model="drawer" :title="logTitle" size="70%" destroy-on-close>
      <JobLog v-if="logId" :job-id="logId" @done="load" />
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, fmtTime, STATUS } from '../api'
import JobLog from '../components/JobLog.vue'

const route = useRoute()
const jobs = ref([])
const drawer = ref(false)
const logId = ref(null)
const logRow = ref(null)
let timer = null

const logTitle = computed(() => (logRow.value ? `#${logRow.value.id} ${logRow.value.typeName} ${logRow.value.session || ''}` : '日志'))

async function load() {
  jobs.value = await api.jobs({ limit: 100 })
}

function took(row) {
  if (!row.startedAt) return '—'
  const end = row.finishedAt ? new Date(row.finishedAt) : new Date()
  const s = Math.max(0, Math.round((end - new Date(row.startedAt)) / 1000))
  return s >= 60 ? `${Math.floor(s / 60)} 分 ${s % 60} 秒` : `${s} 秒`
}

function openLog(row) {
  logRow.value = row
  logId.value = row.id
  drawer.value = true
}

async function run(type) {
  const j = await api.submitJob(type)
  ElMessage.success(`已提交：${j.typeName}`)
  await load()
  openLog(j)
}

async function cancel(row) {
  await ElMessageBox.confirm(`取消任务 #${row.id}（${row.typeName}）？`, '取消任务', { type: 'warning' })
  await api.cancelJob(row.id)
  load()
}

async function tick() {
  try {
    if (jobs.value.some((j) => j.status === 'PENDING' || j.status === 'RUNNING')) await load()
  } finally {
    timer = setTimeout(tick, 3000)
  }
}

onMounted(async () => {
  await load()
  const open = Number(route.query.open)
  if (open) {
    const row = jobs.value.find((j) => j.id === open)
    if (row) openLog(row)
  }
  tick()
})
onBeforeUnmount(() => clearTimeout(timer))
</script>
