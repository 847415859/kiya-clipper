<template>
  <div class="page" v-loading="loading">
    <div class="toolbar">
      <el-date-picker v-model="end" type="date" value-format="YYYY-MM-DD" placeholder="截止日期（默认今天）" :clearable="true" style="width: 180px" @change="load" />
      <el-select v-model="days" style="width: 110px" @change="load">
        <el-option :value="7" label="最近 7 天" />
        <el-option :value="14" label="最近 14 天" />
        <el-option :value="30" label="最近 30 天" />
      </el-select>
      <el-button @click="load">重新统计</el-button>
      <a :href="api.weeklyExportUrl(end, days)" download>
        <el-button type="primary" :disabled="!rep || !pub.overview.sessions">导出给主播的文档</el-button>
      </a>
      <span class="spacer" />
      <span v-if="rep" class="muted">{{ rep.period.label }} · 生成于 {{ rep.generated_at }}</span>
    </div>

    <template v-if="rep">
      <el-alert v-for="n in rep.internal.notes" :key="n" :title="n" type="warning" :closable="false" show-icon class="card" />
      <el-alert type="info" :closable="false" show-icon class="card"
                title="导出的文档只有下面“梗 / 高光场面 / 类型 / 词云”这几块，不含审核备注、通过率、禁区话题和上面这些提示。禁区命中的、审核标了不有趣的片段不会出现。" />

      <el-card shadow="never" class="card">
        <template #header><b>概览</b></template>
        <el-space size="large" wrap>
          <span>直播 <b>{{ pub.overview.sessions }}</b> 场（上一个周期 {{ pub.overview.prev_sessions }} 场）</span>
          <span>弹幕 <b>{{ pub.overview.danmaku.toLocaleString() }}</b> 条</span>
          <span>精选高光 <b>{{ pub.overview.moments }}</b> 个</span>
        </el-space>
        <p v-if="pub.note" class="muted">{{ pub.note }}</p>
      </el-card>

      <el-card shadow="never" class="card">
        <template #header><b>本周最火的梗 / 说法 TOP{{ pub.memes.length || 5 }}</b>
          <span class="muted">（主播说的次数 + 观众刷的条数；较上周涨跌；不够 {{ minMentions }} 次的不列，也不凑数）</span></template>
        <ol v-if="pub.memes.length" class="memes">
          <li v-for="m in pub.memes" :key="m.text">
            <b>{{ m.text }}</b>
            <el-tag size="small" :type="m.is_new ? 'danger' : m.trend >= 0 ? 'success' : 'info'" class="tr">
              {{ m.is_new ? '本周新出现' : `较上周 ${m.trend >= 0 ? '+' : ''}${Math.round(m.trend * 100)}%` }}
            </el-tag>
            <el-tag v-if="!m.known" size="small" type="warning" class="tr">新发现（可去“精排记忆”转正）</el-tag>
            <span class="muted">主播说 {{ m.speech }} 次 · 弹幕 {{ m.danmaku }} 条 · {{ m.sessions }} 场</span>
            <div v-if="m.moment && m.moment.what" class="muted">出现在 {{ m.moment.date }}：{{ m.moment.what }}</div>
          </li>
        </ol>
        <span v-else class="muted">这周还没有反复出现的说法（需要有转写或弹幕记录）。</span>
      </el-card>

      <el-card shadow="never" class="card">
        <template #header><b>高光场面</b></template>
        <ol v-if="pub.highlights.length" class="memes">
          <li v-for="h in pub.highlights" :key="h.session + h.rank">
            <el-tag size="small" class="tr">{{ h.type }}</el-tag>
            <b>{{ h.date }}</b> <span class="muted">（开播后 {{ h.at }} · {{ h.session }} #{{ h.rank }}）</span>
            <div v-if="h.title" class="ttl">{{ h.title }}</div>
            <div>{{ h.what }}</div>
          </li>
        </ol>
        <span v-else class="muted">本周还没有精选出的高光场面（先对这几场做“大模型精排”）。</span>
      </el-card>

      <el-card shadow="never" class="card">
        <template #header><b>哪类内容最容易出效果</b>
          <span class="muted">（高光片段攒够 {{ pub.types.need }} 个才出占比：现在 {{ pub.types.have }}/{{ pub.types.need }}）</span></template>
        <el-table v-if="pub.types.enough" :data="pub.types.rows" size="small">
          <el-table-column label="类型" min-width="170">
            <template #default="{ row }"><b>{{ row.type }}</b> <span class="muted">{{ row.hint }}</span></template>
          </el-table-column>
          <el-table-column prop="n" label="本周高光数" width="110" />
          <el-table-column label="占比" width="90"><template #default="{ row }">{{ Math.round(row.share * 100) }}%</template></el-table-column>
          <el-table-column label="AI 均分" width="90"><template #default="{ row }">{{ row.avg_ai }}</template></el-table-column>
          <el-table-column label="切片播放表现" min-width="160">
            <template #default="{ row }">{{ row.view_rel == null ? '—' : `${row.view_rel}× 中位数（${row.view_n} 条）` }}</template>
          </el-table-column>
        </el-table>
        <span v-else class="muted">样本还不够，先不下结论（几个片段的占比没有参考价值）。</span>
        <p v-if="pub.types.enough && !pub.types.has_views" class="muted hint">切片播放表现需要切片号绑定满 5 条成片（“精排记忆”页），现在没有。</p>
      </el-card>

      <el-card shadow="never" class="card">
        <template #header><b>观众常刷的词</b></template>
        <div v-if="pub.words.length" class="cloud">
          <el-tooltip v-for="(w, i) in pub.words" :key="w.text" :content="`${w.count} 条`" placement="top">
            <span :style="{ fontSize: 15 + w.weight * 30 + 'px', color: palette[i % palette.length], opacity: 0.65 + w.weight * 0.35 }">{{ w.text }}</span>
          </el-tooltip>
        </div>
        <span v-else class="muted">本周没有弹幕记录。</span>
      </el-card>

      <p class="muted">统计的场次：{{ rep.internal.sessions.join('、') || '—' }}</p>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'

const end = ref('')
const days = ref(7)
const rep = ref(null)
const loading = ref(false)
const minMentions = 5
const palette = ['#d6336c', '#7048e8', '#1c7ed6', '#0ca678', '#f08c00', '#e8590c']
const pub = computed(() => rep.value.public)

async function load() {
  loading.value = true
  try {
    rep.value = await api.weekly(end.value, days.value)
  } finally {
    loading.value = false
  }
}
onMounted(load)
</script>

<style scoped>
.card { margin-bottom: 16px; }
.toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }
.spacer { flex: 1; }
.muted { color: var(--el-text-color-secondary); font-size: 13px; }
.memes { margin: 0; padding-left: 22px; }
.memes li { margin-bottom: 12px; line-height: 1.7; }
.memes b { font-size: 16px; margin-right: 6px; }
.tr { margin-right: 6px; }
.ttl { font-weight: 600; }
.hint { margin: 8px 0 0; }
.cloud { display: flex; flex-wrap: wrap; gap: 6px 16px; align-items: baseline; line-height: 1.3; }
.cloud span { cursor: default; }
</style>
