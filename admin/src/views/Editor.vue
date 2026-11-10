<template>
  <div class="editor" v-loading="loading">
    <div class="topbar">
      <el-button link @click="back">← 审核</el-button>
      <b>{{ name }} · #{{ rank }} 成片编辑</b>
      <span class="muted">高峰 {{ hms(info.t) }}，片段 {{ fmt(info.clipLen) }}</span>
      <span class="spacer" />
      <el-tag v-if="dirty" type="warning" size="small">未保存</el-tag>
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

    <div v-if="!loading && !info.clip" class="empty-all">
      这个片段还没剪，编辑需要先有审核片段做预览。
      <el-button type="primary" :loading="cutting" @click="cutClip">剪切这个片段（480p）</el-button>
    </div>

    <div v-else class="body">
      <section class="left">
        <div ref="stage" class="stage" @pointerdown.self="selected = null">
          <video ref="video" :src="info.clip ? api.clipUrl(name, info.clip) : ''" :style="cameraStyle" preload="auto"
                 @loadedmetadata="onMeta" @timeupdate="syncTime" @play="playing = true; tick()" @pause="playing = false" />
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
const info = reactive({ clipLen: 60, clip: null, t: 0, finals: [] })
const edit = reactive({ trim: { start: 0, end: 60 }, subtitles: { enabled: true }, items: [] })
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
  edit.trim.start = e.trim.start
  edit.trim.end = e.trim.end
  edit.subtitles.enabled = e.subtitles ? e.subtitles.enabled !== false : true
  styleId.value = (e.subtitles && e.subtitles.style_id) || null
  edit.items.splice(0, edit.items.length, ...(e.items || []).map((i) => ({ ...i, id: i.id || newId() })))
}

async function load() {
  loading.value = true
  loaded = false
  try {
    const r = await api.getEdit(name.value, rank.value)
    Object.assign(info, { clipLen: r.clipLen, clip: r.clip, t: r.t, finals: r.finals || [] })
    apply(r.edit)
    styles.value = await api.styles()
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
    const body = { version: 1, trim: { ...edit.trim }, subtitles: { enabled: edit.subtitles.enabled }, items: edit.items }
    const norm = await api.saveEdit(name.value, rank.value, body, edit.subtitles.enabled ? styleId.value : null)
    loaded = false
    const sel = selected.value
    apply(norm)
    selected.value = sel
    await nextTick()
    dirty.value = false
    loaded = true
    if (!quiet) ElMessage.success('已保存')
    return true
  } finally {
    saving.value = false
  }
}

async function render(height) {
  if (dirty.value) await save(true)
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
</style>
