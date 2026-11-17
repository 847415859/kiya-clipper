<template>
  <el-card shadow="never" class="card viewer-review">
    <template #header><b>观众体验检查</b> <el-tag size="small" type="info">待人工复看</el-tag></template>
    <p class="hint">按第一次认识主播的观众来检查。以下是规则提醒和 AI 意见，不能代替观看成片。</p>
    <ul v-if="issues.length" class="issues">
      <li v-for="issue in issues" :key="issue.code">{{ issue.text }}</li>
    </ul>
    <p v-else class="hint">暂无规则提醒；仍需确认上下文、字幕和包装是否准确。</p>
    <div v-if="llm?.contextCaption" class="caption">
      <b>建议前情</b>：{{ llm.contextCaption }}
      <el-button size="small" :disabled="!!openingCaption(edit, llm.contextCaption) || llm.contextCaption.length > 40"
                 @click="$emit('caption', llm.contextCaption)">补到开头</el-button>
    </div>
    <el-button size="small" @click="$emit('seek', edit.trim.start)">检查开头</el-button>
    <el-button size="small" :disabled="!finalFile" @click="$emit('preview', finalFile)">手机成片复看</el-button>
    <p v-if="!finalFile" class="hint">先渲染预览，再核对真实字幕、BGM 与放大效果。</p>
    <details>
      <summary>标题、封面与内容对照</summary>
      <img v-if="coverUrl" :src="coverUrl" alt="当前发布封面" class="cover" />
      <p><b>当前标题：</b>{{ pack.title || '尚未设置' }}</p>
      <p><b>封面文字：</b>{{ pack.cover_text || '未设置文字' }}</p>
      <p><b>片段内容（AI）：</b>{{ llm?.whatHappened || '暂无结论' }}</p>
      <p><b>笑点原话（AI 引用，需听原声核对）：</b>{{ llm?.punchLine || '暂无引用' }}</p>
      <div v-for="(e, index) in llm?.evidence || []" :key="index" class="evidence">
        <span>{{ e.src }}：{{ e.quote }}</span>
        <el-button size="small" link :disabled="evidenceTime(llm, e, edit.window) === null"
                   @click="$emit('seek', evidenceTime(llm, e, edit.window))">定位证据</el-button>
      </div>
      <p class="hint">人工核对：标题是否兑现、封面是否暗示未发生的情节、陌生观众是否会把玩笑当成事实。证据超出素材范围时定位不可用。</p>
    </details>
    <details v-if="quality">
      <summary>最近一次成片质检</summary>
      <p class="hint">{{ quality.generated_at }} · {{ quality.media?.split(/[\\/]/).pop() }}。修改后请重新渲染；此报告不代表当前未渲染的编辑。</p>
      <p v-for="(issue, index) in [...(quality.errors || []), ...(quality.warnings || [])]" :key="index">{{ issue.message }}</p>
      <p v-if="!quality.errors?.length && !quality.warnings?.length" class="hint">程序检查未发现问题，仍需人工复看。</p>
    </details>
    <p class="hint">最终复看：手机宽度读清字幕；静音看懂前情；带声音听清原话；检查脸部和关键动作是否被遮挡。</p>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'
import { evidenceTime, openingCaption, viewerIssues } from '../viewer-review'
const props = defineProps({ edit: { type: Object, required: true }, llm: Object,
  pack: { type: Object, default: () => ({}) }, coverUrl: String, finalFile: String, quality: Object })
defineEmits(['caption', 'seek', 'preview'])
const issues = computed(() => viewerIssues(props.edit, props.llm))
</script>

<style scoped>
.hint { color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.6; }
.issues { padding-left: 20px; line-height: 1.7; color: var(--el-color-warning-dark-2); }
.caption { padding: 8px; background: var(--el-fill-color-light); margin-bottom: 10px; line-height: 1.8; }
details { margin-top: 14px; line-height: 1.6; }
summary { cursor: pointer; font-weight: 600; }
.cover { width: 100%; margin-top: 8px; border-radius: 4px; }
.evidence { border-top: 1px solid var(--el-border-color-lighter); padding: 5px 0; }
</style>
