<template>
  <section class="transcript-editor">
    <div class="speech-head"><b>原话与字幕校对</b><el-input v-model="query" clearable placeholder="搜索原话或修订文字" aria-label="搜索原话" /></div>
    <p class="hint">点时间定位，试听会在句末暂停。修订只影响当前切片字幕，随顶部“保存”写入；原声和整场转写保持原样。修订句按句展示，不沿用旧逐词高亮，修改后需重新渲染成片。</p>
    <p v-if="loading" class="hint">正在读取素材前后原话…</p>
    <p v-else-if="!speech.available" class="hint">{{ speech.error || '还没有可用转写，先在场次页完成转写。' }}</p>
    <p v-if="asrDoubt" class="doubt">精排提示关键原话可能识别错误，请优先听清人名、否定词、数字和谐音。</p>
    <p v-if="stale.length" class="doubt">有 {{ stale.length }} 条已保存修订未匹配到当前原话（也可能位于本次查询范围之外），请核对。<el-button size="small" link @click="showStale = !showStale">展开</el-button></p>
    <div v-if="showStale" v-for="r in stale" :key="sentenceKey(r)" class="stale-row">
      {{ hms(r.t0) }} · {{ r.original }} → {{ r.text }}
      <el-button size="small" link type="danger" @click="$emit('remove-correction', r)">移除修订</el-button>
    </div>
    <div class="sentences" v-if="speech.available">
      <article v-for="s in matches.slice(0, 120)" :key="sentenceKey(s)" class="sentence"
               :class="{ active: time >= position(s).start && time < position(s).end, outside: !position(s).complete }">
        <div class="sentence-actions">
          <el-button size="small" link :disabled="!intersects(s) || staleVideo" @click="$emit('seek', Math.max(0, position(s).start))">{{ hms(s.t0) }}</el-button>
          <el-tag v-if="!position(s).complete" size="small" type="info">跨素材边界</el-tag>
          <el-button size="small" :disabled="!intersects(s) || staleVideo" @click="$emit('audition', position(s))">试听</el-button>
          <el-button size="small" :disabled="!position(s).complete" @click="$emit('boundary', { edge: 'start', time: position(s).start })">从此句开始</el-button>
          <el-button size="small" :disabled="!position(s).complete" @click="$emit('boundary', { edge: 'end', time: position(s).end })">到此句结束</el-button>
          <el-button v-if="!position(s).complete" size="small" @click="$emit('expand', s)">扩入素材</el-button>
          <el-button size="small" link @click="editing = editing === sentenceKey(s) ? '' : sentenceKey(s)">改字幕</el-button>
        </div>
        <p class="original">{{ s.text }}</p>
        <p v-if="correction(s) && editing !== sentenceKey(s)" class="corrected">字幕修订：{{ correction(s).text || '（内容为空，保存前请填写或恢复原文）' }}</p>
        <div v-if="editing === sentenceKey(s)">
          <el-input type="textarea" :rows="2" :maxlength="1000" show-word-limit aria-label="修订字幕"
                    :model-value="correction(s)?.text ?? s.text" @update:model-value="$emit('correction', { sentence: s, text: $event })" />
          <el-button v-if="correction(s)" size="small" link @click="$emit('remove-correction', s)">恢复原文</el-button>
        </div>
      </article>
      <p v-if="!matches.length" class="hint">当前范围没有匹配的原话。</p>
    </div>
    <p v-if="matches.length > 120 || speech.truncated" class="hint">结果较多，仅显示前 120 句。请搜索关键词或缩小素材范围。</p>
    <p v-if="staleVideo" class="doubt">素材范围已变化，请生成新预览后再定位或试听。</p>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import { hms } from '../api'
import { sentenceKey, sentencePosition } from '../editor-tools'
const props = defineProps({ edit: { type: Object, required: true }, speech: { type: Object, default: () => ({}) },
  time: Number, asrDoubt: Boolean, loading: Boolean, staleVideo: Boolean })
defineEmits(['seek', 'audition', 'boundary', 'expand', 'correction', 'remove-correction'])
const query = ref(''), editing = ref(''), showStale = ref(false)
const overrides = computed(() => props.edit.subtitles.sentence_overrides || [])
const sentences = computed(() => props.speech.sentences || [])
const correction = s => overrides.value.find(r => sentenceKey(r) === sentenceKey(s) && r.original === s.text)
const position = s => sentencePosition(s, props.edit.window)
const intersects = s => position(s).end > 0 && position(s).start < props.edit.window.end - props.edit.window.start
const matches = computed(() => sentences.value.filter(s => `${s.text} ${correction(s)?.text || ''}`.toLowerCase().includes(query.value.trim().toLowerCase())))
const stale = computed(() => overrides.value.filter(r => !sentences.value.some(s => sentenceKey(r) === sentenceKey(s) && r.original === s.text)))
</script>

<style scoped>
.transcript-editor { border: 1px solid var(--el-border-color); padding: 12px; border-radius: 6px; margin-top: 12px; background: #fff; }
.speech-head { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.speech-head .el-input { width: 260px; max-width: 100%; }
.hint { font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.6; }
.doubt { color: var(--el-color-warning-dark-2); font-size: 12px; }
.sentences { max-height: 350px; overflow: auto; }
.sentence { border-top: 1px solid var(--el-border-color-lighter); padding: 9px 6px; }
.sentence.active { background: var(--el-color-primary-light-9); }
.sentence.outside { border-left: 3px solid var(--el-border-color); }
.sentence-actions { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.sentence-actions .el-button + .el-button { margin-left: 0; }
.original, .corrected { margin: 6px 0; line-height: 1.7; overflow-wrap: anywhere; }
.corrected { color: var(--el-color-primary); }
.stale-row { font-size: 12px; padding: 5px 0; overflow-wrap: anywhere; }
</style>
