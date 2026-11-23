<template>
  <div class="research-page">
    <header class="hero">
      <div>
        <p class="eyebrow">RESEARCH REVIEW</p>
        <h1>近期样本人工复核</h1>
        <p class="subtitle">逐段确认声音、画面和剪切关系，结果保存在浏览器，可随时导出回写研究账本。</p>
      </div>
      <div class="hero-actions">
        <label class="button secondary">导入队列<input type="file" accept="application/json" @change="importQueue" /></label>
        <button class="button primary" @click="exportQueue">导出复核结果</button>
      </div>
    </header>

    <div class="pause-banner"><strong>人工复核已暂停</strong><span>当前仅保留队列查看和结果导出，不会产生新的人工结论。恢复后可继续使用现有队列。</span></div>

    <section class="stats">
      <div class="stat"><span>总区间</span><strong>{{ items.length }}</strong></div>
      <div class="stat critical"><span>最高优先级</span><strong>{{ countBy('critical') }}</strong></div>
      <div class="stat high"><span>高优先级</span><strong>{{ countBy('high') }}</strong></div>
      <div class="stat done"><span>已完成</span><strong>{{ completed }}</strong><small>/ {{ items.length }}</small></div>
      <div class="progress"><div :style="{ width: `${progress}%` }"></div></div>
    </section>

    <section class="workspace">
      <aside class="queue-panel">
        <div class="filters">
          <input v-model="query" placeholder="搜索视频或文字" />
          <select v-model="videoFilter"><option value="all">全部视频</option><option v-for="id in videoIds" :key="id" :value="id">{{ id }}</option></select>
          <select v-model="priorityFilter"><option value="all">全部优先级</option><option value="critical">Critical</option><option value="high">High</option></select>
          <select v-model="statusFilter"><option value="all">全部状态</option><option value="pending">待复核</option><option value="completed">已完成</option></select>
        </div>
        <div class="queue-list">
          <button v-for="(item, index) in filteredItems" :key="item.review_id" :class="['queue-item', { active: selected?.review_id === item.review_id }]" @click="selectItem(item)">
            <span class="queue-top"><b>{{ formatTime(item.time_range[0]) }}–{{ formatTime(item.time_range[1]) }}</b><em :class="item.priority">{{ item.priority }}</em></span>
            <span class="queue-video">{{ item.video_id }}</span>
            <span class="queue-text">{{ item.base_text || '无文本' }} <i>↔</i> {{ item.small_text || '无对应' }}</span>
            <span class="queue-bottom"><span>{{ item.reason === 'no_overlap' ? '无时间重叠' : '文本差异' }}</span><span :class="item.review_status === 'completed' ? 'ok' : ''">{{ item.review_status === 'completed' ? '已完成' : '待复核' }}</span></span>
          </button>
          <div v-if="!filteredItems.length" class="empty">没有符合条件的区间</div>
        </div>
      </aside>

      <main v-if="selected" class="detail-panel">
        <div class="detail-head">
          <div><span class="pill" :class="selected.priority">{{ selected.priority }}</span><span class="detail-id">{{ selected.review_id }}</span><h2>{{ selected.video_id }} <small>{{ formatTime(selected.time_range[0]) }}–{{ formatTime(selected.time_range[1]) }}</small></h2></div>
          <div class="detail-actions"><button class="icon-button" title="上一条" @click="move(-1)">←</button><button class="icon-button" title="下一条" @click="move(1)">→</button><button class="button primary" disabled title="人工复核已暂停">保存此段</button></div>
        </div>

        <div class="media-grid">
          <div class="video-box"><video v-if="videoUrl" :src="videoUrl" controls preload="metadata"></video><div v-else class="video-empty"><span>▶</span><b>未加载代理视频</b><label class="button secondary">选择视频文件<input type="file" accept="video/*" @change="loadVideo" /></label><small>可选：只在当前浏览器中临时播放，不上传文件。</small></div></div>
          <div class="evidence-box"><div class="section-label">机器证据 · 仅供定位</div><div class="range">目标 {{ formatTime(selected.time_range[0]) }} – {{ formatTime(selected.time_range[1]) }}<br /><span>上下文 {{ formatTime(selected.context_range[0]) }} – {{ formatTime(selected.context_range[1]) }}</span></div><div class="asr"><div><label>base</label><p>{{ selected.base_text || '无识别文本' }}</p></div><div><label>small</label><p>{{ selected.small_text || '无对应片段' }}</p></div></div><p class="warning">ASR 不自动代表最终台词，抽帧不自动代表画面观察。</p></div>
        </div>

        <div class="review-grid">
          <section class="form-section"><div class="section-title"><span>01</span><div><h3>听音复核</h3><small>确认实际说了什么，以及是否包含非语音声音。</small></div></div><textarea v-model="selected.audio_review.verified_text" placeholder="输入听到的原话；不确定时保留 [听不清]。"></textarea><div class="choice-row"><label>声音类型</label><select v-model="selected.audio_review.speech_or_non_speech"><option :value="null">未判断</option><option value="speech">语音</option><option value="laughter">笑声</option><option value="pause">停顿/吸气</option><option value="music">BGM/音乐</option><option value="effect">音效</option><option value="mixed">混合</option></select><label>置信度</label><select v-model="selected.audio_review.confidence"><option :value="null">未判断</option><option value="high">高</option><option value="medium">中</option><option value="low">低</option></select></div></section>
          <section class="form-section"><div class="section-title"><span>02</span><div><h3>画面复核</h3><small>描述连续画面中实际发生的变化。</small></div></div><textarea v-model="selected.visual_review.observed_change" placeholder="例如：人物近景切到弹幕，随后回到人物反应；看不清就写待核实。"></textarea></section>
          <section class="form-section"><div class="section-title"><span>03</span><div><h3>剪切复核</h3><small>只记录可见事实，不推断源素材删除长度。</small></div></div><div class="choice-row"><label>存在画面切换</label><select v-model="selected.cut_review.cut_present"><option :value="null">未判断</option><option :value="true">是</option><option :value="false">否</option><option value="unknown">待核实</option></select><label>切换类型</label><select v-model="selected.cut_review.cut_type"><option :value="null">未判断</option><option value="hard_cut">硬切</option><option value="jump_cut">跳切/连续性突变</option><option value="reaction">人物/弹幕/对象切换</option><option value="insert">插入/贴图</option><option value="reframe">放大/重新构图</option><option value="bridge_unknown">声画关系待核实</option></select></div></section>
        </div>
        <div class="footer-actions"><button class="button secondary" disabled>清空本段填写</button><span class="save-note">人工复核已暂停</span><button class="button primary" disabled>保存并标记完成</button></div>
      </main>
      <main v-else class="blank"><h2>导入复核队列开始</h2><p>选择 `audition_review_batches.json`，或使用左上角导入按钮。</p></main>
    </section>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const STORAGE = 'kiya-research-review-queue'
const query = ref('')
const videoFilter = ref('all')
const priorityFilter = ref('all')
const statusFilter = ref('all')
const items = ref([])
const selected = ref(null)
const videoUrl = ref('')
const saveMessage = ref('')

const seed = [{ review_id: 'demo-audition-001', video_id: '导入后显示实际视频', time_range: [0, 1], context_range: [0, 3], priority: 'critical', reason: 'no_overlap', base_text: '请导入 audition_review_batches.json', small_text: '然后开始复核', review_status: 'pending', audio_review: { status: 'pending', verified_text: '', speech_or_non_speech: null, confidence: null }, visual_review: { status: 'pending', observed_change: '' }, cut_review: { status: 'pending', cut_present: null, cut_type: null } }]

const videoIds = computed(() => [...new Set(items.value.map((item) => item.video_id))])
const filteredItems = computed(() => items.value.filter((item) => {
  const haystack = `${item.video_id} ${item.base_text || ''} ${item.small_text || ''}`.toLowerCase()
  return (!query.value || haystack.includes(query.value.toLowerCase())) && (videoFilter.value === 'all' || item.video_id === videoFilter.value) && (priorityFilter.value === 'all' || item.priority === priorityFilter.value) && (statusFilter.value === 'all' || (item.review_status || 'pending') === statusFilter.value)
}))
const completed = computed(() => items.value.filter((item) => item.review_status === 'completed').length)
const progress = computed(() => items.value.length ? Math.round(completed.value / items.value.length * 100) : 0)
function countBy(value) { return items.value.filter((item) => item.priority === value).length }
function formatTime(seconds) { const s = Math.max(0, Number(seconds) || 0); return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}` }
function selectItem(item) { selected.value = item; saveMessage.value = '' }
function move(delta) { const index = filteredItems.value.findIndex((item) => item.review_id === selected.value?.review_id); selectItem(filteredItems.value[Math.max(0, Math.min(filteredItems.value.length - 1, index + delta))] || filteredItems.value[0]) }
function saveCurrent() { if (!selected.value) return; localStorage.setItem(STORAGE, JSON.stringify(items.value)); saveMessage.value = '已保存到本浏览器'; setTimeout(() => { saveMessage.value = '' }, 1800) }
function completeCurrent() { if (!selected.value) return; selected.value.review_status = 'completed'; selected.value.audio_review.status = 'verified'; selected.value.visual_review.status = 'verified'; selected.value.cut_review.status = 'verified'; saveCurrent() }
function resetCurrent() { if (!selected.value) return; selected.value.audio_review = { status: 'pending', verified_text: '', speech_or_non_speech: null, confidence: null }; selected.value.visual_review = { status: 'pending', observed_change: '' }; selected.value.cut_review = { status: 'pending', cut_present: null, cut_type: null }; selected.value.review_status = 'pending'; saveCurrent() }
function importQueue(event) { const file = event.target.files?.[0]; if (!file) return; const reader = new FileReader(); reader.onload = () => { try { const data = JSON.parse(reader.result); items.value = (data.items || []).map(normalize); selected.value = items.value[0] || null; saveCurrent() } catch { window.alert('JSON 文件无法读取，请选择 audition_review_batches.json') } }; reader.readAsText(file) }
function normalize(item) { return { ...item, review_status: item.review_status || 'pending', audio_review: { status: 'pending', verified_text: '', speech_or_non_speech: null, confidence: null, ...(item.audio_review || {}) }, visual_review: { status: 'pending', observed_change: '', ...(item.visual_review || {}) }, cut_review: { status: 'pending', cut_present: null, cut_type: null, ...(item.cut_review || {}) } } }
function exportQueue() { const blob = new Blob([JSON.stringify({ status: 'local_review_export', exported_at: new Date().toISOString(), items: items.value }, null, 2)], { type: 'application/json' }); const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = 'audition_review_results.json'; link.click(); URL.revokeObjectURL(link.href) }
function loadVideo(event) { if (videoUrl.value) URL.revokeObjectURL(videoUrl.value); videoUrl.value = URL.createObjectURL(event.target.files[0]) }
onMounted(() => { try { const stored = JSON.parse(localStorage.getItem(STORAGE) || 'null'); items.value = stored?.length ? stored.map(normalize) : seed.map(normalize) } catch { items.value = seed.map(normalize) } selected.value = items.value[0] })
onBeforeUnmount(() => { if (videoUrl.value) URL.revokeObjectURL(videoUrl.value) })
watch(items, () => { if (selected.value) saveMessage.value = '有未保存修改' }, { deep: true })
</script>

<style scoped>
.research-page { min-height: 100%; padding: 28px 32px 40px; color: #20252d; background: #f5f7f9; }
.hero { display: flex; justify-content: space-between; align-items: end; gap: 24px; max-width: 1440px; margin: 0 auto 22px; }
.eyebrow { margin: 0 0 7px; color: #16766d; font-size: 11px; font-weight: 800; letter-spacing: 1.4px; }
h1 { margin: 0; font-size: 28px; letter-spacing: 0; } .subtitle { margin: 8px 0 0; color: #6f7885; font-size: 13px; }
.pause-banner { display: flex; gap: 10px; align-items: center; max-width: 1400px; margin: 0 auto 14px; padding: 10px 14px; border: 1px solid #ead8a8; border-radius: 6px; background: #fff9e8; color: #805f20; font-size: 12px; }.pause-banner strong { color: #76551a; }.hero-actions, .detail-actions { display: flex; gap: 8px; align-items: center; }.button { border: 1px solid #d6dce3; background: white; color: #38414e; border-radius: 6px; padding: 9px 13px; cursor: pointer; font-size: 13px; }.button.primary { background: #16766d; border-color: #16766d; color: white; }.button.secondary:hover, .icon-button:hover { border-color: #16766d; color: #16766d; }.button input { display: none; }
.stats { display: flex; align-items: center; gap: 18px; max-width: 1440px; margin: 0 auto 18px; padding: 14px 18px; background: white; border: 1px solid #e2e7ec; border-radius: 8px; }.stat { display: flex; align-items: baseline; gap: 8px; min-width: 108px; color: #818a96; font-size: 12px; }.stat strong { color: #20252d; font-size: 22px; }.stat small { color: #9da5af; }.stat.critical strong { color: #b54b46; }.stat.high strong { color: #b77927; }.stat.done strong { color: #16766d; }.progress { height: 5px; flex: 1; background: #edf0f2; border-radius: 4px; overflow: hidden; }.progress div { height: 100%; background: #16766d; transition: width .2s; }
.workspace { display: grid; grid-template-columns: 360px minmax(0, 1fr); gap: 16px; max-width: 1440px; min-height: 690px; margin: 0 auto; }.queue-panel, .detail-panel, .blank { background: white; border: 1px solid #e2e7ec; border-radius: 8px; }.queue-panel { overflow: hidden; }.filters { display: grid; grid-template-columns: 1fr 1fr; gap: 7px; padding: 12px; border-bottom: 1px solid #e8ebef; }.filters input, .filters select, .choice-row select { min-width: 0; border: 1px solid #d9dfe6; border-radius: 5px; padding: 8px; color: #4b5663; background: white; font-size: 12px; }.filters input { grid-column: 1 / -1; }.queue-list { max-height: 690px; overflow: auto; }.queue-item { display: block; width: 100%; border: 0; border-bottom: 1px solid #edf0f2; background: white; padding: 12px 14px; text-align: left; cursor: pointer; }.queue-item:hover { background: #f8fbfa; }.queue-item.active { box-shadow: inset 3px 0 #16766d; background: #f0f8f6; }.queue-top, .queue-bottom { display: flex; justify-content: space-between; align-items: center; }.queue-top b { font-size: 13px; }.queue-top em, .pill { padding: 3px 6px; border-radius: 4px; font-size: 10px; font-style: normal; font-weight: 800; text-transform: uppercase; }.critical { color: #a74440; background: #fff0ef; }.high { color: #98641c; background: #fff7e9; }.queue-video, .queue-bottom { display: block; margin-top: 6px; color: #89929e; font-size: 11px; }.queue-text { display: block; overflow: hidden; margin-top: 7px; color: #48525e; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }.queue-text i { color: #b4bdc7; font-style: normal; }.queue-bottom { display: flex; }.queue-bottom .ok { color: #16766d; }.empty, .blank { padding: 40px; color: #8b949f; text-align: center; }
.detail-panel { padding: 22px; overflow: hidden; }.detail-head { display: flex; justify-content: space-between; align-items: start; gap: 16px; margin-bottom: 18px; }.detail-id { margin-left: 8px; color: #9aa2ad; font-size: 11px; }.detail-head h2 { margin: 9px 0 0; font-size: 19px; }.detail-head h2 small { margin-left: 8px; color: #7c8692; font-size: 12px; font-weight: 400; }.icon-button { width: 34px; height: 34px; border: 1px solid #d6dce3; border-radius: 5px; background: white; color: #586372; cursor: pointer; font-size: 16px; }.media-grid { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(260px, .65fr); gap: 12px; }.video-box { min-height: 245px; background: #182127; border-radius: 6px; overflow: hidden; }.video-box video { display: block; width: 100%; height: 100%; min-height: 245px; object-fit: contain; }.video-empty { display: flex; height: 245px; flex-direction: column; align-items: center; justify-content: center; gap: 10px; color: #b3bec7; }.video-empty span { color: #5cb7a8; font-size: 30px; }.video-empty b { color: white; font-size: 13px; }.video-empty small { font-size: 11px; }.evidence-box { padding: 16px; border: 1px solid #e1e6eb; border-radius: 6px; background: #fbfcfd; }.section-label { color: #87919e; font-size: 11px; font-weight: 800; letter-spacing: .5px; }.range { margin-top: 12px; color: #26313b; font-size: 13px; line-height: 1.8; }.range span { color: #8b949e; font-size: 11px; }.asr { display: grid; gap: 8px; margin-top: 14px; }.asr div { padding: 9px; border-left: 3px solid #c4d9d5; background: white; }.asr label { color: #8a949e; font-size: 10px; text-transform: uppercase; }.asr p { margin: 4px 0 0; font-size: 12px; line-height: 1.5; }.warning { margin: 14px 0 0; color: #a87126; font-size: 11px; line-height: 1.5; }
.review-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 14px; }.form-section { padding: 15px; border: 1px solid #e1e6eb; border-radius: 6px; }.section-title { display: flex; gap: 9px; align-items: flex-start; margin-bottom: 13px; }.section-title > span { color: #16766d; font-size: 11px; font-weight: 800; }.section-title h3 { margin: 0; font-size: 14px; }.section-title small { display: block; margin-top: 4px; color: #89929e; font-size: 11px; line-height: 1.4; }.form-section textarea { box-sizing: border-box; width: 100%; min-height: 110px; resize: vertical; border: 1px solid #d9dfe6; border-radius: 5px; padding: 9px; color: #37414d; font: inherit; font-size: 12px; }.choice-row { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; margin-top: 10px; }.choice-row label { color: #7e8894; font-size: 11px; }.choice-row select { flex: 1; }.footer-actions { display: flex; justify-content: flex-end; align-items: center; gap: 10px; margin-top: 16px; }.save-note { margin-right: auto; color: #b27a26; font-size: 12px; }
@media (max-width: 1050px) { .workspace { grid-template-columns: 300px minmax(0, 1fr); }.review-grid { grid-template-columns: 1fr; }.media-grid { grid-template-columns: 1fr; } }
@media (max-width: 760px) { .research-page { padding: 18px 12px; }.hero { display: block; }.hero-actions { margin-top: 15px; }.stats { flex-wrap: wrap; }.progress { flex-basis: 100%; }.workspace { display: block; }.queue-panel { margin-bottom: 12px; }.queue-list { max-height: 310px; }.detail-head { display: block; }.detail-actions { margin-top: 12px; } }
</style>
