<template>
  <div class="page">
    <div class="toolbar">
      <el-button type="primary" :loading="scanning" @click="scan">扫描录像</el-button>
      <el-button @click="load">刷新</el-button>
      <el-button @click="lookupOpen = true">按场次排查</el-button>
      <el-button @click="router.push('/sessions/acceptance')">新场验收</el-button>
      <span class="spacer" />
      <span class="muted">录像按计划同步到 OSS。磁盘空间不足时，优先清理最老的已归档场次。</span>
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
      <el-table-column label="自动制作" min-width="205">
      <template #default="{ row }">
        <el-tag size="small" :type="opType(row.operations?.status)">{{ opLabel(row.operations?.status) }}</el-tag>
        <span v-if="row.operations?.clipCount" class="ops-count">成片 {{ row.operations.clipCount }}</span>
        <div v-if="row.operations?.totalCandidates" class="muted">完成 {{ row.operations.completedCandidates }}/{{ row.operations.totalCandidates }}</div>
        <div v-if="row.operations?.currentStep" class="muted">{{ stepLabel(row.operations.currentStep) }}</div>
      </template>
    </el-table-column>
    <el-table-column label="操作" width="345" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openOperations(row.name)">制作详情</el-button>
          <el-button link type="primary" @click="openTrial(row.name)">隔离试跑</el-button>
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

    <el-dialog v-model="trialOpen" title="历史场次 · 隔离试跑" width="min(760px, 95vw)" @closed="trialRequest++">
      <div v-loading="trialLoading">
        <p><b>场次：{{ trialSession }}</b></p>
        <el-alert v-if="trialError" type="error" :title="trialError" :closable="false" />
        <template v-if="trialOptions">
          <p>来源：历史场次独立任务 · 模式：{{ trialOptions.mode }} · 仅进入隔离库存，不自动投稿</p>
          <p>费用上限：<b>{{ money(trialOptions.maxCostFen) }}</b><span v-if="!Number.isFinite(trialOptions.maxCostFen)">（费用上限未知，暂停提交）</span></p>
          <p>运行步骤：{{ (trialOptions.steps || []).map(step => stepLabel(typeof step === 'string' ? step : step.name || step.stage)).join(' → ') || '未确定' }}</p>
          <el-alert v-for="(blocker, i) in trialOptions.blockers || []" :key="i" type="warning" :title="reasonText(blocker)" :closable="false" />
          <el-table :data="trialOptions.candidates || []" empty-text="无已有候选">
            <el-table-column label="选择" width="65"><template #default="{ row }"><el-radio v-model="trialRank" :value="row.rank" :disabled="!row.available || trialSubmitting" :aria-label="`候选 ${row.rank}`">{{ '' }}</el-radio></template></el-table-column>
            <el-table-column label="候选" width="85"><template #default="{ row }">#{{ row.rank }}</template></el-table-column>
            <el-table-column label="原片区间" width="140"><template #default="{ row }">{{ row.start }}–{{ row.end }} 秒</template></el-table-column>
            <el-table-column label="素材与条件"><template #default="{ row }"><el-tag :type="row.available ? 'success' : 'warning'">{{ row.available ? '素材可读' : '条件未满足' }}</el-tag><div>{{ missingText(row) }}</div><div v-for="(blocker, i) in row.blockers || []" :key="i">{{ reasonText(blocker) }}</div></template></el-table-column>
          </el-table>
          <p v-if="trialCandidate">确认候选 #{{ trialRank }}，运行上述步骤，费用上限 {{ money(trialOptions.maxCostFen) }}。</p>
          <p v-if="trialCandidate?.activeTask">进行中任务 {{ trialCandidate.activeTask.taskId }}：重复提交将返回该任务。<el-button link type="primary" @click="router.push(trialLink({ ...trialCandidate.activeTask, session: trialSession }))">查看进行中任务</el-button></p>
          <p v-else-if="trialCandidate?.lastTask">历史任务 {{ trialCandidate.lastTask.taskId }}：{{ opLabel(trialCandidate.lastTask.status) }}。新一轮试跑请勾选明确重跑。</p>
          <p v-if="trialCandidate?.lastTask && !trialCandidate?.activeTask"><el-checkbox v-model="trialRerun" :disabled="trialSubmitting">明确重跑已结束任务（保留历史记录）</el-checkbox></p>
          <el-checkbox v-model="trialConfirmed" :disabled="trialSubmitting">我确认场次、候选、步骤及费用上限，提交隔离试跑</el-checkbox>
        </template>
        <el-alert v-if="trialResult" type="success" :closable="false" :title="`任务 ${trialResult.taskId} · ${trialResult.message || '已提交'}`" />
        <el-button v-if="trialResult" link type="primary" @click="router.push(trialLink(trialResult))">查看任务详情</el-button>
      </div>
      <template #footer><el-button :disabled="trialSubmitting" @click="openTrial(trialSession)">刷新条件</el-button><el-button type="primary" :loading="trialSubmitting" :disabled="!trialCanSubmit" @click="submitTrial">提交隔离试跑</el-button></template>
    </el-dialog>

    <el-dialog v-model="lookupOpen" title="按场次排查" width="min(420px, 95vw)">
      <el-form @submit.prevent="openOperations(lookupName.trim())">
        <el-form-item label="场次"><el-input v-model="lookupName" placeholder="例如 20261124-2151" /></el-form-item>
        <el-button native-type="submit" type="primary" :disabled="!lookupName.trim()">查询</el-button>
      </el-form>
    </el-dialog>

    <el-dialog v-model="acceptanceOpen" title="新场验收" width="min(720px, 95vw)">
      <div v-loading="acceptanceLoading">
        <template v-if="acceptance">
          <p v-if="acceptance.observed">
            {{ acceptance.apiFailures ? '最近一次直播状态查询失败' : acceptance.live ? '最近记录为直播中' : '最近记录为未直播' }}
            · {{ acceptance.lastCheck }}
          </p>
          <p v-else>还没有监控轮询记录。</p>
          <el-alert v-if="acceptance.unmatched" type="warning" :closable="false"
            :title="`有 ${acceptance.unmatched} 条任务尚未匹配到唯一录播场次`" />
          <el-table :data="acceptance.sessions" empty-text="尚无已匹配的下播任务">
            <el-table-column prop="name" label="场次" min-width="130" />
            <el-table-column label="状态" min-width="110">
              <template #default="{ row }">
                <el-tag size="small" :type="opType(row.status)">{{ opLabel(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="当前步骤" min-width="100">
              <template #default="{ row }">{{ row.currentStep ? stepLabel(row.currentStep) : '—' }}</template>
            </el-table-column>
            <el-table-column label="成片" width="65" prop="clipCount" />
            <el-table-column width="92">
              <template #default="{ row }">
                <el-button link type="primary" @click="openOperations(row.name, true)">查看详情</el-button>
              </template>
            </el-table-column>
          </el-table>
        </template>
        <p v-else-if="!acceptanceLoading">暂时无法读取验收状态。</p>
      </div>
      <template #footer>
        <el-button :loading="acceptanceLoading" @click="refreshAcceptance">刷新状态</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="opsOpen" :title="`${opsAcceptance ? '新场验收' : '自动制作'} · ${opsName}`" width="min(720px, 95vw)">
      <div v-loading="opsLoading" class="ops-detail">
        <template v-if="ops">
          <el-space wrap>
            <el-tag :type="opType(ops.status)">{{ opLabel(ops.status) }}</el-tag>
            <span v-if="ops.totalCandidates">完成 {{ ops.completedCandidates }}/{{ ops.totalCandidates }}</span>
            <span v-if="ops.clipCount">成片 {{ ops.clipCount }}</span>
            <span v-if="ops.needsReviewCount">待复核 {{ ops.needsReviewCount }}</span>
          </el-space>
          <p v-if="ops.message">{{ ops.message }}</p>
          <p v-if="ops.blocker" class="ops-findings">{{ ops.blocker }}</p>
          <dl class="ops-meta">
            <template v-if="ops.room"><dt>房间</dt><dd>{{ ops.room }}</dd></template>
            <template v-if="ops.jobId"><dt>任务</dt><dd>{{ ops.jobId }}</dd></template>
            <template v-if="ops.currentStep"><dt>当前步骤</dt><dd>{{ stepLabel(ops.currentStep) }}</dd></template>
            <template v-if="ops.updatedAt"><dt>最后更新</dt><dd>{{ dateLabel(ops.updatedAt) }}</dd></template>
            <template v-if="ops.retryAt"><dt>下次可处理</dt><dd>{{ dateLabel(ops.retryAt) }}</dd></template>
            <template v-if="ops.budget"><dt>预算快照</dt><dd>剩余 ¥{{ (ops.budget.remainingFen / 100).toFixed(2) }}（{{ ops.budget.day }}）</dd></template>
          </dl>
          <el-alert v-if="ops.resumeRequested" title="已请求续跑，等待自动队列领取" type="info" :closable="false" />
          <el-collapse v-if="ops.candidates?.length" class="ops-candidates">
            <el-collapse-item v-for="item in ops.candidates" :key="item.rank" :name="item.rank">
              <template #title>候选 {{ item.rank }} · {{ candidateLabel(item.status) }}</template>
              <a v-if="item.hasMedia" :href="api.operationsMedia(opsName, item.rank, opsAcceptance)" target="_blank" rel="noopener">打开成片</a>
              <ul v-if="item.findings?.length" class="ops-findings">
                <li v-for="(finding, idx) in item.findings" :key="idx">
                  {{ finding.message }}
                  <span v-if="finding.range?.length === 2">（{{ finding.timeline === 'source' ? '原片' : '对应' }} {{ finding.range[0] }}–{{ finding.range[1] }} 秒）</span>
                </li>
              </ul>
              <span v-else-if="item.currentStep" class="muted">{{ stepLabel(item.currentStep) }}</span>
            </el-collapse-item>
          </el-collapse>
        </template>
        <p v-else-if="!opsLoading" class="muted">读取失败，请刷新重试。</p>
      </div>
      <template #footer>
        <el-button @click="refreshOperations">刷新详情</el-button>
        <el-button v-if="ops?.action" type="primary" :loading="recovering" @click="recoverOperations">{{ ops.action === 'resume' ? '续跑本场' : '补入本场' }}</el-button>
      </template>
    </el-dialog>

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
            精排按主播配置分析候选。连续音画模式分窗记录事件、核对关键边界，再给分和建议起止。
            模型调用按实际用量计费，任务日志显示 token 和缓存命中。
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
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, fmtDuration } from '../api'

import { money } from '../acceptance'
import { canSubmitTrial, missingText, reasonText, trialBody, trialLink } from '../isolatedTrials'

const trialOpen = ref(false), trialLoading = ref(false), trialSubmitting = ref(false)
const trialSession = ref(''), trialOptions = ref(null), trialRank = ref(null), trialConfirmed = ref(false)
const trialError = ref(''), trialResult = ref(null), trialRerun = ref(false)
let trialRequest = 0
const trialCandidate = computed(() => trialOptions.value?.candidates?.find(row => row.rank === trialRank.value))
const trialCanSubmit = computed(() => !trialLoading.value && !trialSubmitting.value && canSubmitTrial(trialOptions.value, trialCandidate.value, trialConfirmed.value))
watch(trialRank, () => { trialConfirmed.value = false; trialRerun.value = false; trialResult.value = null })
async function openTrial(session) {
  const request = ++trialRequest
  trialSession.value = session; trialOptions.value = null; trialRank.value = null
  trialConfirmed.value = false; trialRerun.value = false; trialResult.value = null; trialError.value = ''
  trialOpen.value = true; trialLoading.value = true
  try { const result = await api.isolatedTrialOptions(session); if (request === trialRequest) trialOptions.value = result }
  catch (e) { if (request === trialRequest) trialError.value = e.response?.data?.error || '试跑条件读取失败，请刷新' }
  finally { if (request === trialRequest) trialLoading.value = false }
}
async function submitTrial() {
  if (!trialCanSubmit.value) return
  const request = trialRequest, session = trialSession.value
  const last = trialCandidate.value?.lastTask
  trialSubmitting.value = true; trialError.value = ''
  try {
    const result = await api.submitIsolatedTrial(session, trialBody(trialRank.value, trialRerun.value ? (typeof last === 'string' ? last : last?.taskId) : ''))
    if (request === trialRequest) { trialResult.value = { ...result, session: result.session || session }; trialConfirmed.value = false }
  } catch (e) { if (request === trialRequest) trialError.value = e.response?.data?.error || '提交失败，请刷新条件后重试；重复提交将复用进行中任务' }
  finally { trialSubmitting.value = false }
}

const router = useRouter()
const rows = ref([])
const styles = ref([])
const loading = ref(false)
const scanning = ref(false)
const dialog = ref(false)
const lookupOpen = ref(false)
const lookupName = ref('')
const acceptanceOpen = ref(false)
const acceptance = ref(null)
const acceptanceLoading = ref(false)
const opsOpen = ref(false)
const opsName = ref('')
const opsAcceptance = ref(false)
const ops = ref(null)
const opsLoading = ref(false)
const recovering = ref(false)
const opsLabels = { verify: '待核对', not_queued: '未入队', queued: '已入队', processing: '正在处理',
  waiting_for_budget: '等待预算', waiting_for_resources: '等待资源', needs_review: '部分候选待复核',
  no_candidates: '无合适候选', has_clips: '已有成片', failed: '失败', prepared: '处理完成' }
const stepLabels = { recording: '核对录播', transcribe: '语音转写', screen: '粗筛', rerank: '精排', produce: '制作候选',
  topic: '选题', inventory: '入库', inputs: '核对制作输入', 'initial-edit': '生成初稿' }
const candidateLabels = { inventoried: '已有成片', needs_review: '待复核', paused: '已暂停', failed: '失败',
  dropped: '不适合制作', pending: '等待处理', running: '处理中' }
const dateFormat = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'medium', timeZone: 'Asia/Shanghai' })
const dateLabel = (value) => `${dateFormat.format(new Date(value))}（北京时间）`
const opLabel = (status) => opsLabels[status] || '待核对'
const opType = (status) => ({ has_clips: 'success', failed: 'danger', needs_review: 'warning',
  waiting_for_budget: 'warning', waiting_for_resources: 'warning', processing: 'primary' })[status] || 'info'
const stepLabel = (step) => stepLabels[step] || (/^evidence-/.test(step) ? '定向补证据'
  : /^render-/.test(step) ? '渲染' : /^review-/.test(step) ? '审片' : opsLabels[step] || step)
const candidateLabel = (status) => candidateLabels[status] || '处理中'

async function openAcceptance() {
  acceptanceOpen.value = true
  await refreshAcceptance()
}

async function refreshAcceptance() {
  if (acceptanceLoading.value) return
  acceptanceLoading.value = true
  try { acceptance.value = await api.acceptanceOperations() }
  catch { acceptance.value = null }
  finally { acceptanceLoading.value = false }
}

async function openOperations(name, fromAcceptance = false) {
  lookupOpen.value = false
  acceptanceOpen.value = false
  opsName.value = name
  opsAcceptance.value = fromAcceptance
  ops.value = null
  opsOpen.value = true
  await refreshOperations()
}

let detailRequest = 0
async function refreshOperations() {
  const request = ++detailRequest
  opsLoading.value = true
  try {
    const result = await api.sessionOperations(opsName.value, opsAcceptance.value)
    if (request === detailRequest) ops.value = result
  } catch {
    if (request === detailRequest) ops.value = null
  } finally {
    if (request === detailRequest) opsLoading.value = false
  }
}

async function recoverOperations() {
  const name = opsName.value
  const action = ops.value?.action
  if (opsAcceptance.value || !action || recovering.value || opsLoading.value) return
  recovering.value = true
  try {
    const result = await api.recoverSession(name, action)
    if (opsName.value === name && !opsAcceptance.value) ops.value = result
    ElMessage.success(action === 'resume' ? '已提交原任务的续跑请求' : '本场已入队')
    await load()
  } catch {
    if (opsName.value === name && !opsAcceptance.value) await refreshOperations()
  } finally { recovering.value = false }
}
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

<style scoped>
.ops-count { margin-left: 8px; }
.ops-detail { min-height: 60px; }
.ops-meta { display: grid; grid-template-columns: max-content 1fr; gap: 6px 16px; }
.ops-meta dt { color: var(--el-text-color-secondary); }
.ops-meta dd { margin: 0; overflow-wrap: anywhere; }
.ops-candidates { margin-top: 16px; }
.ops-findings { margin: 6px 0 0; padding-left: 20px; overflow-wrap: anywhere; }
</style>
