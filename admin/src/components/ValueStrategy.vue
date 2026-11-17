<template>
  <el-button size="small" @click="open">价值评审 / 探索</el-button>
  <el-dialog v-model="visible" title="价值评审与非峰值探索" width="min(96vw, 1100px)">
    <p>探索候选单独保存，正式推荐保持原策略。模型评审有每场预算，不会自动投稿。</p>
    <el-space wrap>
      <el-input-number v-model="budget" :min="0" :max="10" aria-label="探索候选预算" />
      <el-checkbox v-model="semantic">语义补充（最多 3 次模型调用）</el-checkbox>
      <el-button :loading="busy" @click="run('VALUE_RECALL')">召回探索候选</el-button>
      <el-button :loading="busy" :disabled="!exploration.length" @click="run('VALUE_EXPLORE')">评审探索候选（调用模型）</el-button>
      <el-button @click="refresh">刷新结果</el-button>
    </el-space>
    <p v-if="job">任务 #{{ job }} 已提交，可到任务页查看进度；完成后刷新结果。</p>
    <el-divider>人工补充时间（整场秒数）</el-divider>
    <el-space wrap>
      <el-input-number v-model="start" :min="0" aria-label="开始秒数" />
      <span>至</span><el-input-number v-model="end" :min="0" aria-label="结束秒数" />
      <el-input v-model="note" placeholder="想保留的自然反应或事件" maxlength="200" />
      <el-button :loading="busy" :disabled="end <= start" @click="run('VALUE_RECALL', true)">保存时间标记</el-button>
    </el-space>
    <el-table :data="exploration" max-height="280" style="margin-top:12px">
      <el-table-column prop="rank" label="探索序号" width="100" />
      <el-table-column label="整场范围" width="170"><template #default="{ row }">{{ Math.round(row.clip_start) }}–{{ Math.round(row.clip_end) }} 秒</template></el-table-column>
      <el-table-column label="来源" width="150"><template #default="{ row }">{{ sources[row.recall_source] || row.recall_source }}</template></el-table-column>
      <el-table-column prop="recall_note" label="线索（待核实）" />
    </el-table>
    <p v-if="data.exploration_rerank?.stale">探索候选已更新，旧评审不再展示，请重新评审。</p>
    <details v-if="data.exploration_candidates?.recall_skipped?.length"><summary>查看合并或跳过的线索</summary><div v-for="(item, i) in data.exploration_candidates.recall_skipped" :key="i">{{ item.reason }}</div></details>
    <template v-if="showAi">
      <el-divider>新旧评审对照（仅快审展示）</el-divider>
      <el-table :data="comparisons" max-height="350">
        <el-table-column prop="rank" label="序号" width="65" />
        <el-table-column prop="new_rank" label="原排名" width="85" />
        <el-table-column prop="shadow_rank" label="价值排名" width="90" />
        <el-table-column label="分流" width="100"><template #default="{ row }">{{ routes[row.shadow_route] || '未评审' }}</template></el-table-column>
        <el-table-column label="依据 / 待修复原因"><template #default="{ row }">{{ row.value_llm?.what_happened }} {{ (row.shadow_reasons || []).join('；') }}<details v-if="row.value_llm?.value_evidence?.length"><summary>查看证据</summary><div v-for="(e, i) in row.value_llm.value_evidence" :key="i">{{ e.dimension }} · {{ e.src }} [{{ e.t }} 秒] {{ e.quote }} — {{ e.why }}</div></details><details v-if="row.value_llm?.title_options?.length"><summary>标题与包装建议（需人工采用）</summary><div v-for="(t, i) in row.value_llm.title_options" :key="i">{{ t.title }} · {{ t.why }}</div><div>封面：{{ row.value_llm.cover_text || '无建议' }}；开头：{{ row.value_llm.hook_caption || '无建议' }}</div></details></template></el-table-column>
      </el-table>
    </template>
    <p v-else>盲审模式隐藏 AI 分数、分流与结论；完成独立判断后，可切换快审查看对照。</p>
  </el-dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'

const props = defineProps({ session: { type: String, required: true }, showAi: Boolean })
const visible = ref(false), busy = ref(false), data = ref({}), job = ref(null)
const semantic = ref(false)
const budget = ref(5), start = ref(0), end = ref(15), note = ref('')
const sources = { semantic_model: '原话语义评审', manual: '人工时间标记', transcript_semantic: '逐字稿线索', non_peak_sample: '非峰值抽样' }
const routes = { quarantine: '待复核', repair: '可修复', low_value: '低价值', normal: '可进入人工选择' }
const exploration = computed(() => data.value.exploration_candidates?.candidates || [])
const comparisons = computed(() => [...(data.value.rerank?.candidates || []), ...(data.value.exploration_rerank?.candidates || [])])
async function refresh() { data.value = await api.valueStrategy(props.session) }
async function open() { visible.value = true; await refresh() }
async function run(type, manual = false) {
  busy.value = true
  try {
    const params = { budget: budget.value, semantic: semantic.value }
    if (manual) Object.assign(params, { start: start.value, end: end.value, note: note.value })
    const result = await api.submitJob(type, props.session, params)
    job.value = result.id
    ElMessage.success('任务已提交')
  } finally { busy.value = false }
}
</script>
