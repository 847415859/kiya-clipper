<template>
  <div class="page" v-loading="loading">
    <div class="toolbar">
      <el-button type="primary" @click="learn(false)">更新精排记忆</el-button>
      <el-button @click="learn(true)">只用本地数据更新</el-button>
      <span class="muted" v-if="mem">版本 {{ mem.version }} · {{ mem.generated_at }} · {{ (mem.sessions || []).length }} 场</span>
      <span class="spacer" />
      <span class="muted">下播后自动更新（KIYA_RERANK=1 时）；这里删掉 / 排除的，下次精排立刻不用</span>
    </div>

    <el-card v-if="jobId" shadow="never" class="card">
      <JobLog :job-id="jobId" @done="load" />
    </el-card>

    <el-alert v-if="!mem && !loading" type="info" :closable="false" show-icon class="card"
              title="还没有精排记忆：点“更新精排记忆”。至少 2 场转写后才会发现梗，标注过的场次会变成审核例子。" />
    <el-alert v-for="e in (mem && mem.errors) || []" :key="e" :title="e" type="warning" :closable="false" show-icon class="card" />

    <template v-if="mem || dec">
      <el-card shadow="never" class="card">
        <template #header>
          <b>梗 / 口头禅</b>
          <span class="muted">（转写和弹幕里在 2 场以上出现的说法自动加入；点 × 删掉，以后不会再加回来）</span>
        </template>
        <div class="tags">
          <el-tooltip v-for="x in lore" :key="x.text" placement="top"
                      :content="x.status === 'manual' ? `手动添加${x.note ? '：' + x.note : ''}` : `${x.sessions} 场；主播说 ${x.speech} 次，弹幕 ${x.danmaku} 条${x.in_titles ? '；投稿标题里也有' : ''}；${x.first_added || ''} 加入`">
            <el-tag :type="x.status === 'manual' ? 'success' : ''" closable class="pick" @close="dropLore(x)" @click="openPromote(x)">{{ x.text }}</el-tag>
          </el-tooltip>
          <span v-if="!lore.length" class="muted">暂无</span>
        </div>
        <p class="muted hint">点一个梗可以“转正”：确认它是真梗后并入主播档案，之后热词识别、字幕高亮、投稿标签、提示词都会固定用它。</p>
        <div v-if="promoted.length" class="promoted">
          <b>已转正 {{ promoted.length }} 个</b>
          <el-tag v-for="x in promoted" :key="x.text" type="warning" closable @close="act({ action: 'unpromote', text: x.text })">
            {{ x.text }}<span class="kind">{{ kindName[x.kind] }}</span>
          </el-tag>
        </div>
        <el-alert v-if="dec.hotwords_dirty" type="info" :closable="false" show-icon class="inline-alert"
                  title="转正的梗要同步到热词表，下一场转写才认得更准">
          <el-button size="small" type="primary" @click="syncHotwords">同步热词表</el-button>
        </el-alert>
        <div class="row">
          <el-input v-model="newLore.text" placeholder="手动加一个梗（20 字内）" style="width: 200px" maxlength="20" />
          <el-input v-model="newLore.note" placeholder="说明（选填，如：粉丝给她起的外号）" style="width: 280px" maxlength="60" />
          <el-button :disabled="!newLore.text.trim()" @click="addLore">添加</el-button>
        </div>
        <el-collapse v-if="(dec.rejected_lore || []).length">
          <el-collapse-item :title="`已删掉的 ${dec.rejected_lore.length} 个（点击恢复）`">
            <el-tag v-for="t in dec.rejected_lore" :key="t" type="info" class="rej" @click="act({ action: 'unreject', text: t })">{{ t }}</el-tag>
          </el-collapse-item>
        </el-collapse>
      </el-card>

      <el-card shadow="never" class="card">
        <template #header>
          <b>禁区话题</b>
          <span class="muted">（主播不想发 / 敏感的话题。切片里主播的原话命中这些词时，精排直接判不切、排到最后，审核页标红）</span>
        </template>
        <div class="tags">
          <el-tooltip v-for="x in taboo" :key="x.word" placement="top" :content="x.note || '没有说明'">
            <el-tag type="danger" closable @close="setTaboo({ action: 'removeTaboo', word: x.word })">{{ x.word }}</el-tag>
          </el-tooltip>
          <span v-if="!taboo.length" class="muted">还没有设置</span>
        </div>
        <div class="row">
          <el-input v-model="newTaboo.word" placeholder="禁区词（至少 2 个字）" style="width: 200px" maxlength="20" />
          <el-input v-model="newTaboo.note" placeholder="说明（选填，如：主播说过不想提）" style="width: 280px" maxlength="60" />
          <el-button :disabled="newTaboo.word.trim().length < 2" @click="addTaboo">添加</el-button>
        </div>
        <p class="muted hint">加 / 删禁区词后，已精排的场次会自动按新规则重排（不重新请求大模型，不花钱）。词要具体，太短的词会误伤。</p>
        <div v-if="vetoes.length">
          <b class="sub">审核时标过“主播不想发”的片段</b>
          <div v-for="e in vetoes" :key="e.id" class="veto">
            <el-button link type="primary" @click="$router.push(`/sessions/${e.session}/review`)">{{ e.id }}</el-button>
            {{ e.what_happened || e.speech || '—' }}
            <span class="muted">{{ e.note }}</span>
          </div>
          <p class="muted hint">这些会作为“明确不要发”的例子给大模型看；想彻底挡住同类话题，把关键词加到上面的禁区里。</p>
        </div>
      </el-card>

      <el-card shadow="never" class="card">
        <template #header>
          <b>审核习惯</b>
          <span class="muted">（审核页的一键原因、耗时和边界反馈；盲审的标注才用来评测 / 校准权重）</span>
        </template>
        <template v-if="review && review.marked">
          <div class="stats">
            <span>已标 {{ review.marked }} 条</span>
            <span v-if="review.avg_seconds">平均 {{ review.avg_seconds }} 秒/条（中位数 {{ review.median_seconds }}）</span>
            <span>盲审 {{ (review.modes || {}).blind || 0 }} · 快审 {{ (review.modes || {}).fast || 0 }} · 采纳 AI {{ (review.modes || {}).confirm || 0 }}</span>
          </div>
          <div class="tags reasons-row">
            <el-tag v-for="(n, k) in review.reasons" :key="k" :type="k.startsWith('start') || k.startsWith('end') ? 'warning' : k === 'gem' ? 'success' : 'info'">
              {{ reasonName[k] || k }} {{ n }}
            </el-tag>
            <span v-if="!Object.keys(review.reasons || {}).length" class="muted">还没有人点过原因：审核时在 Y/N 下面点一下，或按 1–0。</span>
          </div>
          <div class="stats">
            <b>边界反馈</b>
            <span v-for="(n, k) in review.boundary.votes" :key="k">{{ reasonName[k] }} {{ n }}</span>
          </div>
          <el-alert v-for="sg in review.boundary.suggestions" :key="sg" :title="sg" type="warning" :closable="false" show-icon class="inline-alert" />
          <p v-if="!review.boundary.suggestions.length" class="muted hint">
            边界反馈攒够 {{ review.boundary.needed }} 票且明显偏向一边时，这里会建议调粗筛的 clip_before / clip_after_seconds（只提示，不会自动改）。
          </p>
        </template>
        <p v-else class="muted">还没有标注数据。</p>
      </el-card>

      <el-card shadow="never" class="card">
        <template #header>
          <b>剪辑口味</b>
          <span class="muted">（对比 AI 初稿和你改过的终稿：删了什么、改了什么字、裁剪挪了多少；攒够才会影响下一次 AI 初稿）</span>
        </template>
        <template v-if="taste">
          <el-alert :title="taste.status" :type="taste.active ? 'success' : 'info'" :closable="false" show-icon class="inline-alert" />
          <el-table v-if="taste.n" :data="tasteRows" size="small" class="taste-table">
            <el-table-column prop="name" label="效果" width="80" />
            <el-table-column prop="ai" label="AI 排了" width="90" />
            <el-table-column prop="removed" label="被删掉" width="90" />
            <el-table-column label="删除率" width="90"><template #default="{ row }">{{ row.ai ? Math.round((row.removed / row.ai) * 100) + '%' : '—' }}</template></el-table-column>
            <el-table-column prop="added" label="人另外加" width="100" />
          </el-table>
          <div v-if="taste.n" class="stats">
            <span>每条 AI 平均 {{ taste.avg_ai_items }} 个效果，改完 {{ taste.avg_human_items }} 个</span>
            <span v-if="taste.trim_shift">裁剪：开头平均{{ taste.trim_shift.start >= 0 ? '后挪' : '前挪' }} {{ Math.abs(taste.trim_shift.start) }} 秒，结尾{{ taste.trim_shift.end >= 0 ? '后挪' : '前挪' }} {{ Math.abs(taste.trim_shift.end) }} 秒</span>
            <span>完全没改的 {{ taste.unchanged }} 条</span>
          </div>
          <div v-if="taste.text_changes && taste.text_changes.length" class="sub-list">
            <b class="sub">花字被改过</b>
            <div v-for="(c, i) in taste.text_changes" :key="i">“{{ c.from }}” → “{{ c.to }}” <span class="muted">{{ c.session }}#{{ c.rank }}</span></div>
          </div>
          <div v-if="taste.removed_examples && taste.removed_examples.length" class="sub-list">
            <b class="sub">被删掉的</b>
            <div v-for="(c, i) in taste.removed_examples" :key="i">{{ typeName[c.type] }}：{{ c.text }} <span class="muted">{{ c.session }}#{{ c.rank }}</span></div>
          </div>
        </template>
        <p v-else class="muted">还没有数据：在成片编辑页用“AI 初稿”生成后手动改一改并保存（或渲染 1080p 确认）就会记录。点上面的“更新精排记忆”刷新这里。</p>
      </el-card>

      <el-card shadow="never" class="card">
        <template #header>
          <b>审核例子</b>
          <span class="muted">（来自审核页的 Y/N 和备注；精排某一场时只用其他场的，每次最多各 3 个、有备注的优先。写备注最有用）</span>
        </template>
        <el-table :data="examples" size="small" max-height="360" empty-text="还没有标注">
          <el-table-column label="片段" width="150">
            <template #default="{ row }">
              <el-button link type="primary" @click="$router.push(`/sessions/${row.session}/review`)">{{ row.id }}</el-button>
            </template>
          </el-table-column>
          <el-table-column label="标注" width="70">
            <template #default="{ row }"><el-tag size="small" :type="row.mark === 'Y' ? 'success' : 'danger'">{{ row.mark }}</el-tag></template>
          </el-table-column>
          <el-table-column label="内容" min-width="260" show-overflow-tooltip>
            <template #default="{ row }">{{ row.what_happened || row.speech || '—' }}</template>
          </el-table-column>
          <el-table-column label="原因" min-width="150" show-overflow-tooltip>
            <template #default="{ row }">{{ (row.reasons || []).map((r) => reasonName[r] || r).join('、') }}</template>
          </el-table-column>
          <el-table-column prop="note" label="备注" min-width="180" show-overflow-tooltip />
          <el-table-column label="使用" width="70">
            <template #default="{ row }">
              <el-switch :model-value="!excluded.has(row.id)" size="small"
                         @change="(v) => act({ action: v ? 'include' : 'exclude', id: row.id })" />
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <div class="two">
        <el-card shadow="never" class="card">
          <template #header><b>她自己的投稿</b> <span class="muted">（最近 {{ streamerUploads.length }} 条；精排参考她觉得什么值得发、最近聊什么、标题风格）</span></template>
          <el-table :data="streamerUploads" size="small" max-height="320"
                    :empty-text="mem ? '没拉到，看页面顶部的黄色提示' : '还没更新过：点页面顶部的“更新精排记忆”'">
            <el-table-column label="标题" min-width="240" show-overflow-tooltip>
              <template #default="{ row }"><a :href="`https://www.bilibili.com/video/${row.bvid}`" target="_blank" rel="noopener">{{ row.title }}</a></template>
            </el-table-column>
            <el-table-column prop="view" label="播放" width="80" />
            <el-table-column label="发布" width="100"><template #default="{ row }">{{ day(row.pubdate) }}</template></el-table-column>
          </el-table>
        </el-card>

        <el-card shadow="never" class="card">
          <template #header><b>切片号投稿</b></template>
          <div class="row">
            <span class="muted">切片号 UID</span>
            <el-input v-model="myUid" placeholder="空间网址里的数字" style="width: 180px" maxlength="20" />
            <el-button @click="act({ action: 'myUid', uid: myUid.trim() })">保存</el-button>
            <span class="muted">在成片编辑页填 BV 号绑定到片段</span>
          </div>
          <el-table :data="myUploads" size="small" max-height="260" empty-text="还没有（填 UID 后点“更新精排记忆”）">
            <el-table-column label="标题" min-width="200" show-overflow-tooltip>
              <template #default="{ row }"><a :href="`https://www.bilibili.com/video/${row.bvid}`" target="_blank" rel="noopener">{{ row.title }}</a></template>
            </el-table-column>
            <el-table-column prop="view" label="播放" width="70" />
            <el-table-column prop="like" label="点赞" width="60" />
            <el-table-column label="片段" width="150">
              <template #default="{ row }">
                <span v-if="row.session">{{ row.session }}#{{ row.rank }} <span class="muted">{{ row.type }}</span></span>
                <span v-else class="muted">未绑定</span>
              </template>
            </el-table-column>
          </el-table>
          <div v-if="typeStats.length" class="stats">
            <b>按类型</b>
            <span v-for="t in typeStats" :key="t.type">{{ t.type }} {{ t.n }} 条，播放中位数 {{ t.median_view }}（{{ t.relative }}×）</span>
          </div>
          <p v-else class="muted">绑定满 5 条后按类型汇总表现，提示词里会告诉大模型哪类片段观众更买账。</p>
        </el-card>
      </div>

      <el-card shadow="never" class="card">
        <template #header><b>排序权重</b> <span class="muted">（综合分 = 大模型分 × llm + 粗筛名次 × screen；判为不值得切的 × not_funny）</span></template>
        <div class="weights">
          <div><span class="muted">正在用</span> <code>{{ fmtW(w.active) }}</code>
            <el-tag v-if="dec.weights" size="small" type="success">后台确认的（{{ (dec.weights_applied_at || '').slice(0, 10) }}）</el-tag>
            <el-tag v-else size="small" type="info">配置文件默认</el-tag>
          </div>
          <div>{{ w.status || '还没有计算' }}
            <span v-if="w.active_hits != null" class="muted">（{{ w.sessions }} 场，前 {{ w.k }} 名命中：现在 {{ w.active_hits }}，留一场验证 {{ w.loo_hits }}，标注为有趣共 {{ w.total_yes }}）</span>
          </div>
          <div v-if="w.proposed"><span class="muted">建议</span> <code>{{ fmtW(w.proposed) }}</code>
            <el-button type="primary" size="small" @click="applyWeights">应用并重排已精排的场次</el-button>
          </div>
          <div v-if="dec.weights"><el-button size="small" link @click="act({ action: 'resetWeights' })">恢复配置文件默认</el-button></div>
        </div>
      </el-card>
    </template>

    <el-dialog v-model="promoteDlg.show" title="梗转正" width="420px">
      <p>把“<b>{{ promoteDlg.text }}</b>”并入主播档案：</p>
      <el-radio-group v-model="promoteDlg.kind">
        <el-radio value="meme">梗</el-radio>
        <el-radio value="catchphrase">口头禅</el-radio>
        <el-radio value="topic">常聊话题</el-radio>
      </el-radio-group>
      <el-input v-model="promoteDlg.note" placeholder="说明（选填）" maxlength="60" class="dlg-note" />
      <template #footer>
        <el-button @click="promoteDlg.show = false">取消</el-button>
        <el-button type="primary" @click="doPromote">转正</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import JobLog from '../components/JobLog.vue'

const loading = ref(true)
const mem = ref(null)
const dec = ref({})
const jobId = ref(null)
const myUid = ref('')
const newLore = reactive({ text: '', note: '' })
const newTaboo = reactive({ word: '', note: '' })
const promoteDlg = reactive({ show: false, text: '', kind: 'meme', note: '' })
const kindName = { meme: '梗', catchphrase: '口头禅', topic: '话题' }
const typeName = { text: '花字', zoom: '推镜', sticker: '贴图' }
const reasonName = {
  start_early: '开头太早', start_late: '开头太晚', end_early: '结尾太早', end_late: '结尾太晚',
  not_funny: '不好笑', need_context: '要前情才懂', read_sc: '只是念SC', repeat_meme: '重复的梗', taboo: '主播不想发', gem: '特别好笑',
}

const rejected = computed(() => new Set((dec.value.rejected_lore || []).map((t) => String(t).toLowerCase())))
const excluded = computed(() => new Set(dec.value.excluded_examples || []))
const lore = computed(() => {
  const manual = (dec.value.manual_lore || []).map((m) => ({ ...m, status: 'manual' }))
  const auto = ((mem.value && mem.value.lore) || []).filter((x) => x.status !== 'manual' && !rejected.value.has(x.text.toLowerCase()))
  const seen = new Set(manual.map((m) => m.text.toLowerCase()))
  return [...manual, ...auto.filter((x) => !seen.has(x.text.toLowerCase()))]
})
const examples = computed(() => (mem.value && mem.value.examples) || [])
const streamerUploads = computed(() => (mem.value && mem.value.streamer_uploads) || [])
const myUploads = computed(() => (mem.value && mem.value.my_uploads) || [])
const typeStats = computed(() => Object.entries((mem.value && mem.value.type_stats) || {}).map(([type, v]) => ({ type, ...v })))
const promoted = computed(() => dec.value.promoted_lore || [])
const taboo = computed(() => (dec.value.taboo || []).map((x) => (typeof x === 'string' ? { word: x } : x)))
const vetoes = computed(() => examples.value.filter((e) => e.veto))
const review = computed(() => (mem.value && mem.value.review) || null)
const taste = computed(() => (mem.value && mem.value.edit_taste) || null)
const tasteRows = computed(() => Object.entries(typeName).map(([k, name]) => ({ name, ...(taste.value.by_type[k] || { ai: 0, removed: 0, added: 0 }) })))
const w = computed(() => (mem.value && mem.value.weights) || { active: dec.value.weights })

const day = (ts) => (ts ? new Date(ts * 1000).toLocaleDateString() : '')
const fmtW = (x) => (x ? `llm ${x.llm} · screen ${x.screen} · not_funny ${x.not_funny ?? 0.6}` : '—')

async function load() {
  loading.value = true
  try {
    const r = await api.memory()
    mem.value = r.memory
    dec.value = r.decisions || {}
    myUid.value = dec.value.my_uid || ''
  } finally {
    loading.value = false
  }
}

async function act(body) {
  dec.value = await api.memoryAction(body)
  ElMessage.success('已保存')
}

function openPromote(x) {
  Object.assign(promoteDlg, { show: true, text: x.text, kind: 'meme', note: x.note || '' })
}

async function doPromote() {
  await act({ action: 'promote', text: promoteDlg.text, kind: promoteDlg.kind, note: promoteDlg.note.trim() })
  promoteDlg.show = false
}

async function syncHotwords() {
  const j = await api.submitJob('HOTWORDS_SYNC', null, {})
  jobId.value = j.id
  await act({ action: 'hotwordsSynced' })
}

// 禁区词改了：已精排的场次按新规则重排（RESCORE 不请求大模型，不花钱）
async function rescoreAll() {
  const list = (await api.sessions()).filter((s) => s.hasRerank)
  for (const s of list) await api.submitJob('RESCORE', s.name, {})
  if (list.length) ElMessage.success(`已提交 ${list.length} 场重排，任务页可看进度`)
}

async function setTaboo(body) {
  await act(body)
  await rescoreAll()
}

async function addTaboo() {
  await setTaboo({ action: 'addTaboo', word: newTaboo.word.trim(), note: newTaboo.note.trim() })
  Object.assign(newTaboo, { word: '', note: '' })
}

function dropLore(x) {
  return x.status === 'manual' ? act({ action: 'removeLore', text: x.text }) : act({ action: 'reject', text: x.text })
}

async function addLore() {
  await act({ action: 'addLore', text: newLore.text.trim(), note: newLore.note.trim() })
  Object.assign(newLore, { text: '', note: '' })
}

async function learn(noFetch) {
  const j = await api.submitJob('LEARN', null, noFetch ? { noFetch: true } : {})
  jobId.value = j.id
}

async function applyWeights() {
  await ElMessageBox.confirm('换成建议的权重，并按新权重重排所有已精排的场次（不重新请求大模型，不花钱）？', '应用新权重', { type: 'warning' })
  await act({ action: 'applyWeights' })
  const list = (await api.sessions()).filter((s) => s.hasRerank)
  for (const s of list) await api.submitJob('RESCORE', s.name, {})
  ElMessage.success(`已提交 ${list.length} 场重排，任务页可看进度`)
}

onMounted(load)
</script>

<style scoped>
.card { margin-bottom: 16px; }
.toolbar, .row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }
.row { margin: 12px 0 4px; }
.spacer { flex: 1; }
.muted { color: var(--el-text-color-secondary); font-size: 13px; }
.tags { display: flex; flex-wrap: wrap; gap: 6px; }
.rej { margin: 0 6px 6px 0; cursor: pointer; }
.pick { cursor: pointer; }
.hint { margin: 8px 0 0; }
.promoted { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 10px; }
.kind { margin-left: 4px; opacity: .6; font-size: 11px; }
.inline-alert { margin-top: 10px; }
.sub { display: block; margin: 12px 0 4px; }
.veto { padding: 2px 0; font-size: 13px; }
.sub-list { margin-top: 8px; font-size: 13px; }
.taste-table { max-width: 520px; margin-top: 10px; }
.reasons-row { margin: 10px 0; }
.dlg-note { margin-top: 12px; }
.two { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 16px; }
.two .card { margin-bottom: 16px; }
.stats { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 10px; font-size: 13px; }
.weights { display: flex; flex-direction: column; gap: 8px; }
code { background: var(--el-fill-color-light); padding: 1px 6px; border-radius: 4px; }
a { color: var(--el-color-primary); text-decoration: none; }
</style>
