<template>
  <div class="page settings">
    <el-card shadow="never">
      <template #header><b>额外热词</b> <span class="muted">语音识别优先认这些词；主播资料里的昵称、粉丝名、梗会自动加入，不用重复填</span></template>
      <el-select v-model="hotwords" multiple filterable allow-create default-first-option :reserve-keyword="false"
                 placeholder="输入后回车添加（2–15 个字）" style="width: 100%" />
      <div class="toolbar" style="margin-top: 12px">
        <el-button @click="previewHotwords">查看最终热词表</el-button>
        <el-button type="warning" plain @click="syncHotwords">同步到百炼</el-button>
        <span class="muted">保存后要“同步到百炼”才对之后的转写生效</span>
      </div>
      <pre v-if="hotwordText" class="log" style="margin-top: 8px">{{ hotwordText }}</pre>
    </el-card>

    <el-card shadow="never">
      <template #header><b>字幕纠错词</b> <span class="muted">语音识别固定认错的词，剪切烧录字幕时自动替换（和 config/subtitles.yaml 里的合并）</span></template>
      <el-table :data="corrections" size="small">
        <el-table-column label="识别成" min-width="160">
          <template #default="{ row }"><el-input v-model="row.from" placeholder="例如：发件礼" /></template>
        </el-table-column>
        <el-table-column label="改成" min-width="160">
          <template #default="{ row }"><el-input v-model="row.to" placeholder="例如：发舰礼" /></template>
        </el-table-column>
        <el-table-column width="70">
          <template #default="{ $index }"><el-button link type="danger" @click="corrections.splice($index, 1)">删除</el-button></template>
        </el-table-column>
      </el-table>
      <el-button style="margin-top: 8px" @click="corrections.push({ from: '', to: '' })">添加一行</el-button>
    </el-card>

    <div class="toolbar">
      <el-button type="primary" :loading="saving" @click="saveWords">保存热词和纠错词</el-button>
    </div>

    <el-card shadow="never">
      <template #header><b>修改密码</b> <span class="muted">后台对公网开放，请用 10 位以上不常用的密码</span></template>
      <el-form label-width="90px" style="max-width: 420px" @submit.prevent>
        <el-form-item label="原密码"><el-input v-model="pwd.old" type="password" show-password autocomplete="current-password" /></el-form-item>
        <el-form-item label="新密码"><el-input v-model="pwd.new1" type="password" show-password autocomplete="new-password" /></el-form-item>
        <el-form-item label="再输一次"><el-input v-model="pwd.new2" type="password" show-password autocomplete="new-password" /></el-form-item>
        <el-form-item><el-button type="primary" @click="changePassword">修改</el-button></el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api'

const router = useRouter()
const hotwords = ref([])
const corrections = ref([])
const hotwordText = ref('')
const saving = ref(false)
const pwd = reactive({ old: '', new1: '', new2: '' })

async function load() {
  const w = await api.words()
  hotwords.value = w.hotwords || []
  corrections.value = Object.entries(w.corrections || {}).map(([from, to]) => ({ from, to }))
}

async function saveWords() {
  const map = {}
  for (const r of corrections.value) {
    if (r.from && r.from.trim()) map[r.from.trim()] = (r.to || '').trim()
  }
  saving.value = true
  try {
    await api.saveWords({ hotwords: hotwords.value, corrections: map })
    ElMessage.success('已保存')
    await load()
  } finally {
    saving.value = false
  }
}

async function previewHotwords() {
  hotwordText.value = (await api.hotwordsPreview()).text
}

async function syncHotwords() {
  const j = await api.submitJob('HOTWORDS_SYNC')
  router.push({ path: '/jobs', query: { open: j.id } })
}

async function changePassword() {
  if (pwd.new1 !== pwd.new2) return ElMessage.error('两次输入的新密码不一样')
  if (pwd.new1.length < 10) return ElMessage.error('新密码至少 10 位')
  await api.changePassword(pwd.old, pwd.new1)
  ElMessage.success('密码已修改')
  Object.assign(pwd, { old: '', new1: '', new2: '' })
}

onMounted(load)
</script>

<style scoped>
.settings { display: flex; flex-direction: column; gap: 16px; max-width: 900px; }
</style>
