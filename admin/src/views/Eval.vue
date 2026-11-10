<template>
  <div class="page">
    <div class="toolbar">
      <el-button type="primary" @click="run('EVALUATE')">运行评测</el-button>
      <el-button @click="run('EVAL_SWEEP')">分数线扫描</el-button>
      <el-button @click="logResult">评测并记录…</el-button>
      <el-tooltip content="对比粗筛排序和大模型精排排序，哪个更接近你的 Y/N 标注（需要已精排并标注的场次）">
        <el-button @click="run('EVAL_RERANK')">精排对比</el-button>
      </el-tooltip>
      <span class="spacer" />
      <span class="muted">标准答案 = 审核页的 Y/N 标注 + eval/annotations.csv 里手写的漏掉片段；攒够 3 场再据此调参数</span>
    </div>

    <el-card v-if="jobId" shadow="never" style="margin-bottom: 16px">
      <JobLog :job-id="jobId" @done="loadResults" />
    </el-card>

    <h3>历史记录 <span class="muted">（eval/results.csv，每次“评测并记录”追加一行）</span></h3>
    <el-table :data="results" size="small" stripe empty-text="还没有记录">
      <el-table-column v-for="col in columns" :key="col" :prop="col" :label="labels[col] || col"
                       :min-width="col === 'change' || col === 'note' ? 200 : 100" show-overflow-tooltip />
    </el-table>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessageBox } from 'element-plus'
import { api } from '../api'
import JobLog from '../components/JobLog.vue'

const results = ref([])
const jobId = ref(null)
const labels = {
  date: '时间', change: '改了什么', config_version: '配置版本', sessions: '场次数', top10_hit_rate: 'Top10 命中（旧）',
  recall: '召回率', note: '信号', precision: '精确率', candidates: '送审数', top10_coverage: '前 10 覆盖'
}
const columns = computed(() => (results.value.length ? Object.keys(results.value[0]) : []))

async function loadResults() {
  results.value = (await api.evalResults()).slice().reverse()
}

async function run(type, params) {
  const j = await api.submitJob(type, null, params)
  jobId.value = j.id
}

async function logResult() {
  const { value } = await ElMessageBox.prompt('这次改了什么？（会和评测结果一起记到 eval/results.csv）', '评测并记录', {
    inputPlaceholder: '例如：第 3 场；情绪词加了“绷”', inputValidator: (v) => (!!v && v.length <= 100) || '1–100 个字'
  })
  run('EVAL_LOG', { note: value })
}

onMounted(loadResults)
</script>
