<template>
  <div class="page" v-loading="loading">
    <div class="toolbar">
      <span class="muted">贴图库（素材库）：成片编辑器里「+ 贴图」从这里选。AI 编排只用「有含义说明」的贴图，没有说明的先补上。</span>
    </div>

    <el-tabs v-model="tab">
      <el-tab-pane label="我的贴图" name="mine">
        <div class="toolbar">
          <el-upload :show-file-list="false" accept=".png,.jpg,.jpeg,.gif,.webp" :http-request="upload">
            <el-button type="primary">上传图片</el-button>
          </el-upload>
          <span class="muted">PNG（透明背景最好）/ JPG / GIF / WEBP，最大 5 MB。只用有授权或可商用的素材；网络来的带 net_ 前缀，商用前换掉。</span>
        </div>
        <div v-if="!stickers.length" class="muted">贴图库是空的：先上传，或到「网络搜索」里找。</div>
        <div class="gallery">
          <div v-for="s in stickers" :key="s.name" class="cell">
            <img :src="api.stickerUrl(s.name)" :alt="s.name" loading="lazy" />
            <div class="nm">{{ s.name }}</div>
            <div class="desc" :class="{ no: !desc(s.name) }">{{ desc(s.name) || '（无含义说明，AI 编排不会用）' }}</div>
            <div v-if="boundMemes(s.name).length" class="bound">绑定：{{ boundMemes(s.name).join('、') }}</div>
            <div class="ops">
              <el-button link size="small" type="primary" @click="editMeta(s.name)">含义</el-button>
              <el-button link size="small" type="danger" @click="del(s.name)">删除</el-button>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane label="网络搜索" name="search">
        <div class="toolbar">
          <el-input v-model="searchQ" placeholder="搜梗图：破防、问号、惊讶、大笑…" style="width: 240px" clearable @keyup.enter="doSearch" />
          <el-button type="primary" :loading="searching" @click="doSearch">搜索</el-button>
          <span class="muted">来自斗图吧，仅内部测试用；下载后文件名带 net_ 前缀，记得补含义说明。</span>
        </div>
        <div v-if="searchQ && !searchResults.length && !searching" class="muted">没搜到，换个词试试。</div>
        <div class="gallery">
          <div v-for="(u, i) in searchResults" :key="i" class="cell">
            <img :src="u" :alt="`${searchQ}${i}`" loading="lazy" />
            <el-button size="small" type="primary" class="add" @click="addNet(u, i)">添加</el-button>
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="metaDlg.show" :title="`贴图含义：${metaDlg.name}`" width="520px">
      <el-form label-width="70px">
        <el-form-item label="含义">
          <el-input v-model="metaDlg.meaning" maxlength="80" show-word-limit placeholder="画的是什么、表达什么情绪（必填，AI 编排靠它决定什么时候用）" />
        </el-form-item>
        <el-form-item label="适合">
          <el-input v-model="metaDlg.mood" maxlength="60" placeholder="选填：适合的情绪 / 场景，如「震惊、无语、吐槽」" />
        </el-form-item>
        <el-form-item label="不适合">
          <el-input v-model="metaDlg.avoid" maxlength="60" placeholder="选填：不适合的场合" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="metaDlg.show = false">取消</el-button>
        <el-button type="primary" :disabled="!metaDlg.meaning.trim()" @click="saveMeta">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'

const loading = ref(true)
const tab = ref('mine')
const stickers = ref([])
const meta = ref({})
const memes = ref([])
const searchQ = ref('')
const searchResults = ref([])
const searching = ref(false)
const metaDlg = reactive({ show: false, name: '', meaning: '', mood: '', avoid: '' })

// 贴图文件名 → 绑定了它的梗列表（反向关联，方便删除前判断影响）
const stickerToMemes = computed(() => {
  const map = {}
  for (const m of memes.value) {
    if (m.sticker) {
      ;(map[m.sticker] ||= []).push(m.text)
    }
  }
  return map
})

function boundMemes(name) {
  return stickerToMemes.value[name] || []
}

function desc(name) {
  const m = meta.value[name]
  return m && m.meaning ? m.meaning + (m.mood ? ` · 适合：${m.mood}` : '') : ''
}

async function load() {
  loading.value = true
  try {
    stickers.value = await api.stickers()
    meta.value = await api.stickerMeta()
    memes.value = ((await api.memes()) || {}).memes || []
  } finally {
    loading.value = false
  }
}

async function upload({ file }) {
  const r = await api.uploadSticker(file)
  ElMessage.success(`已上传 ${r.name}，记得补含义说明`)
  await load()
}

async function del(name) {
  await ElMessageBox.confirm(`从贴图库删除 ${name}？已经用了它的片段渲染时会报错。`, '删除贴图', { type: 'warning' })
  await api.deleteSticker(name)
  await load()
}

async function doSearch() {
  const q = searchQ.value.trim()
  if (!q) return
  searching.value = true
  searchResults.value = []
  try {
    searchResults.value = await api.searchSticker(q)
    if (!searchResults.value.length) ElMessage.info('没搜到，换个词试试')
  } finally {
    searching.value = false
  }
}

async function addNet(url, i) {
  const name = `net_${searchQ.value.trim()}_${String(i + 1).padStart(2, '0')}`
  const r = await api.fetchSticker(url, name)
  ElMessage.success(`已添加 ${r.name}，记得补含义说明`)
  await load()
}

function editMeta(name) {
  const m = meta.value[name] || {}
  Object.assign(metaDlg, { show: true, name, meaning: m.meaning || '', mood: m.mood || '', avoid: m.avoid || '' })
}

async function saveMeta() {
  await api.saveStickerMeta(metaDlg.name, metaDlg.meaning.trim(), metaDlg.mood.trim(), metaDlg.avoid.trim())
  metaDlg.show = false
  ElMessage.success('已保存')
  await load()
}

onMounted(load)
</script>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
.gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 12px; }
.cell { border: 1px solid var(--el-border-color-light); border-radius: 6px; padding: 8px; text-align: center; }
.cell img { max-width: 100%; height: 100px; object-fit: contain; background: repeating-conic-gradient(#eee 0 25%, #fff 0 50%) 0 0 / 12px 12px; }
.cell .nm { font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: 4px; }
.cell .desc { font-size: 12px; color: var(--el-text-color-secondary); min-height: 32px; margin-top: 2px; }
.cell .desc.no { color: var(--el-color-warning); }
.cell .bound { font-size: 12px; color: var(--el-color-primary); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cell .ops { margin-top: 4px; }
.cell .add { margin-top: 4px; width: 100%; }
.muted { color: var(--el-text-color-secondary); font-size: 13px; }
</style>
