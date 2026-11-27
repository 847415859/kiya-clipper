<template>
  <div class="page acceptance-page">
    <div class="toolbar">
      <div>
        <h2>新场验收</h2>
        <p class="muted">只读验收：刷新、展开和播放不会入队、续跑、付费或发布。</p>
      </div>
      <span class="spacer" />
      <el-radio-group v-model="source" :disabled="loading || drawer" @change="changeSource"><el-radio-button value="acceptance">隔离验收队列</el-radio-button><el-radio-button value="production">生产队列</el-radio-button></el-radio-group>
      <el-button :loading="loading" @click="load">刷新验收</el-button>
      <el-button @click="router.push('/sessions')">返回场次</el-button>
    </div>

    <el-alert v-if="error" type="error" :title="error" show-icon :closable="false" />
    <section class="overview" v-loading="loading">
      <div class="conclusion" :class="conclusionType">
        <span class="eyebrow">当前结论</span>
        <strong>{{ conclusion }}</strong>
        <span>{{ monitorText }}</span>
      </div>
      <div class="facts">
        <div><b>{{ sessions.length }}</b><span>可核对场次</span></div>
        <div><b>{{ unmatched }}</b><span>待核对任务</span></div>
        <div><b>{{ liveText }}</b><span>监控状态</span></div>
      </div>
    </section>

    <el-alert v-if="!observed" type="info" title="尚无轮询记录" description="页面无法确认监控是否观察到下播。" :closable="false" />
    <el-alert v-else-if="apiFailures" type="warning" :title="`监控最近有 ${apiFailures} 次查询失败`" description="当前直播状态未知；保留显示最后一次成功记录，时区以原记录为准。" :closable="false" />

    <p v-if="loadedAt" class="muted">页面读取于 {{ dateLabel(loadedAt) }} · {{ source === 'acceptance' ? '隔离验收' : '生产' }} · 观察到 {{ sessions.length + unmatched }} 条任务（非重复触发验证结论）</p>
    <el-collapse v-if="overview?.unmatchedTasks?.length">
      <el-collapse-item title="待核对的下播 / 队列任务" name="unmatched">
        <div v-for="task in overview.unmatchedTasks" :key="task.jobId" class="match-box"><b>{{ task.jobId }}</b><span>房间 {{ task.room || '未知' }}</span><span>{{ task.queued ? '已入队' : '下播记录' }}</span><span>{{ task.message }}</span><span>监控观察下播：{{ dateLabel(task.observedEnd) }}</span></div>
      </el-collapse-item>
    </el-collapse>
    <section class="session-list">
      <div class="section-title"><h3>场次验收</h3><span class="muted">按记录时间倒序</span></div>
      <el-empty v-if="!sessions.length && !loading" description="暂无可匹配的下播场次" />
      <article v-for="row in sessions" :key="row.name" class="session-row">
        <div class="row-main">
          <div class="row-title"><strong>{{ row.name }}</strong><el-tag size="small" :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag></div>
          <p>{{ row.message || '暂无补充说明' }}</p>
          <div class="row-meta"><span v-if="row.jobId">任务 {{ row.jobId }}</span><span v-if="row.currentStep">当前：{{ stepLabel(row.currentStep) }}</span><span v-if="row.updatedAt">更新 {{ dateLabel(row.updatedAt) }}</span></div>
        </div>
        <div class="row-stats"><b>{{ row.clipCount || 0 }}</b><span>可播放成片</span><small v-if="row.totalCandidates">候选 {{ row.completedCandidates || 0 }}/{{ row.totalCandidates }}</small></div>
        <el-button type="primary" plain @click="open(row.name)">查看详情</el-button>
      </article>
    </section>

    <el-drawer v-model="drawer" :title="`新场验收 · ${source === 'acceptance' ? '隔离' : '生产'} · ${name}`" size="min(880px, 100vw)" destroy-on-close @closed="detailRequestId++">
      <div v-loading="detailLoading" class="detail">
        <template v-if="detail">
          <div class="detail-conclusion" :class="statusType(detail.status)"><span>当前结论</span><strong>{{ statusLabel(detail.status) }}</strong><p>{{ detail.message || '暂无说明' }}</p></div>
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="任务">{{ detail.jobId || '未入队 / 未记录' }}</el-descriptions-item>
            <el-descriptions-item label="房间">{{ detail.room || '未知' }}</el-descriptions-item>
            <el-descriptions-item label="当前步骤">{{ detail.currentStep ? stepLabel(detail.currentStep) : '尚未领取' }}</el-descriptions-item>
            <el-descriptions-item label="最后更新">{{ detail.updatedAt ? dateLabel(detail.updatedAt) : '未知' }}</el-descriptions-item>
            <el-descriptions-item label="下次处理">{{ detail.retryAt ? dateLabel(detail.retryAt) : '未记录' }}</el-descriptions-item>
            <el-descriptions-item label="预算">{{ budgetText(detail.budget) }}</el-descriptions-item>
          </el-descriptions>

          <h4>监控与入队匹配</h4>
          <div class="match-box"><span>监控：{{ detail.monitor?.observed ? (detail.monitor.live == null ? '查询失败或状态未知' : detail.monitor.live ? '最近观察为直播中' : '最近观察为未直播') : '尚无轮询记录' }}</span><span>下播事件：{{ detail.match?.endedCount ? '已记录' : '未记录' }}</span><span>队列：{{ detail.match?.jobId || '未入队' }}</span><span>唯一匹配：{{ detail.match?.unique ? '是' : '待核对' }}</span></div>

          <p v-if="detail.match?.endedAt" class="muted">监控观察下播时间：{{ dateLabel(detail.match.endedAt) }}</p>
          <p v-if="detail.blocker" class="blocker">当前卡点：{{ detail.blocker }}</p>
          <h4>制作时间线</h4>
          <el-timeline v-if="detail.timeline?.length"><el-timeline-item v-for="(event, i) in detail.timeline" :key="i" :timestamp="event.at ? dateLabel(event.at) : '未知'" :type="event.status === 'done' || event.status === 'inventoried' ? 'success' : event.status === 'paused' || event.status === 'failed' ? 'danger' : 'primary'"><b>{{ stepLabel(event.stage) }}</b> · {{ event.status }}<p v-if="event.reason" class="muted">{{ event.reason }}</p></el-timeline-item></el-timeline><p v-else class="muted">已入队，等待制作；尚无 Worker 领取记录。</p>

          <h4>候选与当前版本</h4>
          <el-empty v-if="!detail.candidates?.length" description="尚未产生候选，或候选处置未落盘" />
          <el-collapse v-else>
            <el-collapse-item v-for="item in detail.candidates" :key="item.rank" :name="item.rank">
              <template #title><span>候选 #{{ item.rank }} · {{ candidateLabel(item.status) }}</span><el-tag v-if="item.playable" size="small" type="success">已有合格成片</el-tag></template>
              <div class="candidate-grid"><span>当前版本：{{ item.version || '未知' }}</span><span>步骤：{{ stepLabel(item.stage) || '未知' }}</span><span>媒体：{{ item.playable ? '可播放' : '无有效库存' }}</span></div>
              <el-alert v-if="item.findings?.length" type="warning" :closable="false" :title="item.findings[0].message || '存在卡点'" />
              <div v-if="item.review?.missingRepairFields" class="muted">审片记录缺少返修动作或成片区间；需要人工复核。</div>
              <div v-for="(finding, idx) in item.findings || []" :key="idx" class="muted">{{ rangeText(finding.range, finding.timeline) }}</div>
              <div class="reports"><div><b>审片（{{ item.review?.bound ? '当前版本' : '未绑定当前版本' }}）</b>：{{ reviewLabel(item.review) }}<span v-if="item.review?.range">（{{ rangeText(item.review.range, item.review.timeline) }}）</span><span v-if="item.review?.reason">；{{ item.review.reason }}</span></div><div><b>技术质检（{{ item.quality?.bound ? '当前版本' : '未绑定当前版本' }}）</b>：{{ qualityLabel(item.quality) }}<span v-if="item.quality?.errors?.length">；{{ item.quality.errors.join('；') }}</span></div><div><b>证据覆盖</b>：{{ item.evidence?.status || '未记录' }}</div><div><b>媒体规格</b>：{{ item.quality?.data?.width || item.quality?.data?.checks?.resolution?.width || '未知' }} × {{ item.quality?.data?.height || item.quality?.data?.checks?.resolution?.height || '未知' }} · {{ item.quality?.data?.durationSeconds || item.quality?.data?.checks?.file_integrity?.duration || '未知' }} 秒</div></div>
              <div v-if="item.versions?.length" class="version-compare"><div class="compare-controls"><el-select v-model="compareVersions[item.rank]" aria-label="对比版本" placeholder="选择历史版本"><el-option v-for="version in item.versions" :key="version.version" :label="`${version.current ? '当前' : '历史'} · ${version.version.slice(0, 12)}`" :value="version.version" :disabled="!version.playable" /></el-select><el-button :disabled="!compareVersions[item.rank]" @click="seekCompare(item.rank)">定位当前版问题</el-button><el-button :disabled="!compareVersions[item.rank]" @click="seekCompare(item.rank, null, true)">定位历史版问题</el-button><el-button :disabled="!compareVersions[item.rank]" @click="seekCompare(item.rank, videos[`current-${item.rank}`]?.currentTime)">按原片锚点同步</el-button></div><div v-if="compareVersions[item.rank]" class="compare-grid"><div><b>当前通过版本</b><video v-if="item.playable" :ref="el => videos[`current-${item.rank}`] = el" class="preview" controls preload="metadata" :src="api.operationsMedia(name, item.rank, source === 'acceptance')"></video><p class="muted">{{ item.inventoryVersion || item.qualifiedVersion || '无有效库存版本' }} · {{ item.playable ? '已入库 / 可播放' : '无有效库存' }}</p></div><div><b>选定历史版本（不代表已入库）</b><video :ref="el => videos[`compare-${item.rank}`] = el" class="preview" controls preload="metadata" :src="api.operationsMedia(name, item.rank, source === 'acceptance', compareVersions[item.rank])"></video><p class="muted">{{ versionById(item, compareVersions[item.rank])?.version }} · 父版 {{ versionById(item, compareVersions[item.rank])?.parent || '无' }} · 审片 {{ versionById(item, compareVersions[item.rank])?.review?.status || '未知' }}（{{ versionById(item, compareVersions[item.rank])?.reviewBound ? '哈希绑定' : '未绑定' }}） · SHA-256 {{ versionById(item, compareVersions[item.rank])?.mediaSha256 || '未知' }}</p></div></div><p v-if="compareMessage[item.rank]" class="muted" role="status">{{ compareMessage[item.rank] }}</p><div v-if="compareVersions[item.rank]" class="reports"><b>历史版 → 当前库存版改动</b><el-empty v-if="!compareDiff(item).length" description="无已知编辑差异或版本清单不完整" /><div v-for="(change, idx) in compareDiff(item)" :key="idx"><b>{{ change.action }} · {{ change.target }}</b><pre class="report-data">{{ JSON.stringify({ before: change.before, after: change.after }, null, 2) }}</pre></div></div></div><el-collapse v-if="item.versions?.length > 1 || item.attempts?.length || item.events?.length"><el-collapse-item title="历史版本与处理审计" :name="`history-${item.rank}`"><div v-for="version in item.versions" :key="version.version">版本 {{ version.version }} · {{ version.current ? '当前' : '历史' }} · 父版本 {{ version.parent || '未知' }}<span> · {{ version.playable ? '可播放' : '媒体失效/未绑定' }} · {{ version.accepted ? '已入库' : '历史预览' }} · {{ version.simulation === true ? '模拟夹具' : version.simulation === false ? '实际媒体审片' : '证据类型未知' }}</span><pre class="report-data">{{ JSON.stringify({ review: version.review, quality: version.quality, evidence: version.evidence }, null, 2) }}</pre></div><p v-for="(attempt, idx) in item.attempts" :key="`attempt-${idx}`">{{ attempt.purpose || '请求' }} · {{ attempt.status || '状态未知' }} · 预留上限 {{ money(attempt.maximum_fen) }} · 已知结算 {{ money(attempt.actual_fen) }} · 用量 {{ attempt.usage ? JSON.stringify(attempt.usage) : '未记录' }} · HTTP 尝试次数与缓存命中：未记录</p><p v-for="(event, idx) in item.events" :key="`event-${idx}`">{{ stepLabel(event.stage) }} · {{ event.status }} · {{ event.reason || '' }}</p></el-collapse-item></el-collapse>
              <el-collapse><el-collapse-item title="质检项目、证据映射与检查点" :name="`reports-${item.rank}`"><pre class="report-data">{{ JSON.stringify({ quality: item.quality, evidence: item.evidence, steps: item.steps }, null, 2) }}</pre></el-collapse-item></el-collapse>
              <video v-if="item.playable" :ref="el => videos[item.rank] = el" class="preview" controls preload="metadata" :src="api.operationsMedia(name, item.rank, source === 'acceptance')"></video>
              <el-button v-if="item.playable && item.review?.range?.length === 2 && item.review.timeline === 'output'" size="small" @click="seek(item.rank, item.review.range[0])">定位成片问题位置</el-button>
            </el-collapse-item>
          </el-collapse>
        </template>
        <el-alert v-if="detailError" type="warning" :title="detailError" :closable="false" />
        <el-empty v-else-if="!detailLoading && !detail" description="验收详情读取失败" />
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { acceptanceTime as dateLabel, money, statusLabel, rangeLabel } from '../acceptance'
import { mappedSeek, editChanges, versionMapping } from '../versionCompare'
const source = ref('acceptance')
const videos = {}
const compareVersions = ref({})
function seek(rank, seconds) { const video = videos[rank]; if (video && Number.isFinite(seconds)) video.currentTime = Math.max(0, seconds) }
function versionById (item, id) { return item?.versions?.find(version => version.version === id) }
const compareMessage = ref({})
function qualifiedVersion (item) { return versionById(item, item.inventoryVersion || item.qualifiedVersion || item.version) }
function compareDiff (item) { return editChanges(versionById(item, compareVersions.value[item.rank])?.draft?.edit, qualifiedVersion(item)?.draft?.edit) }
function seekCompare (rank, seconds = null, fromHistorical = false) {
  const item = detail.value?.candidates?.find(row => row.rank === rank)
  const historical = versionById(item, compareVersions.value[rank])
  const current = qualifiedVersion(item)
  const origin = fromHistorical ? historical : current
  const target = fromHistorical ? current : historical
  const review = fromHistorical ? historical?.review : current?.review || item?.review
  const time = Number.isFinite(seconds) ? seconds : review?.timeline === 'output' ? review.range?.[0] : null
  if (!Number.isFinite(time)) { compareMessage.value[rank] = '缺少该版本的成片定位；可独立播放核对。'; return }
  const destination = mappedSeek(time, versionMapping(origin), versionMapping(target))
  const originVideo = videos[`${fromHistorical ? 'compare' : 'current'}-${rank}`]
  if (originVideo) originVideo.currentTime = Math.max(0, time)
  if (destination === null) { compareMessage.value[rank] = '另一版本未覆盖同一原片锚点或映射未知；仅定位问题所属版本。'; return }
  const targetVideo = videos[`${fromHistorical ? 'current' : 'compare'}-${rank}`]
  if (targetVideo) targetVideo.currentTime = Math.max(0, destination)
  compareMessage.value[rank] = `已按同一原片锚点定位：${time.toFixed(2)} 秒 ↔ ${destination.toFixed(2)} 秒`
}
const loadedAt = ref('')
const detailError = ref('')
let requestId = 0
let detailRequestId = 0

const router = useRouter()
const loading = ref(false); const error = ref(''); const overview = ref(null); const drawer = ref(false); const name = ref(''); const detail = ref(null); const detailLoading = ref(false)
const sessions = computed(() => overview.value?.sessions || [])
const observed = computed(() => !!overview.value?.observed); const apiFailures = computed(() => overview.value?.apiFailures || 0); const unmatched = computed(() => overview.value?.unmatched || 0)
const liveText = computed(() => !observed.value || overview.value?.live == null ? '未知' : overview.value.live ? '直播中' : '未直播')
const conclusion = computed(() => !observed.value ? '等待监控记录' : apiFailures.value || overview.value?.monitor?.stale ? '监控状态可能已过期' : overview.value.live ? '已观察到直播，等待下播' : sessions.value.length ? `${statusLabel(sessions.value[0].status)}` : unmatched.value ? '已有任务，录播匹配待核对' : '已记录未直播，尚无下播任务')
const conclusionType = computed(() => apiFailures.value || overview.value?.monitor?.stale ? 'warning' : overview.value?.live ? 'live' : 'normal')
const monitorText = computed(() => overview.value?.lastCheck ? `最近记录：${dateLabel(overview.value.lastCheck)}${overview.value.monitor?.timeBasis ? ` · ${overview.value.monitor.timeBasis}` : ''}` : '没有可用的轮询时间')
const statusType = (v) => ({ has_clips: 'success', processing: 'primary', queued: 'info', waiting_for_budget: 'warning', waiting_for_resources: 'warning', failed: 'danger', needs_review: 'warning', no_candidates: 'info' }[v] || 'info')
const candidateLabel = (v) => ({ inventoried: '已有成片', needs_review: '待复核', paused: '已暂停', failed: '失败', dropped: '不适合制作', pending: '等待处理', running: '处理中' }[v] || '状态未知')
const stepLabel = (v) => ({ recording: '核对录播', transcribe: '语音转写', screen: '粗筛', rerank: '精排', produce: '候选制作', topic: '选题', inventory: '入库', inputs: '核对输入', 'initial-edit': '生成初稿', queue: '等待领取' }[v] || (/^evidence-/.test(v || '') ? '补证据' : /^render-/.test(v || '') ? '渲染' : /^review-/.test(v || '') ? '审片' : v || '未知'))
const budgetText = (b) => !b ? '未记录' : `${b.day || '日期未知'} · 剩余 ${money(b.remainingFen)} · 预留 ${money(b.bookedFen)}`
const reviewLabel = (r) => r?.status === 'pass' ? '通过' : r?.status === 'repair' ? '返修' : r?.status === 'unknown' ? '未记录' : '未通过 / 待复核'; const qualityLabel = (q) => q?.status === 'pass' ? '通过' : q?.status === 'fail' ? '失败' : '未记录'
function rangeText (r, timeline) { return rangeLabel(r, timeline) }
async function load () {
  if (loading.value) return
  const request = ++requestId
  loading.value = true
  try { const result = await api.acceptanceOperations(source.value); if (request === requestId) { overview.value = result; loadedAt.value = new Date().toISOString(); error.value = '' } }
  catch { if (request === requestId) error.value = overview.value ? '刷新失败，仍显示上次成功结果，状态可能已过期' : '验收数据读取失败，请稍后刷新' }
  finally { if (request === requestId) loading.value = false }
}
async function changeSource () { overview.value = null; await load() }
async function open (session) {
  const request = ++detailRequestId
  compareVersions.value = {}; compareMessage.value = {}
  for (const key of Object.keys(videos)) { videos[key]?.pause(); delete videos[key] }
  name.value = session; drawer.value = true; detail.value = null; detailError.value = ''; detailLoading.value = true
  try { const result = await api.acceptanceDetail(session, source.value); if (request === detailRequestId) detail.value = result }
  catch { if (request === detailRequestId) detailError.value = '详情读取失败，请刷新重试' }
  finally { if (request === detailRequestId) detailLoading.value = false }
}
onMounted(load)
</script>

<style scoped>
.acceptance-page { max-width: 1280px; margin: 0 auto; }
.report-data { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 360px; overflow: auto; background: #f5f7fa; padding: 12px; }
.blocker { color: #b54708; overflow-wrap: anywhere; }
h2, h3, h4 { margin: 0; } h4 { margin-top: 24px; margin-bottom: 10px; }
.toolbar h2 { margin-bottom: 4px; } .overview { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 12px; margin: 16px 0; }
.conclusion { display: flex; flex-direction: column; gap: 8px; padding: 20px; border-left: 4px solid #409eff; background: white; border-radius: 4px; box-shadow: 0 1px 4px #0000000d; } .conclusion.warning { border-color: #e6a23c; } .conclusion.live { border-color: #67c23a; } .conclusion strong { font-size: 22px; } .eyebrow { color: #909399; font-size: 12px; }
.facts { display: grid; grid-template-columns: repeat(3, 1fr); background: white; border: 1px solid #ebeef5; } .facts div { display: flex; flex-direction: column; align-items: center; justify-content: center; border-right: 1px solid #ebeef5; gap: 6px; } .facts div:last-child { border: 0; } .facts b { font-size: 20px; } .facts span { color: #909399; font-size: 12px; }
.session-list { margin-top: 18px; } .section-title { display: flex; align-items: baseline; gap: 10px; margin-bottom: 10px; } .session-row { display: flex; align-items: center; gap: 18px; background: white; border: 1px solid #ebeef5; padding: 14px 16px; margin-bottom: 8px; } .row-main { flex: 1; min-width: 0; } .row-title { display: flex; align-items: center; gap: 10px; } .row-main p { margin: 6px 0; color: #606266; } .row-meta { display: flex; gap: 14px; flex-wrap: wrap; color: #909399; font-size: 12px; } .row-stats { width: 90px; text-align: center; display: flex; flex-direction: column; gap: 3px; } .row-stats b { font-size: 19px; } .row-stats span, .row-stats small { color: #909399; font-size: 12px; }
.detail-conclusion { padding: 14px; border-left: 4px solid #909399; background: #f5f7fa; margin-bottom: 16px; } .detail-conclusion strong { margin-left: 12px; } .detail-conclusion p { margin: 8px 0 0; } .detail-conclusion.success { border-color: #67c23a; } .detail-conclusion.danger { border-color: #f56c6c; } .match-box, .candidate-grid, .reports { display: flex; gap: 14px; flex-wrap: wrap; padding: 12px; background: #f8f9fb; color: #606266; font-size: 13px; } .reports { display: block; line-height: 1.9; background: transparent; padding: 8px 0; } .preview { width: min(100%, 560px); max-height: 320px; background: #111; margin-top: 12px; } .version-compare { margin: 14px 0; border-top: 1px solid #ebeef5; padding-top: 12px; } .compare-controls { display: flex; gap: 8px; align-items: center; } .compare-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 10px; } .compare-grid > div { min-width: 0; } .spacer { flex: 1; }
.version-compare { overflow-wrap: anywhere; }
.compare-controls { flex-wrap: wrap; }
.compare-controls :deep(.el-select) { flex: 1 1 180px; min-width: 0; }
.compare-controls :deep(.el-button) { margin-left: 0; }
@media (max-width: 700px) { .overview { grid-template-columns: 1fr; } .session-row { align-items: flex-start; flex-wrap: wrap; } .row-stats { width: 70px; } .compare-grid { grid-template-columns: minmax(0, 1fr); } }
</style>
