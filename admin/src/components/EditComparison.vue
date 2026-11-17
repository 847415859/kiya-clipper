<template>
  <el-card shadow="never" class="card">
    <template #header><b>与 AI 原稿对比</b></template>
    <p v-if="!comparison" class="hint">尚无 AI 原稿，可直接手工编辑。</p>
    <template v-else>
      <p class="hint">对比当前编辑与选定版本的 AI 原稿，仅查看差异，不替换当前脚本。</p>
      <p>原稿起止：{{ comparison.trim.before.map(t => t.toFixed(2) + ' 秒').join(' – ') }}（整场）</p>
      <p>当前起止：{{ comparison.trim.after.map(t => t.toFixed(2) + ' 秒').join(' – ') }}（整场）</p>
      <p>新增 {{ count('added') }} · 删除 {{ count('removed') }} · 修改 {{ count('changed') }} · 当前逐句字幕修订 {{ comparison.subtitleChanges }} 条</p>
      <p v-if="comparison.settings.length">设置变化：{{ comparison.settings.join('、') }}</p>
      <details v-if="comparison.changes.length">
        <summary>查看效果差异</summary>
        <div v-for="(change, i) in comparison.changes" :key="i" class="change">
          <b>{{ labels[change.kind] }}</b> · {{ itemName(change.after || change.before) }}
          <p v-if="change.before">原稿 {{ hms(change.before.start) }} · {{ change.before.text || change.before.asset || `放大 ${change.before.scale} 倍` }}</p>
          <p v-if="change.after">当前 {{ hms(change.after.start) }} · {{ change.after.text || change.after.asset || `放大 ${change.after.scale} 倍` }}</p>
          <p v-for="field in change.fields || []" :key="field" class="hint">{{ fieldNames[field] || field }}：{{ change.before[field] ?? '—' }} → {{ change.after[field] ?? '—' }}</p>
          <el-button v-if="change.after" size="small" link @click="$emit('select', change.after)">定位当前效果</el-button>
        </div>
      </details>
    </template>
  </el-card>
</template>
<script setup>
import { computed } from 'vue'
import { hms } from '../api'
import { compareEdits } from '../editor-tools'
const props = defineProps({ edit: { type: Object, required: true }, ai: Object, candidateStart: Number })
defineEmits(['select'])
const labels = { added: '新增', removed: '删除', changed: '修改' }
const fieldNames = { start: '开始时间', duration: '时长', text: '文字', asset: '素材', x: '横向位置', y: '纵向位置', scale: '缩放', size: '字号', color: '颜色', preset: '动画', shake: '震动', rotate: '旋转', stroke: '描边', stroke_width: '描边宽度', font: '字体', ease_in: '进入时间', ease_out: '退出时间', title: '标题', tag: '标签', kind: '类型', style: '样式', meme: '梗', meme_reason: '用梗说明' }
const comparison = computed(() => compareEdits(props.ai, props.edit, props.candidateStart))
const count = kind => comparison.value.changes.filter(c => c.kind === kind).length
const itemName = item => ({ text: '花字', zoom: '运镜', sticker: '贴图', narration: '旁白', fakeui: '伪 UI' })[item.type] || item.type
</script>
<style scoped>
.hint { color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.6; }
p { font-size: 12px; line-height: 1.6; }
summary { cursor: pointer; }
.change { padding: 8px 0; border-bottom: 1px solid var(--el-border-color-lighter); overflow-wrap: anywhere; }
</style>
