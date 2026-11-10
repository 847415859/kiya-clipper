<template>
  <div class="review">
    <div class="topbar">
      <el-button link @click="$router.push('/sessions')">← 场次</el-button>
      <b>{{ name }}</b>
      <span class="muted">已标 {{ done }}/{{ list.length }}，有趣 {{ yes }}<template v-if="done">（{{ Math.round((yes / done) * 100) }}%）</template></span>
      <span v-if="data.signals" class="muted">信号 {{ data.signals }}；弹幕延迟 {{ data.delay }} 秒</span>
      <span v-if="data.minScore != null" class="muted">分数线 {{ data.minScore }}，另有 {{ data.belowThreshold }} 个未过线</span>
      <span class="spacer" />
      <span v-if="done && avgSec" class="muted">平均 {{ avgSec }} 秒/条</span>
      <el-tooltip v-if="data.rerank" placement="bottom">
        <template #content>
          盲审：先自己判断，标注才能用来评测精排、校准权重。<br />
          快审：边看 AI 边审，AI 很有把握的（≥{{ HI }} 分或 ≤{{ LO }} 分）可以按 A 直接采纳；快审的标注不进评测。
        </template>
        <el-radio-group v-model="reviewMode" size="small">
          <el-radio-button value="blind">盲审（评测）</el-radio-button>
          <el-radio-button value="fast">快审（生产）</el-radio-button>
        </el-radio-group>
      </el-tooltip>
      <el-checkbox v-if="data.rerank && reviewMode === 'fast'" v-model="sortAi" size="small">按 AI 排序</el-checkbox>
      <span class="muted keys">Y 有趣 · N 不有趣 · {{ reviewMode === 'fast' ? 'A 采纳 AI · ' : '' }}1–0 原因 · ←/→ 切换 · 空格 播放 · J/L ±5 秒 · E 备注</span>
    </div>

    <div v-if="!list.length && !loading" class="empty-all">
      这场没有候选片段（没有过分数线，或还没粗筛）。
    </div>

    <div v-else class="body">
      <aside class="list">
        <div v-for="(c, i) in list" :key="c.rank" :class="['item', { cur: i === cur }]" @click="select(i)">
          <span class="r">#{{ c.rank }}</span>
          <span>{{ hms(c.t) }}</span>
          <span v-if="tabooHits(c).length" class="taboo-mark" title="命中禁区话题">禁</span>
          <span v-if="aiVisible(c) && c.llm && !c.llm.error" class="ai" :class="{ hot: c.llm.funny }">AI {{ c.llm.score }}</span>
          <el-tag size="small" :type="c.mark === 'Y' ? 'success' : c.mark === 'N' ? 'danger' : 'info'" class="badge">
            {{ c.mark === 'Y' ? '有趣' : c.mark === 'N' ? '不有趣' : c.clip ? '未标' : '未剪' }}
          </el-tag>
        </div>
      </aside>

      <section v-if="c" class="main">
        <video v-if="c.clip" ref="video" :key="c.clip" :src="api.clipUrl(name, c.clip)" controls autoplay preload="auto" />
        <div v-else class="empty">
          <p>这个片段还没剪。</p>
          <el-button :loading="cutting" type="primary" @click="cutOne">剪切这个片段（480p，默认字幕样式）</el-button>
          <p v-if="cutting" class="muted">2 核服务器上 1 分钟片段约需 30–60 秒，前面有任务时要先排队…</p>
        </div>

        <div v-if="tabooHits(c).length" class="taboo-bar">
          命中禁区话题：{{ tabooHits(c).join('、') }}。精排会把它排到最后；确定不发的话直接标 N。
        </div>
        <div v-if="adoptable(c)" class="adopt">
          AI 很有把握：{{ adoptable(c) === 'Y' ? '有趣' : '不值得切' }}（{{ c.llm.score }} 分）
          <el-button size="small" type="primary" plain @click="adopt">采纳 (A)</el-button>
        </div>
        <div class="row">
          <el-button :type="c.mark === 'Y' ? 'success' : ''" :plain="c.mark !== 'Y'" @click="mark('Y')">Y 有趣</el-button>
          <el-button :type="c.mark === 'N' ? 'danger' : ''" :plain="c.mark !== 'N'" @click="mark('N')">N 不有趣</el-button>
          <el-button @click="mark('')">清除</el-button>
          <el-button @click="go(-1)">← 上一个</el-button>
          <el-button @click="go(1)">下一个 →</el-button>
          <el-button type="primary" plain @click="$router.push(`/sessions/${name}/edit/${c.rank}`)">编辑成片 →</el-button>
          <span class="saved">{{ saved }}</span>
        </div>
        <div class="reasons">
          <span class="muted">原因（点一下就行，1–0 快捷键）</span>
          <el-check-tag v-for="(r, i) in REASONS" :key="r.code" :checked="(c.reasons || []).includes(r.code)"
                        :class="['rtag', r.group]" @change="toggleReason(r.code)">
            <kbd>{{ (i + 1) % 10 }}</kbd>{{ r.label }}
          </el-check-tag>
        </div>
        <el-input ref="noteInput" v-model="note" placeholder="备注：为什么有趣 / 为什么不行（例如：舰长互动，笑点在 0:35；只是在念 SC；边界太早）"
                  maxlength="500" @change="saveNote" @keydown.stop @keyup.enter="blurNote" />

        <div class="meta">
          <span>序号</span><b>#{{ c.rank }} / {{ list.length }}</b>
          <span>高峰时间</span><b>{{ hms(c.t) }}（片段 {{ hms(c.clipStart) }}–{{ hms(c.clipEnd) }}）</b>
          <span>分数</span><b>{{ (c.score || 0).toFixed(2) }}</b>
          <span>主要信号</span><b>{{ signals(c) }}</b>
          <span>弹幕</span><b>{{ Math.round(c.danmakuPerMin || 0) }} 条/分，情绪词 {{ c.emotionHits }}，礼物 {{ Math.round(c.giftValue || 0) }}</b>
          <span>高频弹幕</span><b>{{ (c.topDanmaku || []).join(' | ') || '—' }}</b>
        </div>

        <div v-if="c.llm && aiVisible(c)" class="aibox">
          <div v-if="c.llm.error" class="muted">AI 精排失败：{{ c.llm.error }}</div>
          <template v-else>
            <div>
              <el-tag size="small" :type="c.llm.funny ? 'success' : 'info'">{{ c.llm.funny ? '有梗' : '一般' }} {{ c.llm.score }}/10</el-tag>
              <el-tag size="small" effect="plain">{{ c.llm.type }}</el-tag>
              <el-tag v-if="c.llm.standalone" size="small" effect="plain" type="success">能单独看懂</el-tag>
              <span class="muted">精排后第 {{ c.llm.newRank }} 名（综合 {{ c.llm.final }}）</span>
            </div>
            <div><b>{{ c.llm.whatHappened }}</b> <span class="muted">{{ c.llm.reason }}</span></div>
            <div v-if="c.llm.story && c.llm.story.punch" class="muted">
              铺垫：{{ c.llm.story.setup || '—' }}｜笑点：{{ c.llm.story.punch }}｜反应：{{ c.llm.story.reaction || '—' }}
            </div>
            <div v-if="c.llm.refined" class="muted">
              建议切 {{ hms(c.llm.refined.start) }}–{{ hms(c.llm.refined.end) }}（{{ Math.round(c.llm.refined.end - c.llm.refined.start) }} 秒，{{ c.llm.refined.note }}）
            </div>
            <div v-if="c.llm.titles && c.llm.titles.length">标题候选：{{ c.llm.titles.join(' ／ ') }}</div>
          </template>
        </div>

        <div v-if="c.speech" class="speech">
          <div class="muted">主播原话（点一句跳到那里；语音识别可能有错字）</div>
          <div v-if="!c.speech.length" class="muted">这段没有识别到说话</div>
          <div v-for="(s, i) in c.speech" :key="i" class="sl" @click="seek(s.t0 - c.clipStart)">
            <span class="st">{{ mmss(s.t0 - c.clipStart) }}</span><template v-for="(p, k) in pieces(s.text)" :key="k"><mark v-if="p.hit" class="tb">{{ p.t }}</mark><template v-else>{{ p.t }}</template></template>
          </div>
        </div>

        <el-collapse v-if="data.hasCurve">
          <el-collapse-item title="本场信号曲线">
            <img :src="api.curveUrl(name)" class="curve" alt="信号曲线" />
          </el-collapse-item>
        </el-collapse>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, hms } from '../api'

const route = useRoute()
const name = computed(() => route.params.name)
const data = ref({})
const all = ref([])
const HI = 8                       // AI 评分 ≥ HI 且判有梗 → 很有把握是“有趣”
const LO = 3                       // AI 评分 ≤ LO → 很有把握是“不值得切”
const REASONS = [
  { code: 'start_early', label: '开头太早', group: 'edge' }, { code: 'start_late', label: '开头太晚', group: 'edge' },
  { code: 'end_early', label: '结尾太早', group: 'edge' }, { code: 'end_late', label: '结尾太晚', group: 'edge' },
  { code: 'not_funny', label: '不好笑', group: 'no' }, { code: 'need_context', label: '要前情才懂', group: 'no' },
  { code: 'read_sc', label: '只是念SC', group: 'no' }, { code: 'repeat_meme', label: '重复的梗', group: 'no' },
  { code: 'taboo', label: '主播不想发', group: 'no' }, { code: 'gem', label: '特别好笑', group: 'yes' },
]
const NEG = new Set(['not_funny', 'need_context', 'read_sc', 'repeat_meme', 'taboo'])
const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d } catch { return d } },
  set(k, v) { try { localStorage.setItem(k, v) } catch { /* 隐私模式下存不了就算了 */ } },
}
const reviewMode = ref(store.get('kiya.reviewMode', 'blind') === 'fast' ? 'fast' : 'blind')
const sortAi = ref(store.get('kiya.sortAi', '') === '1')
watch(reviewMode, (v) => store.set('kiya.reviewMode', v))
watch(sortAi, (v) => store.set('kiya.sortAi', v ? '1' : ''))
const list = computed(() => {
  if (reviewMode.value === 'fast' && sortAi.value) {
    return [...all.value].sort((a, b) => ((a.llm && a.llm.newRank) || 999) - ((b.llm && b.llm.newRank) || 999) || a.rank - b.rank)
  }
  return all.value
})
// 审核耗时：只算页面可见的时间，换一条重新计
let spent = 0
let since = Date.now()
function spentNow() {
  return Math.round(spent + (since ? Date.now() - since : 0))
}
function resetSpent() {
  spent = 0
  since = document.hidden ? 0 : Date.now()
}
function onVisible() {
  if (document.hidden) {
    if (since) spent += Date.now() - since
    since = 0
  } else if (!since) since = Date.now()
}
const cur = ref(0)
const note = ref('')
const saved = ref('')
const loading = ref(true)
const cutting = ref(false)
const video = ref(null)
const noteInput = ref(null)
let savedTimer = null

const c = computed(() => list.value[cur.value])
const done = computed(() => list.value.filter((x) => x.mark).length)
const yes = computed(() => list.value.filter((x) => x.mark === 'Y').length)
const avgSec = computed(() => {
  const t = all.value.filter((x) => x.mark && x.reviewMs > 0)
  return t.length ? Math.round(t.reduce((a, x) => a + x.reviewMs, 0) / t.length / 1000) : 0
})

async function load(keep) {
  loading.value = true
  try {
    data.value = await api.candidates(name.value)
    all.value = data.value.candidates || []
    if (!keep) {
      const first = list.value.findIndex((x) => !x.mark)
      cur.value = first < 0 ? 0 : first
    }
  } finally {
    loading.value = false
  }
}

watch(c, (v) => { note.value = (v && v.note) || ''; resetSpent() })

// 盲审：没标注前不显示 AI 判断，免得先入为主，标注才能用来评测精排；快审始终显示
const aiVisible = (x) => reviewMode.value === 'fast' || !!x.mark

// 'Y' / 'N' = AI 很有把握（只在快审里给“采纳”按钮）；否则 ''
function adoptable(x) {
  if (reviewMode.value !== 'fast' || !x || !x.llm || x.llm.error || x.mark) return ''
  if (x.llm.funny && x.llm.score >= HI) return 'Y'
  if (x.llm.score <= LO) return 'N'
  return ''
}

// 禁区话题：命中的词（后台“精排记忆”页管理）
function tabooHits(x) {
  const words = data.value.taboo || []
  if (!x || !words.length) return []
  const text = ((x.speech || []).map((s) => s.text).join('')).toLowerCase()
  return words.filter((w) => text.includes(w.toLowerCase()))
}

function pieces(text) {
  const words = (data.value.taboo || []).filter(Boolean)
  if (!words.length) return [{ t: text, hit: false }]
  const re = new RegExp('(' + words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')', 'gi')
  return text.split(re).filter((t) => t !== '').map((t) => ({ t, hit: words.some((w) => w.toLowerCase() === t.toLowerCase()) }))
}

function mmss(sec) {
  const s = Math.max(0, Math.round(sec))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

function signals(x) {
  const e = Object.entries(x.contrib || {}).sort((a, b) => b[1] - a[1]).slice(0, 3)
  return e.map(([k, v]) => `${k} ${v > 0 ? '+' : ''}${v}`).join('，') || '—'
}

function flash() {
  saved.value = '已保存'
  clearTimeout(savedTimer)
  savedTimer = setTimeout(() => { saved.value = '' }, 1200)
}

function select(i) {
  cur.value = Math.max(0, Math.min(list.value.length - 1, i))
}

function go(d) {
  select(cur.value + d)
}

async function mark(m, adopted = false) {
  const x = c.value
  if (!x) return
  const body = { mark: m }
  if (m) {
    body.mode = adopted ? 'confirm' : reviewMode.value === 'fast' && !x.mark ? 'fast' : x.mode || 'blind'
    body.ms = spentNow()
  }
  await api.mark(name.value, x.rank, body)
  x.mark = m || null
  if (m) {
    x.mode = body.mode
    x.reviewMs = (x.reviewMs || 0) + body.ms
    resetSpent()
  } else {
    x.mode = null
    x.reviewMs = 0
    x.reasons = []
  }
  flash()
  if (m) setTimeout(() => go(1), 250)
}

function adopt() {
  const v = adoptable(c.value)
  if (v) mark(v, true)
}

async function toggleReason(code) {
  const x = c.value
  if (!x) return
  const cur = new Set(x.reasons || [])
  const on = !cur.has(code)
  on ? cur.add(code) : cur.delete(code)
  const reasons = REASONS.map((r) => r.code).filter((k) => cur.has(k))
  const body = { reasons }
  // 选了“不好笑”这类原因，还没标注的话顺手标 N；“特别好笑”标 Y（不自动跳下一条，可以继续补原因）
  const implied = on && !x.mark ? (NEG.has(code) ? 'N' : code === 'gem' ? 'Y' : '') : ''
  if (implied) {
    body.mark = implied
    body.mode = reviewMode.value === 'fast' ? 'fast' : 'blind'
    body.ms = spentNow()
  }
  await api.mark(name.value, x.rank, body)
  x.reasons = reasons
  if (implied) {
    x.mark = implied
    x.mode = body.mode
    x.reviewMs = (x.reviewMs || 0) + body.ms
    resetSpent()
  }
  flash()
}

async function saveNote() {
  const x = c.value
  if (!x || (x.note || '') === note.value) return
  await api.mark(name.value, x.rank, { note: note.value })
  x.note = note.value
  flash()
}

function blurNote() {
  const el = noteInput.value
  if (el) el.blur()
}

function seek(t) {
  const v = video.value
  if (v) {
    v.currentTime = Math.max(0, t)
    v.play()
  }
}

async function cutOne() {
  const x = c.value
  cutting.value = true
  try {
    const j = await api.submitJob('CUT', name.value, { ranks: [x.rank], height: 480 })
    for (;;) {
      await new Promise((r) => setTimeout(r, 3000))
      const s = await api.job(j.id)
      if (s.status === 'SUCCESS') break
      if (s.status === 'FAILED' || s.status === 'CANCELED') {
        ElMessage.error(`剪切失败：${s.summary || ''}（详情见“任务”页 #${j.id}）`)
        return
      }
    }
    await load(true)
  } finally {
    cutting.value = false
  }
}

function onKey(e) {
  const tag = (e.target && e.target.tagName) || ''
  if (tag === 'INPUT' || tag === 'TEXTAREA' || e.ctrlKey || e.metaKey || e.altKey) return
  const v = video.value
  const k = e.key.toLowerCase()
  if (k === 'y') mark('Y')
  else if (k === 'n') mark('N')
  else if (k === 'a') adopt()
  else if (/^[0-9]$/.test(e.key) && REASONS[(Number(e.key) + 9) % 10]) toggleReason(REASONS[(Number(e.key) + 9) % 10].code)
  else if (e.key === 'ArrowRight') go(1)
  else if (e.key === 'ArrowLeft') go(-1)
  else if (k === 'e') { e.preventDefault(); nextTick(() => noteInput.value && noteInput.value.focus()) }
  else if (v && e.key === ' ') { e.preventDefault(); v.paused ? v.play() : v.pause() }
  else if (v && k === 'j') v.currentTime = Math.max(0, v.currentTime - 5)
  else if (v && k === 'l') v.currentTime = v.currentTime + 5
  else return
}

onMounted(() => {
  load(false)
  window.addEventListener('keydown', onKey)
  document.addEventListener('visibilitychange', onVisible)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  document.removeEventListener('visibilitychange', onVisible)
  clearTimeout(savedTimer)
})
</script>

<style scoped>
.review { height: 100%; display: flex; flex-direction: column; }
.topbar { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; padding: 10px 16px; border-bottom: 1px solid var(--el-border-color-light); background: #fff; }
.topbar .spacer { flex: 1; }
.body { flex: 1; min-height: 0; display: grid; grid-template-columns: 240px 1fr; }
.list { border-right: 1px solid var(--el-border-color-light); overflow: auto; background: #fff; }
.item { display: flex; gap: 8px; align-items: center; padding: 8px 12px; border-bottom: 1px solid var(--el-border-color-lighter); cursor: pointer; }
.item.cur { background: var(--el-color-primary-light-9); }
.item .r { width: 30px; color: var(--muted); }
.item .badge { margin-left: auto; }
.main { padding: 16px; overflow: auto; }
video { width: 100%; max-height: 58vh; background: #000; border-radius: 8px; display: block; }
.empty { padding: 40px; border: 1px dashed var(--el-border-color); border-radius: 8px; text-align: center; color: var(--muted); }
.empty-all { padding: 60px; text-align: center; color: var(--muted); }
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 12px 0; }
.saved { color: var(--el-color-success); font-size: 12px; }
.meta { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; margin: 12px 0; color: var(--muted); font-size: 13px; }
.meta b { color: var(--el-text-color-primary); font-weight: 500; word-break: break-all; }
.speech { margin: 12px 0; padding: 8px 10px; border: 1px solid var(--el-border-color-light); border-radius: 8px; background: #fff; max-height: 240px; overflow: auto; }
.sl { padding: 2px 4px; border-radius: 4px; cursor: pointer; }
.sl:hover { background: var(--el-color-primary-light-9); }
.st { color: var(--muted); font-size: 12px; margin-right: 8px; font-variant-numeric: tabular-nums; }
.curve { max-width: 100%; border-radius: 6px; background: #fff; }
.keys { font-size: 12px; }
@media (max-width: 800px) {
  .body { grid-template-columns: 1fr; }
  .list { max-height: 200px; }
  .keys { display: none; }
}
.ai { font-size: 11px; color: var(--el-text-color-secondary); margin-left: auto; margin-right: 4px; }
.ai.hot { color: var(--el-color-success); font-weight: 600; }
.aibox { margin: 10px 0; padding: 8px 12px; border-left: 3px solid var(--el-color-primary-light-5); background: var(--el-fill-color-light); display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.aibox .el-tag { margin-right: 6px; }
.adopt { margin: 8px 0 0; padding: 6px 10px; border-radius: 6px; background: var(--el-color-primary-light-9); font-size: 13px; display: flex; gap: 10px; align-items: center; }
.reasons { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 8px; }
.rtag kbd { font-size: 10px; opacity: .55; margin-right: 4px; font-family: inherit; }
.taboo-bar { margin: 8px 0 0; padding: 6px 10px; border-radius: 6px; background: var(--el-color-danger-light-9); color: var(--el-color-danger); font-size: 13px; }
.taboo-mark { font-size: 11px; color: #fff; background: var(--el-color-danger); border-radius: 3px; padding: 0 3px; margin-left: auto; }
.taboo-mark + .ai { margin-left: 4px; }
mark.tb { background: var(--el-color-danger-light-7); color: var(--el-color-danger); border-radius: 2px; }
</style>
