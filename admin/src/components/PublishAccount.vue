<template>
  <el-card shadow="never" class="publish-account">
    <template #header><b>B站发布账号</b> <span class="muted">用于当前主播的作品投稿，与后台登录、录播姬账号分别独立</span></template>
    <div v-loading="loading">
      <div class="account-summary">
        <el-tag :type="current.state === 'logged_in' ? 'success' : current.state === 'expired' ? 'warning' : 'info'">{{ states[current.state] || '尚未检查' }}</el-tag>
        <b v-if="current.account">{{ current.account.name }}</b>
        <span v-if="current.account">UID {{ current.account.uid }}</span>
        <span class="muted" v-if="current.state === 'logged_in'">{{ current.source === 'website' ? '网站扫码登录' : '服务器原有配置' }}</span>
      </div>
      <p v-if="current.message" class="muted">{{ current.message }}</p>
      <el-space wrap style="margin-top:12px">
        <el-button type="primary" :loading="starting" :disabled="busy" @click="start">{{ current.state === 'logged_in' ? '扫码切换账号' : '扫码登录' }}</el-button>
        <el-button :disabled="busy" @click="refresh">检查登录状态</el-button>
        <el-button v-if="current.state !== 'logged_out' && current.state !== ''" :disabled="busy" type="danger" plain @click="logout">停用发布账号</el-button>
        <el-button v-if="current.account" :disabled="busy" link @click="syncUid">同步为切片号统计账号</el-button>
      </el-space>
    </div>
    <div v-if="login" class="qr-panel">
      <template v-if="phase !== 'ready'">
        <img v-if="login.qr_image" :src="login.qr_image" alt="用 B站手机 App 扫码登录" class="qr-image" />
        <p>使用 B站手机 App 扫码，并在手机上确认登录。</p>
        <p class="muted">手机打开本页时，可长按保存二维码，再用 B站扫一扫从相册识别。</p>
        <el-tag :type="phase === 'expired' ? 'warning' : 'info'">{{ phases[phase] || '等待扫码' }}<template v-if="remaining > 0 && phase !== 'expired'"> · {{ remaining }} 秒</template></el-tag>
        <p v-if="pollError" class="error">{{ pollError }}</p>
      </template>
      <template v-else>
        <el-alert title="扫码已完成，请核对发布账号" type="success" :closable="false" />
        <p><b>{{ candidate?.name }}</b> · UID {{ candidate?.uid }}</p>
        <p>确认后，当前主播后续投稿将使用此账号，已发布作品不变。</p>
        <el-button type="primary" :loading="busy" @click="confirm">确认用于发布</el-button>
      </template>
      <el-button :disabled="busy" @click="cancel">取消登录</el-button>
    </div>
    <p class="muted footnote">登录凭证仅保存在服务器，不会展示或下载到浏览器。切换账号前需等待排队和正在上传的投稿任务结束。</p>
  </el-card>
</template>

<script setup>
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'

const states = { logged_in: '登录有效', logged_out: '未登录 / 已停用', expired: '登录已失效', unknown: '暂时无法验证' }
const phases = { waiting: '等待扫码', scanned: '已扫码，等待手机确认', verifying: '扫码已完成，正在验证账号', expired: '二维码已过期' }
const current = ref({ state: '' }), loading = ref(false), starting = ref(false), busy = ref(false)
const login = ref(null), phase = ref('waiting'), candidate = ref(null), remaining = ref(0), pollError = ref('')
let pollTimer, countdown, generation = 0, alive = true
function stop() { clearTimeout(pollTimer); clearInterval(countdown) }
async function refresh() {
  loading.value = true
  const gen = generation
  try { const result = await api.publishAccount(); if (alive && gen === generation) current.value = result }
  finally { if (alive) loading.value = false }
}
async function poll(gen) {
  if (!alive || gen !== generation || !login.value) return
  try {
    const result = await api.publishLoginPoll(login.value.login_id)
    if (!alive || gen !== generation) return
    phase.value = result.state
    pollError.value = result.message || ''
    if (result.expires_at) login.value.expires_at = result.expires_at
    if (result.state === 'ready') { candidate.value = result.account; stop(); return }
    if (result.state === 'expired') { stop(); return }
  } catch (error) {
    if (!alive || gen !== generation) return
    pollError.value = error.response?.data?.error || '暂时无法查询扫码状态，正在重试'
  }
  if (alive && gen === generation && remaining.value > 0) pollTimer = setTimeout(() => poll(gen), 5000)
}
async function start() {
  starting.value = true
  stop()
  const previous = login.value?.login_id
  const gen = ++generation
  if (previous) await api.publishLoginCancel(previous).catch(() => {})
  login.value = null; candidate.value = null; pollError.value = ''; phase.value = 'waiting'
  try {
    const result = await api.publishLoginStart()
    if (!alive || gen !== generation) { api.publishLoginCancel(result.login_id).catch(() => {}); return }
    login.value = result
    const tick = () => {
      remaining.value = Math.max(0, Math.ceil(login.value.expires_at - Date.now() / 1000))
      if (remaining.value === 0 && phase.value !== 'ready') { phase.value = 'expired'; stop() }
    }
    tick(); countdown = setInterval(tick, 1000)
    pollTimer = setTimeout(() => poll(gen), 3500)
  } finally { if (alive) starting.value = false }
}
async function cancel() {
  stop(); ++generation
  const id = login.value?.login_id
  login.value = null; candidate.value = null
  if (id) await api.publishLoginCancel(id).catch(() => {})
}
async function confirm() {
  if (!login.value || !candidate.value) return
  busy.value = true
  try {
    current.value = await api.publishLoginConfirm(login.value.login_id, candidate.value.uid)
    stop(); ++generation; login.value = null; candidate.value = null
    ElMessage.success('已设置为当前主播的发布账号')
  } finally { busy.value = false }
}
async function logout() {
  try { await ElMessageBox.confirm('停用后，当前主播需要重新登录才能投稿。历史作品不受影响，也不会退出手机上的 B站。', '停用发布账号', { type: 'warning' }) } catch { return }
  busy.value = true
  try { current.value = await api.publishAccountLogout(); stop(); ++generation; login.value = null; ElMessage.success('已停用') }
  finally { busy.value = false }
}
async function syncUid() {
  await api.memoryAction({ action: 'myUid', uid: current.value.account.uid })
  ElMessage.success('切片号统计 UID 已同步，可到精排记忆更新数据')
}
onMounted(() => refresh().catch(() => {}))
onBeforeUnmount(() => {
  alive = false; stop(); ++generation
  const id = login.value?.login_id
  if (id) api.publishLoginCancel(id).catch(() => {})
})
</script>

<style scoped>
.account-summary { display:flex; align-items:center; flex-wrap:wrap; gap:10px; }
.qr-panel { margin-top:20px; padding:18px; border:1px solid var(--el-border-color); border-radius:8px; text-align:center; }
.qr-image { width:240px; max-width:100%; image-rendering:pixelated; }
.muted { font-size:13px; color:var(--el-text-color-secondary); }
.error { color:var(--el-color-danger); }
.footnote { margin-bottom:0; line-height:1.7; }
</style>
