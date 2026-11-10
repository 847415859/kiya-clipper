<template>
  <div class="editor" v-loading="loading">
    <div class="topbar">
      <el-button link @click="back">← 审核</el-button>
      <b>{{ name }} · #{{ rank }} 成片编辑</b>
      <span class="muted">高峰 {{ hms(info.t) }}，素材 {{ hms(winStart) }}–{{ hms(winEnd) }}（{{ fmt(info.clipLen) }}）</span>
      <span class="spacer" />
      <el-tag v-if="dirty" type="warning" size="small">未保存</el-tag>
      <el-tooltip content="让大模型按精排结果写一版花字 / 运镜 / 贴图，再手动微调" placement="bottom">
        <el-button :loading="drafting" @click="aiDraft">AI 初稿</el-button>
      </el-tooltip>
      <el-tooltip v-if="info.aiDraft" content="丢掉手动修改，回到 AI 刚生成的版本（这次保存不计入“剪辑口味”）" placement="bottom">
        <el-button @click="restoreAi">还原 AI 原稿</el-button>
      </el-tooltip>
      <el-button type="primary" :disabled="!dirty" :loading="saving" @click="save()">保存</el-button>
      <el-dropdown split-button type="success" :disabled="rendering" @click="render(480)" @command="render">
        {{ rendering ? '渲染中…' : '渲染 480p 预览' }}
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item :command="480">480p（快，先看效果）</el-dropdown-item>
            <el-dropdown-item :command="720">720p</el-dropdown-item>
            <el-dropdown-item :command="1080">1080p（发布用，最慢）</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <el-button v-if="renderJob" link type="primary" @click="$router.push({ path: '/jobs', query: { open: renderJob } })">渲染日志</el-button>
    </div>

    <div v-if="info.llm || warnings.length || generatedBy" class="ai-bar">
      <template v-if="info.llm && !info.llm.error">
        <el-tag size="small" :type="info.llm.funny ? 'success' : 'info'">AI {{ info.llm.funny ? '有梗' : '一般' }} {{ info.llm.score }}</el-tag>
        <el-tag size="small" effect="plain">{{ info.llm.type }}</el-tag>
        <span>{{ info.llm.whatHappened }}</span>
        <span v-if="info.llm.refined" class="muted">（建议起止已按精排修正：{{ info.llm.refined.note }}）</span>
        <span v-if="info.llm.titles && info.llm.titles.length" class="muted">标题：{{ info.llm.titles.join(' / ') }}</span>
      </template>
      <span v-if="generatedBy" class="muted">当前脚本由 {{ generatedBy }} 生成{{ aiNote ? '：' + aiNote : '' }}</span>
      <el-alert v-for="w in warnings" :key="w" :title="w" type="warning" :closable="false" show-icon class="warn" />
    </div>

    <div v-if="!loading && !videoSrc" class="empty-all">
      <template v-if="sameAsCandidate">
        这个片段还没剪，编辑需要先有审核片段做预览。
        <el-button type="primary" :loading="cutting" @click="cutClip">剪切这个片段（480p）</el-button>
      </template>
      <template v-else>
        素材范围 {{ hms(winStart) }}–{{ hms(winEnd) }} 超出了审核片段（按精排建议的起止），需要先剪一段预览视频。
        <el-button type="primary" :loading="cutting" @click="makePreview">生成预览（480p）</el-button>
        <el-button link @click="resetWindow">只用审核片段</el-button>
      </template>
    </div>

    <div v-else class="body">
      <section class="left">
        <div ref="stage" class="stage" @pointerdown.self="selected = null">
          <video ref="video" :src="videoSrc" :style="cameraStyle" preload="auto"
                 @loadedmetadata="onMeta" @timeupdate="syncTime" @play="playing = true; tick()" @pause="playing = false" />
          <div v-if="windowStale" class="stale">
            素材范围改了，画面还是旧的。
            <el-button size="small" type="primary" :loading="cutting" @click="makePreview">保存并生成新预览</el-button>
          </div>
          <div class="overlay" @pointerdown.self="selected = null">
            <template v-for="it in visibleItems" :key="it.id">
              <div v-if="it.type === 'text'" class="ov text" :class="{ sel: it.id === selected }" :style="textStyle(it)"
                   @pointerdown.stop="startDrag($event, it)">{{ it.text }}</div>
              <img v-else-if="it.type === 'sticker'" class="ov" :class="{ sel: it.id === selected }" :src="api.stickerUrl(it.asset)"
                   :style="stickerStyle(it)" draggable="false" @pointerdown.stop="startDrag($event, it)" />
            </template>
            <div v-if="selItem && selItem.type === 'zoom'" class="cross" :style="{ left: selItem.x * 100 + '%', top: selItem.y * 100 + '%' }"
                 @pointerdown.stop="startDrag($event, selItem)" title="拖动设置放大焦点" />
          </div>
          <div v-if="outsideTrim" class="trim-mask">裁剪范围之外（成片里不会出现）</div>
        </div>

        <div class="controls">
          <el-button size="small" @click="togglePlay">{{ playing ? '暂停' : '播放' }}</el-button>
          <span class="time">{{ fmt(time) }} / {{ fmt(info.clipLen) }}</span>
          <el-button size="small" @click="addText">+ 花字</el-button>
          <el-button size="small" @click="addZoom">+ 放大运镜</el-button>
          <el-button size="small" @click="gallery = true">+ 贴图</el-button>
          <span class="spacer" />
          <span class="muted">在当前时间添加 · 画面上可拖动位置 · 空格播放 · Delete 删除选中</span>
        </div>

        <div ref="track" class="timeline" @pointerdown.self="seekFromEvent">
          <div class="mask" :style="{ left: 0, width: pct(edit.trim.start) }" />
          <div class="mask" :style="{ left: pct(edit.trim.end), right: 0 }" />
          <div v-for="(row, ri) in rows" :key="row.type" class="row-label" :style="{ top: 18 + ri * 28 + 'px' }">{{ row.label }}</div>
          <div v-for="it in edit.items" :key="it.id" class="block" :class="[it.type, { sel: it.id === selected }]"
               :style="blockStyle(it)" @pointerdown.stop="startMove($event, it)">{{ blockLabel(it) }}</div>
          <div class="playhead" :style="{ left: pct(time) }" />
          <div class="ruler" @pointerdown="seekFromEvent">
            <span v-for="s in ticks" :key="s" :style="{ left: pct(s) }">{{ s }}s</span>
          </div>
        </div>

        <div class="trim">
          <span class="muted">素材范围</span>
          <el-button-group size="small">
            <el-button @click="shiftWindow(-10, 0)">往前多 10 秒</el-button>
            <el-button @click="shiftWindow(10, 0)">前面少 10 秒</el-button>
            <el-button @click="shiftWindow(0, -10)">后面少 10 秒</el-button>
            <el-button @click="shiftWindow(0, 10)">往后多 10 秒</el-button>
          </el-button-group>
          <span class="muted">审核片段 {{ hms(info.candidate.start) }}–{{ hms(info.candidate.end) }}</span>
          <el-button v-if="!sameAsCandidate" link size="small" @click="resetWindow">还原成审核片段</el-button>
        </div>
        <div class="trim">
          <span class="muted">成片范围</span>
          <el-slider v-model="trimRange" range :min="0" :max="info.clipLen || 60" :step="0.1" :format-tooltip="fmt" />
          <span class="muted">{{ fmt(edit.trim.start) }} – {{ fmt(edit.trim.end) }}（{{ fmt(edit.trim.end - edit.trim.start) }}）</span>
        </div>
        <p class="muted hint">预览说明：动画和位置与成片一致；画面上的字幕是审核片段里烧好的，放大时会跟着放大，成片里字幕由渲染器单独绘制、不会被放大。</p>
      </section>

      <aside class="right">
        <el-card shadow="never" class="card">
          <template #header><b>字幕</b></template>
          <el-form label-width="64px" size="small">
            <el-form-item label="显示"><el-switch v-model="edit.subtitles.enabled" /></el-form-item>
            <el-form-item label="样式">
              <el-select v-model="styleId" placeholder="默认样式" clearable :disabled="!edit.subtitles.enabled" style="width: 100%">
                <el-option v-for="s in styles" :key="s.id" :label="s.name + (s.isDefault ? '（默认）' : '')" :value="s.id" />
              </el-select>
            </el-form-item>
          </el-form>
        </el-card>

        <el-card shadow="never" class="card">
          <template #header>
            <b>{{ selItem ? typeName[selItem.type] : '效果' }}</b>
            <span v-if="selItem" style="float: right">
              <el-button link size="small" @click="duplicate">复制</el-button>
              <el-button link size="small" type="danger" @click="removeSelected">删除</el-button>
            </span>
          </template>
          <div v-if="!selItem" class="muted">
            点时间轴上的色块或画面上的元素来编辑；用左下的按钮在当前时间添加。共 {{ edit.items.length }} 个效果。
          </div>
          <el-form v-else label-width="64px" size="small" @submit.prevent>
            <el-form-item label="开始">
              <el-input-number v-model="selItem.start" :min="0" :max="info.clipLen" :step="0.1" :precision="2" controls-position="right" />
              <el-button link @click="selItem.start = round(time)">设为当前</el-button>
            </el-form-item>
            <el-form-item label="时长">
              <el-input-number v-model="selItem.duration" :min="0.2" :max="30" :step="0.1" :precision="2" controls-position="right" />
            </el-form-item>

            <template v-if="selItem.type === 'text'">
              <el-form-item label="内容"><el-input v-model="selItem.text" type="textarea" :rows="2" maxlength="40" show-word-limit /></el-form-item>
              <el-form-item label="动画"><el-select v-model="selItem.preset"><el-option v-for="p in textPresets" :key="p.v" :value="p.v" :label="p.l" /></el-select></el-form-item>
              <el-form-item label="字体"><el-select v-model="selItem.font"><el-option v-for="f in fonts" :key="f" :value="f" :label="f" /></el-select></el-form-item>
              <el-form-item label="字号"><el-slider v-model="selItem.size" :min="24" :max="300" /></el-form-item>
              <el-form-item label="颜色">
                <el-color-picker :model-value="selItem.color" show-alpha @change="(v) => (selItem.color = hex(v, selItem.color))" />
                <span class="muted" style="margin: 0 8px">描边</span>
                <el-color-picker :model-value="selItem.stroke" show-alpha @change="(v) => (selItem.stroke = hex(v, selItem.stroke))" />
              </el-form-item>
              <el-form-item label="描边粗细"><el-slider v-model="selItem.stroke_width" :min="0" :max="30" /></el-form-item>
              <el-form-item label="旋转"><el-slider v-model="selItem.rotate" :min="-45" :max="45" /></el-form-item>
            </template>

            <template v-if="selItem.type === 'zoom'">
              <el-form-item label="倍数"><el-slider v-model="selItem.scale" :min="1.05" :max="3" :step="0.05" /></el-form-item>
              <el-form-item label="震动"><el-slider v-model="selItem.shake" :min="0" :max="1" :step="0.05" /></el-form-item>
              <el-form-item label="推入"><el-slider v-model="selItem.ease_in" :min="0.05" :max="2" :step="0.05" /></el-form-item>
              <el-form-item label="拉回"><el-slider v-model="selItem.ease_out" :min="0.05" :max="2" :step="0.05" /></el-form-item>
              <p class="muted">画面上的十字是放大焦点，拖动它对准主播的脸或笑点位置。</p>
            </template>

            <template v-if="selItem.type === 'sticker'">
              <el-form-item label="图片">
                <img :src="api.stickerUrl(selItem.asset)" class="thumb" alt="" />
                <el-button link @click="gallery = true">更换</el-button>
              </el-form-item>
              <el-form-item label="动画"><el-select v-model="selItem.preset"><el-option v-for="p in stickerPresets" :key="p.v" :value="p.v" :label="p.l" /></el-select></el-form-item>
              <el-form-item label="大小"><el-slider v-model="selItem.scale" :min="0.03" :max="1" :step="0.01" /></el-form-item>
              <el-form-item label="旋转"><el-slider v-model="selItem.rotate" :min="-45" :max="45" /></el-form-item>
            </template>

            <template v-if="selItem.type !== 'zoom'">
              <el-form-item label="横向"><el-slider v-model="selItem.x" :min="0" :max="1" :step="0.01" /></el-form-item>
              <el-form-item label="纵向"><el-slider v-model="selItem.y" :min="0" :max="1" :step="0.01" /></el-form-item>
            </template>
          </el-form>
        </el-card>

        <el-card shadow="never" class="card">
          <template #header><b>成片</b> <span class="muted">（output/{{ name }}/final/）</span></template>
          <div v-if="!info.finals || !info.finals.length" class="muted">还没有渲染过。先“渲染 480p 预览”看效果，满意了再出 1080p。</div>
          <div v-for="f in info.finals" :key="f.file" class="final">
            <el-button link type="primary" @click="finalFile = f.file">{{ f.file }}</el-button>
            <span class="muted">{{ (f.size / 1048576).toFixed(1) }} MB · {{ new Date(f.modified).toLocaleString() }}</span>
          </div>
        </el-card>

        <el-card shadow="never" class="card" v-loading="packLoading">
          <template #header>
            <b>发布</b> <span class="muted">（投稿时复制粘贴；类型选“转载”，来源填直播间）</span>
            <el-button style="float: right" link size="small" @click="regenPack">按精排重新生成文字</el-button>
          </template>
          <div class="cover">
            <img v-if="pack.cover" :src="api.coverUrl(name, rank, pack.cover_version)" alt="封面" @click="openCover" />
            <div v-else class="muted cover-empty">还没有封面</div>
          </div>
          <el-form label-width="64px" size="small" class="pack">
            <el-form-item label="封面字">
              <el-input v-model="coverText" maxlength="30" placeholder="空着就是纯截图" />
            </el-form-item>
            <el-form-item label="画面">
              <span class="muted">{{ pack.cover_t != null ? hms(pack.cover_t) + '（' + (pack.cover_source || '') + '）' : '默认笑点' }}</span>
              <el-button link size="small" @click="coverAtCurrent">用当前播放位置</el-button>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" plain :loading="covering" @click="makeCover()">{{ pack.cover ? '重画封面' : '生成封面' }}</el-button>
              <a v-if="pack.cover" :href="api.coverUrl(name, rank, pack.cover_version)" :download="`${name}_${rank}_cover.jpg`"><el-button>下载</el-button></a>
            </el-form-item>
            <el-form-item label="标题">
              <el-select v-model="pack.title" filterable allow-create default-first-option style="width: 100%">
                <el-option v-for="t in pack.title_candidates || []" :key="t" :label="t" :value="t" />
              </el-select>
              <span class="muted">{{ (pack.title || '').length }}/80</span>
              <el-button link size="small" @click="copy(pack.title)">复制</el-button>
            </el-form-item>
            <el-form-item label="简介">
              <el-input v-model="pack.description" type="textarea" :rows="6" maxlength="2000" />
              <el-button link size="small" @click="copy(pack.description)">复制</el-button>
            </el-form-item>
            <el-form-item label="标签">
              <el-select v-model="pack.tags" multiple filterable allow-create default-first-option :multiple-limit="10" style="width: 100%" />
              <el-button link size="small" @click="copy((pack.tags || []).join(','))">复制（逗号分隔）</el-button>
            </el-form-item>
            <el-form-item label="来源">
              <span class="muted">{{ pack.source }}</span>
              <el-button link size="small" @click="copy(pack.source)">复制</el-button>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="savePack">保存文字</el-button>
            </el-form-item>
          </el-form>
          <div class="publish">
            <div class="muted">发布后填 BV 号：精排会参考它的播放数据（“精排记忆”页）</div>
            <el-input v-model="pub.bvid" placeholder="BV1xxxxxxxxx" size="small" style="width: 150px" maxlength="12" />
            <el-input v-model="pub.completion" placeholder="完播率 0.35（选填）" size="small" style="width: 140px" />
            <el-input v-model="pub.followers" placeholder="涨粉（选填）" size="small" style="width: 100px" />
            <el-button size="small" type="primary" plain :disabled="!pub.bvid" @click="savePublish">保存</el-button>
            <el-button v-if="pub.saved" size="small" link type="danger" @click="unpublish">解绑</el-button>
          </div>
        </el-card>
      </aside>
    </div>

    <el-dialog v-model="gallery" title="贴图库" width="720px">
      <div class="toolbar">
        <el-upload :show-file-list="false" accept=".png,.jpg,.jpeg,.gif,.webp" :http-request="upload">
          <el-button type="primary">上传图片</el-button>
        </el-upload>
        <span class="muted">PNG（透明背景最好）/ JPG / GIF / WEBP，最大 5 MB。只用有授权或可商用的素材。</span>
      </div>
      <div v-if="!stickers.length" class="muted">贴图库是空的，先上传几张表情包。</div>
      <div class="gallery">
        <div v-for="s in stickers" :key="s.name" class="cell" @click="pickSticker(s.name)">
          <img :src="api.stickerUrl(s.name)" :alt="s.name" />
          <div class="nm">{{ s.name }}</div>
          <el-button link size="small" type="danger" class="del" @click.stop="delSticker(s.name)">删除</el-button>
        </div>
      </div>
    </el-dialog>

    <el-dialog :model-value="!!finalFile" :title="finalFile" width="900px" destroy-on-close @close="finalFile = ''">
      <video v-if="finalFile" :src="api.finalUrl(name, finalFile)" controls autoplay style="width: 100%" />
      <template #footer>
        <a v-if="finalFile" :href="api.finalUrl(name, finalFile)" :download="finalFile"><el-button>下载</el-button></a>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, hms } from '../api'
// 与渲染器共用同一份动画代码：预览里的弹出、抖动、放大和成片一致
import { cameraAt, enterExit, outlineShadow } from '../../../renderer/src/anim.js'

const route = useRoute()
const router = useRouter()
const name = computed(() => route.params.name)
const rank = computed(() => Number(route.params.rank))

const typeName = { text: '花字', zoom: '放大运镜', sticker: '贴图' }
const rows = [{ type: 'text', label: '花字' }, { type: 'zoom', label: '运镜' }, { type: 'sticker', label: '贴图' }]
const textPresets = [{ v: 'pop', l: '弹出' }, { v: 'slam', l: '砸下（带震动）' }, { v: 'shake', l: '持续抖动' }, { v: 'bounce', l: '掉落弹跳' }, { v: 'fade', l: '淡入' }]
const stickerPresets = [{ v: 'pop', l: '弹出' }, { v: 'slide', l: '右侧滑入' }, { v: 'shake', l: '持续抖动' }, { v: 'bounce', l: '掉落弹跳' }, { v: 'slam', l: '砸下' }, { v: 'fade', l: '淡入' }]
const fonts = ['Noto Sans CJK SC', 'ZCOOL KuaiLe']

const loading = ref(true)
const saving = ref(false)
const cutting = ref(false)
const rendering = ref(false)
const renderJob = ref(null)
const drafting = ref(false)
const pub = reactive({ bvid: '', completion: '', followers: '', saved: '' })
const pack = ref({})
const packLoading = ref(false)
const covering = ref(false)
const coverText = ref('')
let coverT = null
const warnings = ref([])
const generatedBy = ref('')
const aiNote = ref('')
let restoredAi = false           // 刚点了“还原 AI 原稿”：下一次保存不算人工修改
const info = reactive({ aiDraft: null, clipLen: 60, clip: null, preview: null, t: 0, finals: [], llm: null, exists: false,
  candidate: { start: 0, end: 60 }, videoWindow: null })
const edit = reactive({ window: { start: 0, end: 60 }, trim: { start: 0, end: 60 }, subtitles: { enabled: true }, items: [] })
const styleId = ref(null)
const styles = ref([])
const stickers = ref([])
const selected = ref(null)
const time = ref(0)
const playing = ref(false)
const dirty = ref(false)
const gallery = ref(false)
const finalFile = ref('')
const video = ref(null)
const stage = ref(null)
const track = ref(null)
const stageSize = reactive({ w: 960, h: 540 })
let loaded = false
let raf = 0
let resizeObs = null

const winStart = computed(() => edit.window.start)
const winEnd = computed(() => edit.window.end)
const sameAsCandidate = computed(() => Math.abs(edit.window.start - info.candidate.start) < 0.05 && Math.abs(edit.window.end - info.candidate.end) < 0.05)
const videoSrc = ref('')
// 画面对应的素材范围和当前编辑的不一致（改了素材范围还没重新剪预览）
const windowStale = computed(() => !!info.videoWindow && (Math.abs(info.videoWindow.start - edit.window.start) > 0.05 || Math.abs(info.videoWindow.end - edit.window.end) > 0.05))
const selItem = computed(() => edit.items.find((i) => i.id === selected.value) || null)
const visibleItems = computed(() => edit.items.filter((i) => i.type !== 'zoom' && time.value >= i.start && time.value < i.start + i.duration))
const outsideTrim = computed(() => time.value < edit.trim.start || time.value > edit.trim.end)
const ticks = computed(() => {
  const step = info.clipLen > 90 ? 15 : info.clipLen > 30 ? 10 : 5
  const out = []
  for (let s = 0; s <= info.clipLen; s += step) out.push(s)
  return out
})
const trimRange = computed({
  get: () => [edit.trim.start, edit.trim.end],
  set: ([a, b]) => { edit.trim.start = a; edit.trim.end = b }
})

const round = (x) => Math.round(x * 100) / 100
const clamp = (x, a, b) => Math.min(b, Math.max(a, x))
const pct = (s) => `${(clamp(s, 0, info.clipLen) / (info.clipLen || 1)) * 100}%`
const newId = () => Math.random().toString(36).slice(2, 10)
function fmt(s) {
  s = Math.max(0, s || 0)
  return `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, '0')}`
}

// 颜色：取色器可能给 #rrggbbaa 或 rgba(...)，统一成 #RRGGBB(AA)
function hex(v, old) {
  if (!v) return old
  const m = /^rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)$/i.exec(v)
  const h2 = (n) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, '0')
  if (m) {
    const a = m[4] === undefined ? 1 : parseFloat(m[4])
    return ('#' + h2(+m[1]) + h2(+m[2]) + h2(+m[3]) + (a < 1 ? h2(a * 255) : '')).toUpperCase()
  }
  return String(v).toUpperCase()
}

// ---------------------------------------------------------------- 预览
const cameraStyle = computed(() => {
  const zooms = edit.items.filter((i) => i.type === 'zoom')
  const c = cameraAt(time.value, zooms, stageSize.w, stageSize.h)
  return { transformOrigin: `${c.x * 100}% ${c.y * 100}%`, transform: `translate(${c.dx}px, ${c.dy}px) scale(${c.scale})` }
})

function anim(it) {
  const seed = edit.items.indexOf(it) + 1
  return enterExit(it.preset || 'pop', time.value - it.start, it.duration, stageSize.w, stageSize.h, seed)
}

function textStyle(it) {
  const a = anim(it)
  const k = stageSize.h / 1080
  return {
    left: it.x * 100 + '%', top: it.y * 100 + '%',
    transform: `translate(-50%, -50%) translate(${a.dx}px, ${a.dy}px) rotate(${(it.rotate || 0) + a.rotate}deg) scale(${a.scale})`,
    opacity: Math.max(0.15, a.opacity),
    fontFamily: `"${it.font}", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif`,
    fontSize: it.size * k + 'px', color: it.color,
    textShadow: outlineShadow((it.stroke_width ?? 8) * k, it.stroke, 6 * k, 'rgba(0,0,0,0.45)')
  }
}

function stickerStyle(it) {
  const a = anim(it)
  return {
    left: it.x * 100 + '%', top: it.y * 100 + '%', width: it.scale * 100 + '%',
    transform: `translate(-50%, -50%) translate(${a.dx}px, ${a.dy}px) rotate(${(it.rotate || 0) + a.rotate}deg) scale(${a.scale})`,
    opacity: Math.max(0.15, a.opacity)
  }
}

function syncTime() {
  if (video.value) time.value = video.value.currentTime
}
function tick() {
  cancelAnimationFrame(raf)
  const loop = () => {
    syncTime()
    if (playing.value) raf = requestAnimationFrame(loop)
  }
  raf = requestAnimationFrame(loop)
}
function onMeta() {
  measure()
}
function measure() {
  if (stage.value) {
    stageSize.w = stage.value.clientWidth
    stageSize.h = stage.value.clientHeight
  }
}
function seek(t) {
  t = clamp(t, 0, info.clipLen)
  time.value = t
  if (video.value) video.value.currentTime = t
}
function togglePlay() {
  const v = video.value
  if (!v) return
  if (v.paused) v.play()
  else v.pause()
}
function seekFromEvent(e) {
  const r = track.value.getBoundingClientRect()
  seek(((e.clientX - r.left) / r.width) * info.clipLen)
  selected.value = null
}

// 画面上拖动元素：更新中心位置（画面比例 0~1）
function startDrag(e, it) {
  selected.value = it.id
  const r = stage.value.getBoundingClientRect()
  const move = (ev) => {
    it.x = round(clamp((ev.clientX - r.left) / r.width, 0, 1))
    it.y = round(clamp((ev.clientY - r.top) / r.height, 0, 1))
  }
  const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up) }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
}

// 时间轴上拖动色块：改开始时间
function startMove(e, it) {
  selected.value = it.id
  const r = track.value.getBoundingClientRect()
  const x0 = e.clientX
  const s0 = it.start
  let moved = false
  const move = (ev) => {
    const d = ((ev.clientX - x0) / r.width) * info.clipLen
    if (Math.abs(ev.clientX - x0) > 3) moved = true
    it.start = round(clamp(s0 + d, 0, info.clipLen - 0.2))
  }
  const up = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
    if (!moved) seek(it.start + 0.01)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
}

function blockStyle(it) {
  const row = rows.findIndex((r) => r.type === it.type)
  return { left: pct(it.start), width: `${(Math.min(it.duration, info.clipLen - it.start) / (info.clipLen || 1)) * 100}%`, top: 18 + row * 28 + 'px' }
}
function blockLabel(it) {
  if (it.type === 'text') return it.text
  if (it.type === 'zoom') return `×${it.scale}`
  return it.asset
}

// ---------------------------------------------------------------- 增删效果
function add(it) {
  edit.items.push(it)
  selected.value = it.id
}
function addText() {
  add({ id: newId(), type: 'text', start: round(time.value), duration: 2, text: '笑死', preset: 'pop', x: 0.5, y: 0.3, size: 110,
        color: '#FFE14D', stroke: '#E0457B', stroke_width: 8, rotate: 0, font: 'Noto Sans CJK SC' })
}
function addZoom() {
  add({ id: newId(), type: 'zoom', start: round(Math.max(0, time.value - 0.2)), duration: 2.5, scale: 1.5, x: 0.5, y: 0.45,
        shake: 0, ease_in: 0.25, ease_out: 0.3 })
}
function pickSticker(asset) {
  gallery.value = false
  if (selItem.value && selItem.value.type === 'sticker') {
    selItem.value.asset = asset
    return
  }
  add({ id: newId(), type: 'sticker', start: round(time.value), duration: 1.5, asset, preset: 'pop', x: 0.8, y: 0.65, scale: 0.22, rotate: 0 })
}
function duplicate() {
  const it = selItem.value
  if (!it) return
  add({ ...JSON.parse(JSON.stringify(it)), id: newId(), start: round(Math.min(info.clipLen - 0.2, it.start + it.duration)) })
}
function removeSelected() {
  const i = edit.items.findIndex((x) => x.id === selected.value)
  if (i >= 0) edit.items.splice(i, 1)
  selected.value = null
}

// ---------------------------------------------------------------- 贴图库
async function loadStickers() {
  stickers.value = await api.stickers()
}
async function upload({ file }) {
  const r = await api.uploadSticker(file)
  ElMessage.success(`已上传 ${r.name}`)
  await loadStickers()
}
async function delSticker(n) {
  await ElMessageBox.confirm(`从贴图库删除 ${n}？已经用了它的片段渲染时会报错。`, '删除贴图', { type: 'warning' })
  await api.deleteSticker(n)
  loadStickers()
}

// ---------------------------------------------------------------- 加载 / 保存 / 渲染
function apply(e) {
  if (e.window) Object.assign(edit.window, { start: e.window.start, end: e.window.end })
  edit.trim.start = e.trim.start
  edit.trim.end = e.trim.end
  edit.subtitles.enabled = e.subtitles ? e.subtitles.enabled !== false : true
  styleId.value = (e.subtitles && e.subtitles.style_id) || null
  edit.items.splice(0, edit.items.length, ...(e.items || []).map((i) => ({ ...i, id: i.id || newId() })))
  generatedBy.value = e.generated_by || ''
  aiNote.value = e.note || ''
}

async function load() {
  loading.value = true
  loaded = false
  try {
    const r = await api.getEdit(name.value, rank.value)
    Object.assign(info, { clipLen: r.clipLen, clip: r.clip, preview: r.preview || null, t: r.t, finals: r.finals || [],
      llm: r.llm || null, exists: !!r.exists, aiDraft: r.aiDraft || null, candidate: r.candidate || { start: 0, end: r.clipLen } })
    info.videoWindow = null
    videoSrc.value = ''
    pickVideo()
    apply(r.edit)
    styles.value = await api.styles()
    loadPublish()
    loadPack()
    loadStickers()
  } finally {
    loading.value = false
    await nextTick()
    dirty.value = false
    loaded = true
    measure()
  }
}

watch([edit, styleId], () => { if (loaded) dirty.value = true }, { deep: true })

async function save(quiet) {
  saving.value = true
  try {
    const body = { version: 1, window: { ...edit.window }, trim: { ...edit.trim }, subtitles: { enabled: edit.subtitles.enabled }, items: edit.items }
    if (generatedBy.value) Object.assign(body, { generated_by: generatedBy.value, note: aiNote.value })
    const r = await api.saveEdit(name.value, rank.value, body, edit.subtitles.enabled ? styleId.value : null, restoredAi)
    restoredAi = false
    warnings.value = r.warnings || []
    info.exists = true
    loaded = false
    const sel = selected.value
    apply(r.edit)
    selected.value = sel
    await nextTick()
    dirty.value = false
    loaded = true
    if (!quiet) ElMessage[warnings.value.length ? 'warning' : 'success'](warnings.value.length ? `已保存，有 ${warnings.value.length} 条提醒` : '已保存')
    return true
  } finally {
    saving.value = false
  }
}

async function restoreAi() {
  await ElMessageBox.confirm('丢掉现在的修改，回到 AI 刚生成的版本？', '还原 AI 原稿', { type: 'warning', confirmButtonText: '还原' })
  apply(info.aiDraft)
  restoredAi = true
  await nextTick()
  dirty.value = true
}

async function render(height) {
  if (dirty.value || height >= 1080) await save(true)          // 渲染发布用的 1080p 也算“确认这版”
  const j = await api.submitJob('RENDER', name.value, { rank: rank.value, height })
  renderJob.value = j.id
  rendering.value = true
  ElMessage.success(`已提交渲染（${height}p），完成后会出现在右下“成片”里`)
  try {
    for (;;) {
      await new Promise((r) => setTimeout(r, 4000))
      const s = await api.job(j.id)
      if (s.status === 'SUCCESS') {
        const r = await api.getEdit(name.value, rank.value)
        info.finals = r.finals || []
        ElMessage.success('渲染完成')
        break
      }
      if (s.status === 'FAILED' || s.status === 'CANCELED') {
        ElMessage.error(`渲染没有完成：${s.summary || s.status}（点“渲染日志”看详情）`)
        break
      }
    }
  } finally {
    rendering.value = false
  }
}

// ---------------------------------------------------------------- 发布包
async function loadPack() {
  packLoading.value = true
  try {
    pack.value = await api.publishPack(name.value, rank.value)
    coverText.value = pack.value.cover_text ?? (pack.value.title_candidates || [])[0] ?? ''
    coverT = null
  } catch (e) {
    // 发布包读不到不影响编辑
  } finally {
    packLoading.value = false
  }
}

function coverAtCurrent() {
  coverT = Math.round((edit.window.start + time.value) * 100) / 100
  makeCover()
}

async function makeCover() {
  covering.value = true
  try {
    pack.value = { ...pack.value, ...(await api.publishCover(name.value, rank.value, { t: coverT, text: coverText.value })) }
    ElMessage.success('封面已生成')
  } finally {
    covering.value = false
  }
}

async function savePack() {
  pack.value = await api.savePublishPack(name.value, rank.value,
    { title: pack.value.title, description: pack.value.description, tags: pack.value.tags || [] })
  ElMessage.success('已保存')
}

async function regenPack() {
  await ElMessageBox.confirm('标题、简介、标签按精排结果重新生成，覆盖改过的内容？', '重新生成', { type: 'warning' })
  pack.value = await api.regeneratePublish(name.value, rank.value)
}

function openCover() {
  window.open(api.coverUrl(name.value, rank.value, pack.value.cover_version), '_blank', 'noopener')
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text || '')
    ElMessage.success('已复制')
  } catch (e) {
    ElMessage.warning('浏览器不让直接复制，请手动选中复制')
  }
}

async function loadPublish() {
  try {
    const d = (await api.memory()).decisions || {}
    const hit = Object.entries(d.published || {}).find(([, v]) => v.session === name.value && Number(v.rank) === rank.value)
    Object.assign(pub, hit
      ? { bvid: hit[0], completion: hit[1].completion ?? '', followers: hit[1].followers ?? '', saved: hit[0] }
      : { bvid: '', completion: '', followers: '', saved: '' })
  } catch (e) {
    // 精排记忆读不到不影响编辑
  }
}

async function savePublish() {
  const bvid = pub.bvid.trim()
  if (pub.saved && pub.saved !== bvid) await api.memoryAction({ action: 'unpublish', bvid: pub.saved })
  await api.memoryAction({ action: 'publish', bvid, session: name.value, rank: rank.value,
    completion: pub.completion === '' ? null : pub.completion, followers: pub.followers === '' ? null : pub.followers })
  pub.saved = bvid
  ElMessage.success('已绑定，下次“更新精排记忆”时拉取播放数据')
}

async function unpublish() {
  await api.memoryAction({ action: 'unpublish', bvid: pub.saved })
  Object.assign(pub, { bvid: '', completion: '', followers: '', saved: '' })
}

async function waitJob(id, every) {
  for (;;) {
    await new Promise((r) => setTimeout(r, every))
    const s = await api.job(id)
    if (s.status !== 'PENDING' && s.status !== 'RUNNING') return s
  }
}

async function aiDraft() {
  const has = info.exists || edit.items.length > 0
  if (has) {
    await ElMessageBox.confirm('AI 初稿会覆盖当前的剪辑脚本（花字、运镜、贴图和裁剪范围），确定？', 'AI 初稿',
      { type: 'warning', confirmButtonText: '覆盖' })
  }
  if (!info.llm) {
    ElMessage.info('这个候选还没有精排结果，初稿只能参考原话和弹幕；建议先在场次页跑“精排”')
  }
  drafting.value = true
  try {
    const j = await api.submitJob('AUTO_EDIT', name.value, { rank: rank.value, force: has })
    const s = await waitJob(j.id, 3000)
    if (s.status !== 'SUCCESS') return ElMessage.error(`初稿没有生成：${s.summary || s.status}（详情见任务页）`)
    await load()
    ElMessage.success('AI 初稿已载入，检查后记得渲染预览')
  } finally {
    drafting.value = false
  }
}

// 选画面：素材范围 = 审核片段时用审核片段，否则用按素材范围剪好的预览；都没有就保留当前画面（提示需要重新剪）
function pickVideo() {
  const w = { start: edit.window.start, end: edit.window.end }
  if (sameAsCandidate.value && info.clip) {
    videoSrc.value = api.clipUrl(name.value, info.clip)
    info.videoWindow = w
  } else if (info.preview && info.preview === previewName(w)) {
    videoSrc.value = api.editPreviewUrl(name.value, info.preview)
    info.videoWindow = w
  }
}

function previewName(w) {
  return `${String(rank.value).padStart(2, '0')}_${w.start.toFixed(1)}_${w.end.toFixed(1)}.mp4`
}

// 改素材范围：所有时间都相对素材范围开头，开头往前挪多少，效果和成片范围就往后挪多少，画面位置不变
function setWindow(start, end) {
  start = Math.round(Math.max(0, start) * 10) / 10
  end = Math.round(end * 10) / 10
  const len = end - start
  if (len < 5 || len > 300) return ElMessage.warning('素材范围要在 5 秒到 5 分钟之间')
  const d = Math.round((edit.window.start - start) * 10) / 10
  const outside = []
  for (const it of edit.items) it.start = round(it.start + d)
  edit.items.splice(0, edit.items.length, ...edit.items.filter((it) => (it.start >= 0 && it.start < len) || !outside.push(it)))
  edit.trim.start = clamp(round(edit.trim.start + d), 0, len)
  edit.trim.end = clamp(round(edit.trim.end + d), 0, len)
  if (edit.trim.end - edit.trim.start < 1) Object.assign(edit.trim, { start: 0, end: len })
  Object.assign(edit.window, { start, end })
  info.clipLen = len
  pickVideo()
  if (outside.length) ElMessage.warning(`有 ${outside.length} 个效果落在新的素材范围之外，已删掉`)
}

function shiftWindow(ds, de) {
  setWindow(edit.window.start + ds, edit.window.end + de)
}

function resetWindow() {
  setWindow(info.candidate.start, info.candidate.end)
}

async function makePreview() {
  cutting.value = true
  try {
    if (dirty.value || !info.exists) await save(true)
    if (sameAsCandidate.value) {
      await load()
      if (!info.clip) await cutClip()
      return
    }
    const j = await api.submitJob('EDIT_PREVIEW', name.value, { rank: rank.value, start: edit.window.start, end: edit.window.end })
    const s = await waitJob(j.id, 3000)
    if (s.status !== 'SUCCESS') return ElMessage.error(`预览没剪出来：${s.summary || s.status}（详情见任务页）`)
    await load()
  } finally {
    cutting.value = false
  }
}

async function cutClip() {
  cutting.value = true
  try {
    const j = await api.submitJob('CUT', name.value, { ranks: [rank.value], height: 480 })
    for (;;) {
      await new Promise((r) => setTimeout(r, 3000))
      const s = await api.job(j.id)
      if (s.status === 'SUCCESS') break
      if (s.status === 'FAILED' || s.status === 'CANCELED') return ElMessage.error('剪切失败，详情见任务页')
    }
    await load()
  } finally {
    cutting.value = false
  }
}

function back() {
  router.push(`/sessions/${name.value}/review`)
}

function onKey(e) {
  const tag = (e.target && e.target.tagName) || ''
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag) || e.ctrlKey || e.metaKey || e.altKey) return
  if (e.key === ' ') { e.preventDefault(); togglePlay() }
  else if ((e.key === 'Delete' || e.key === 'Backspace') && selected.value) { e.preventDefault(); removeSelected() }
}

onBeforeRouteLeave(async () => {
  if (!dirty.value) return true
  try {
    await ElMessageBox.confirm('有未保存的修改，确定离开？', '离开编辑', { type: 'warning', confirmButtonText: '不保存，离开' })
    return true
  } catch (e) {
    return false
  }
})

onMounted(() => {
  load()
  window.addEventListener('keydown', onKey)
  if (window.ResizeObserver) {
    resizeObs = new ResizeObserver(measure)
    nextTick(() => stage.value && resizeObs.observe(stage.value))
  }
})
onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  window.removeEventListener('keydown', onKey)
  if (resizeObs) resizeObs.disconnect()
})
</script>

<style scoped>
.editor { height: 100%; display: flex; flex-direction: column; }
.topbar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; padding: 10px 16px; background: #fff; border-bottom: 1px solid var(--el-border-color-light); }
.topbar .spacer, .controls .spacer { flex: 1; }
.empty-all { padding: 60px; text-align: center; color: var(--muted); display: flex; flex-direction: column; gap: 16px; align-items: center; }
.body { flex: 1; min-height: 0; display: grid; grid-template-columns: 1fr 360px; }
.left { padding: 16px; overflow: auto; }
.right { border-left: 1px solid var(--el-border-color-light); overflow: auto; padding: 12px; background: #fff; display: flex; flex-direction: column; gap: 12px; }
.card :deep(.el-card__header) { padding: 10px 14px; }
.card :deep(.el-card__body) { padding: 12px 14px; }
.stage { position: relative; width: 100%; aspect-ratio: 16 / 9; background: #000; overflow: hidden; border-radius: 6px; user-select: none; }
.stage video { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; }
.overlay { position: absolute; inset: 0; }
.ov { position: absolute; cursor: move; white-space: pre; line-height: 1.15; font-weight: 900; text-align: center; }
.ov.sel { outline: 2px dashed rgba(64, 158, 255, 0.9); outline-offset: 4px; }
.cross { position: absolute; width: 28px; height: 28px; margin: -14px 0 0 -14px; border: 2px solid #409eff; border-radius: 50%; cursor: move; box-shadow: 0 0 0 2px rgba(255,255,255,0.8); }
.cross::before, .cross::after { content: ''; position: absolute; background: #409eff; }
.cross::before { left: 12px; top: -8px; width: 2px; height: 40px; }
.cross::after { top: 12px; left: -8px; height: 2px; width: 40px; }
.trim-mask { position: absolute; left: 0; right: 0; bottom: 0; padding: 6px; text-align: center; color: #fff; background: rgba(0,0,0,0.55); font-size: 12px; }
.controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 10px 0; }
.time { font-variant-numeric: tabular-nums; min-width: 110px; }
.timeline { position: relative; height: 104px; background: #fff; border: 1px solid var(--el-border-color-light); border-radius: 6px; overflow: hidden; cursor: pointer; }
.mask { position: absolute; top: 0; bottom: 0; background: rgba(0, 0, 0, 0.06); pointer-events: none; }
.row-label { position: absolute; left: 4px; font-size: 11px; color: var(--muted); pointer-events: none; }
.block { position: absolute; height: 22px; min-width: 6px; border-radius: 4px; font-size: 11px; line-height: 22px; padding: 0 4px; overflow: hidden; white-space: nowrap; color: #fff; cursor: grab; box-sizing: border-box; }
.block.text { background: #e0457b; }
.block.zoom { background: #409eff; }
.block.sticker { background: #67c23a; }
.block.sel { box-shadow: 0 0 0 2px #303133; }
.playhead { position: absolute; top: 0; bottom: 0; width: 2px; background: #f56c6c; pointer-events: none; }
.ruler { position: absolute; left: 0; right: 0; top: 0; height: 16px; border-bottom: 1px solid var(--el-border-color-lighter); }
.ruler span { position: absolute; top: 1px; font-size: 10px; color: var(--muted); transform: translateX(2px); }
.trim { display: grid; grid-template-columns: auto 1fr auto; gap: 12px; align-items: center; margin-top: 12px; }
.hint { margin-top: 8px; }
.thumb { max-width: 80px; max-height: 60px; margin-right: 8px; border-radius: 4px; background: #f4f4f5; }
.final { display: flex; flex-direction: column; margin-bottom: 6px; }
.gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 10px; margin-top: 12px; max-height: 60vh; overflow: auto; }
.cell { border: 1px solid var(--el-border-color-light); border-radius: 6px; padding: 6px; text-align: center; cursor: pointer; position: relative; }
.cell:hover { border-color: var(--el-color-primary); }
.cell img { max-width: 100%; height: 80px; object-fit: contain; background: repeating-conic-gradient(#eee 0 25%, #fff 0 50%) 0 0 / 12px 12px; }
.cell .nm { font-size: 11px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cell .del { position: absolute; top: 2px; right: 4px; }
@media (max-width: 1000px) { .body { grid-template-columns: 1fr; } .right { border-left: none; } }
.ai-bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 6px 12px; font-size: 13px; border-bottom: 1px solid var(--el-border-color-lighter); }
.ai-bar .warn { width: 100%; padding: 4px 10px; }
.publish { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 10px; padding-top: 10px; border-top: 1px dashed var(--el-border-color); }
.publish .muted { width: 100%; font-size: 12px; }
.stale { position: absolute; top: 8px; left: 50%; transform: translateX(-50%); z-index: 5; background: rgba(0, 0, 0, 0.72); color: #fff; padding: 6px 12px; border-radius: 6px; font-size: 13px; display: flex; gap: 8px; align-items: center; }
.cover img { width: 100%; aspect-ratio: 16 / 10; object-fit: cover; border-radius: 6px; cursor: zoom-in; display: block; }
.cover-empty { aspect-ratio: 16 / 10; display: flex; align-items: center; justify-content: center; background: var(--el-fill-color-light); border-radius: 6px; }
.pack { margin-top: 10px; }
</style>
