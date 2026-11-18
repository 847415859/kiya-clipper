<template>
  <el-card shadow="never" class="publish-account">
    <template #header><b>B站发布账号</b> <span class="muted">管理当前主播的投稿账号</span></template>
    <div v-loading="loading">
      <div class="account-summary">
        <el-tag :type="current.state === 'logged_in' ? 'success' : current.state === 'expired' ? 'warning' : 'info'">{{ states[current.state] || '尚未检查' }}</el-tag>
        <b v-if="current.account">{{ current.account.name }}</b><span v-if="current.account">UID {{ current.account.uid }}</span>
        <span class="muted" v-if="current.state === 'logged_in'">{{ current.source === 'website' ? '网站托管登录' : '服务器原有配置' }}</span>
      </div>
      <p v-if="current.message" class="muted">{{ current.message }}</p>
      <el-space wrap style="margin-top:12px">
        <el-button :disabled="busy" @click="refresh">检查登录状态</el-button>
        <el-button v-if="current.state && current.state !== 'logged_out'" :disabled="busy" type="danger" plain @click="logout">停用发布账号</el-button>
        <el-button v-if="current.account" :disabled="busy" link @click="syncUid">同步为切片号统计账号</el-button>
      </el-space>
    </div>

    <el-tabs v-model="method" @tab-change="changeMethod" class="login-tabs">
      <el-tab-pane label="扫码登录" name="qr">
        <p class="muted">使用 B站 App 扫码。手机打开本页时可长按保存二维码，再从扫一扫相册识别。</p>
        <el-button type="primary" :loading="busy" @click="runMethod('qr')">{{ current.state === 'logged_in' ? '扫码切换账号' : '扫码登录' }}</el-button>
      </el-tab-pane>
      <el-tab-pane label="账号密码" name="password">
        <el-alert v-if="!secure" title="请使用 HTTPS 访问网站后再输入密码" type="warning" :closable="false" />
        <el-form label-width="85px" @submit.prevent="runMethod('password')" style="max-width:520px">
          <el-form-item label="账号"><el-input v-model="form.username" placeholder="手机号、邮箱或用户名" autocomplete="username" maxlength="256" /></el-form-item>
          <el-form-item label="密码"><el-input v-model="form.password" type="password" show-password autocomplete="off" maxlength="256" @keyup.enter="runMethod('password')" /></el-form-item>
          <el-form-item><el-button type="primary" :loading="busy" :disabled="!secure || !form.username || !form.password" @click="runMethod('password')">账号密码登录</el-button></el-form-item>
        </el-form>
      </el-tab-pane>
      <el-tab-pane label="短信登录" name="sms">
        <el-alert v-if="!secure" title="请使用 HTTPS 访问网站后再进行短信登录" type="warning" :closable="false" />
        <el-form label-width="85px" style="max-width:560px" @submit.prevent>
          <el-form-item label="国家代码"><el-input v-model="form.country" inputmode="numeric" maxlength="4" :disabled="smsLocked" placeholder="86" style="max-width:120px" /></el-form-item>
          <el-form-item label="手机号"><el-input v-model="form.phone" inputmode="tel" maxlength="20" :disabled="smsLocked" placeholder="已注册 B站的手机号，不含国家代码" /></el-form-item>
          <el-form-item><el-button :loading="busy" :disabled="!secure || !form.phone || smsCooldown > 0" @click="runMethod('sms-send')">{{ smsCooldown ? smsCooldown + ' 秒后重发' : '发送短信验证码' }}</el-button></el-form-item>
          <el-form-item label="验证码"><el-input v-model="form.code" inputmode="numeric" autocomplete="one-time-code" maxlength="8" placeholder="短信验证码" /></el-form-item>
          <el-form-item><el-button type="primary" :loading="busy" :disabled="!secure || phase !== 'sms_sent' || !form.code" @click="runMethod('sms-code')">短信登录</el-button></el-form-item>
        </el-form>
      </el-tab-pane>
      <el-tab-pane label="浏览器登录" name="browser">
        <p>在 B站官方页面完成登录或授权，返回本页后核对账号。无需读取浏览器中其他网站的数据。</p>
        <el-button type="primary" :loading="busy" @click="runMethod('browser')">获取浏览器授权链接</el-button>
      </el-tab-pane>
      <el-tab-pane label="Cookie 登录" name="cookie">
        <el-alert v-if="!secure" title="请使用 HTTPS 访问网站后再导入 Cookie" type="warning" :closable="false" />
        <p class="muted">支持 Cookie 请求头、浏览器导出的 Cookie JSON、完整 biliup 登录 JSON。普通 Cookie 需包含 SESSDATA 和 bili_jct。</p>
        <el-input v-model="form.cookie" type="textarea" :rows="4" maxlength="65536" autocomplete="off" :class="{ 'masked-cookie': !showCookie }" placeholder="粘贴 Cookie 或登录 JSON；提交后会清空输入" />
        <el-space wrap style="margin-top:10px"><el-checkbox v-model="showCookie">显示输入内容</el-checkbox><label class="file-label">选择 JSON / TXT 文件<input type="file" accept=".json,.txt" @change="importCookie" /></label></el-space>
        <div style="margin-top:12px"><el-button type="primary" :loading="busy" :disabled="!secure || !form.cookie.trim()" @click="runMethod('cookie')">验证 Cookie 并登录</el-button></div>
      </el-tab-pane>
    </el-tabs>

    <p v-if="failure" class="error" role="alert">{{ failure }}</p>
    <div v-if="login" class="login-panel">
      <template v-if="phase === 'ready'">
        <el-alert title="登录已完成，请核对发布账号" type="success" :closable="false" />
        <p><b>{{ candidate?.name }}</b> · UID {{ candidate?.uid }}</p>
        <p>确认后，当前主播后续投稿将使用此账号，已发布作品不变。</p>
        <el-button type="primary" :disabled="remaining === 0" :loading="busy" @click="confirm">确认用于发布</el-button>
        <p v-if="remaining === 0" class="error">确认请求已过期，请重新登录。</p>
      </template>
      <template v-else>
        <img v-if="login.qr_image && method === 'qr'" :src="login.qr_image" alt="用 B站手机 App 扫码登录" class="qr-image" />
        <p v-if="login.login_url"><a :href="login.login_url" target="_blank" rel="noopener noreferrer" class="official-link">打开 B站官方页面完成{{ phase === 'action_required' ? '验证' : '登录' }}</a></p>
        <p v-if="method === 'browser'" class="muted">完成官方页面操作后返回此页，网站会自动查询授权结果。</p>
        <el-tag :type="phase === 'expired' ? 'warning' : 'info'">{{ phases[phase] || phase }}<template v-if="remaining > 0 && phase !== 'expired'"> · {{ remaining }} 秒</template></el-tag>
        <p v-if="login.message" class="muted">{{ login.message }}</p>
        <p v-if="pollError" class="error">{{ pollError }}</p>
        <div v-if="phase === 'captcha_required' && login.captcha" class="captcha">
          <p>请手动完成下方验证，完成后会继续登录或发送短信。</p>
          <iframe :key="login.captcha.challenge" ref="captchaFrame" src="/publish-captcha" sandbox="allow-scripts" title="B站人机验证" @load="sendCaptcha" />
        </div>
      </template>
      <el-button :disabled="busy" @click="cancel">取消登录</el-button>
    </div>
    <p class="muted footnote">密码和短信验证码不保存。凭证按主播保存在服务器，输入内容不写入浏览器持久存储。切换账号前需等待排队和正在上传的投稿任务结束。</p>
  </el-card>
</template>

<script setup>
import { computed, onMounted, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'

const states = { logged_in: '登录有效', logged_out: '未登录 / 已停用', expired: '登录已失效', unknown: '暂时无法验证' }
const phases = { waiting: '等待登录或授权', scanned: '已扫码，等待手机确认', verifying: '正在验证账号', expired: '登录请求已过期', captcha_required: '需要人机验证', sms_sent: '短信已发送，请填写验证码', action_required: '需要额外验证' }
const current = ref({ state: '' }), loading = ref(false), busy = ref(false), method = ref('qr')
const login = ref(null), phase = ref('waiting'), candidate = ref(null), failure = ref(''), pollError = ref('')
const captchaFrame = ref(null), showCookie = ref(false), clock = ref(Date.now()), retryUntil = ref(0)
const form = reactive({ username: '', password: '', country: '86', phone: '', code: '', cookie: '' })
const secure = location.protocol === 'https:' || ['localhost','127.0.0.1','[::1]'].includes(location.hostname)
const remaining = computed(() => login.value ? Math.max(0, Math.ceil(login.value.expires_at - clock.value / 1000)) : 0)
const smsCooldown = computed(() => Math.max(0,Math.ceil((retryUntil.value-clock.value)/1000)))
const smsLocked = computed(() => busy.value || login.value?.method === 'sms' && phase.value !== 'expired')
let pollTimer, countdown, generation = 0, alive = true
function stop() { clearTimeout(pollTimer) }
function clearSecrets() { form.password = ''; form.code = ''; form.cookie = ''; showCookie.value = false }
async function refresh() {
  loading.value = true
  const gen = generation
  try { const result = await api.publishAccount(); if (alive && gen === generation) current.value = result }
  finally { if (alive) loading.value = false }
}
function accept(result, gen) {
  if (!alive || gen !== generation) { if(result.login_id) api.publishLoginCancel(result.login_id).catch(() => {}); return }
  login.value = { ...login.value, ...result }
  phase.value = result.state
  failure.value = ''; pollError.value = ''
  if (result.retry_after) retryUntil.value = Date.now()+result.retry_after*1000
  if (phase.value === 'ready') { candidate.value = result.account; clearSecrets(); stop(); return }
  if (['waiting','scanned','verifying'].includes(phase.value)) pollTimer = setTimeout(() => poll(gen), 4000)
}
async function poll(gen) {
  if (!alive || gen !== generation || !login.value) return
  try {
    const result = await api.publishLoginPoll(login.value.login_id)
    if (!alive || gen !== generation) return
    accept(result,gen)
  } catch (error) {
    if (!alive || gen !== generation) return
    pollError.value = error.response?.data?.error || '暂时无法查询登录状态，正在重试'
    if (remaining.value > 0) pollTimer = setTimeout(() => poll(gen),5000)
  }
}
async function runMethod(action, proof = null) {
  if (busy.value || !secure && ['password','cookie','sms-send','sms-code'].includes(action)) return
  const family = action.startsWith('sms-') ? 'sms' : action
  const continuation = login.value && login.value.method === family && ['password','sms-send','sms-code'].includes(action) && phase.value !== 'expired' && phase.value !== 'ready'
  if (action === 'sms-code' && !continuation) return
  busy.value = true; stop()
  const gen = continuation ? generation : ++generation
  try {
    if (!continuation && login.value) await api.publishLoginCancel(login.value.login_id).catch(() => {})
    if (!continuation) { login.value = null; candidate.value = null }
    let body = {}
    if (action === 'password') body = { username: form.username, password: form.password }
    if (action === 'sms-send') body = { country: form.country, phone: form.phone }
    if (action === 'sms-code') body = { code: form.code }
    if (action === 'cookie') body = { cookie: form.cookie }
    if (proof) body.proof = proof
    const result = action === 'qr' ? await api.publishLoginStart() : await api.publishLoginMethod(action,body,continuation ? login.value.login_id : null)
    accept(result,gen)
    if (action === 'password' && result.state !== 'captcha_required') form.password = ''
  } catch (error) {
    if (alive && gen === generation) failure.value = error.response?.data?.error || '登录请求失败，请稍后重试'
    if (action === 'password') form.password = ''
  } finally {
    if (action === 'cookie') form.cookie = ''
    if (action === 'sms-code') form.code = ''
    if (alive) busy.value = false
  }
}
function sendCaptcha() {
  if (login.value?.captcha && phase.value === 'captcha_required') captchaFrame.value?.contentWindow?.postMessage({type:'publish-captcha-config',...login.value.captcha},'*')
}
function captchaMessage(event) {
  if (event.source !== captchaFrame.value?.contentWindow || event.origin !== 'null' || phase.value !== 'captcha_required') return
  if (event.data?.type === 'publish-captcha-ready') sendCaptcha()
  if (event.data?.type === 'publish-captcha-error') failure.value = '验证组件加载失败，请重试或使用浏览器登录'
  if (event.data?.type === 'publish-captcha-result' && event.data.proof && !busy.value) {
    const proof = event.data.proof
    if (!['challenge','validate','seccode'].every(k => typeof proof[k] === 'string' && proof[k].length <= 512)) return
    runMethod(login.value.method === 'password' ? 'password' : 'sms-send',proof)
  }
}
async function cancel() {
  stop(); ++generation
  const id = login.value?.login_id
  login.value = null; candidate.value = null; clearSecrets(); failure.value = ''
  if (id) await api.publishLoginCancel(id).catch(() => {})
}
async function changeMethod() { await cancel(); form.phone = ''; form.username = '' }
async function importCookie(event) {
  const file = event.target.files?.[0]
  if (!file) return
  if (file.size > 65536) { ElMessage.error('登录文件不能超过 64KB'); event.target.value = ''; return }
  const gen = generation
  try { const value = await file.text(); if (alive && gen === generation && method.value === 'cookie') form.cookie = value }
  catch { ElMessage.error('登录文件无法读取，请重新选择') }
  finally { event.target.value = '' }
}
async function confirm() {
  if (!login.value || !candidate.value) return
  busy.value = true
  try {
    current.value = await api.publishLoginConfirm(login.value.login_id,candidate.value.uid)
    stop(); ++generation; login.value = null; candidate.value = null; clearSecrets()
    ElMessage.success('已设置为当前主播的发布账号')
  } finally { busy.value = false }
}
async function logout() {
  try { await ElMessageBox.confirm('停用后，当前主播需要重新登录才能投稿。历史作品不受影响，也不会退出手机上的 B站。','停用发布账号',{type:'warning'}) } catch { return }
  busy.value = true
  try { current.value = await api.publishAccountLogout(); stop(); ++generation; login.value = null; clearSecrets(); ElMessage.success('已停用') }
  finally { busy.value = false }
}
async function syncUid() { await api.memoryAction({action:'myUid',uid:current.value.account.uid}); ElMessage.success('统计 UID 已同步，可到精排记忆更新数据') }
watch(remaining,value => { if (login.value && value === 0 && phase.value !== 'ready') { phase.value = 'expired'; clearSecrets(); stop() } })
onMounted(() => { countdown = setInterval(() => {clock.value = Date.now()},1000); window.addEventListener('message',captchaMessage); refresh().catch(() => {}) })
onBeforeUnmount(() => { alive = false; stop(); clearInterval(countdown); ++generation; window.removeEventListener('message',captchaMessage); clearSecrets(); if(login.value) api.publishLoginCancel(login.value.login_id).catch(() => {}) })
</script>

<style scoped>
.account-summary { display:flex; align-items:center; flex-wrap:wrap; gap:10px; }
.login-tabs { margin-top:18px; }
.login-panel { margin-top:18px; padding:18px; border:1px solid var(--el-border-color); border-radius:8px; text-align:center; }
.qr-image { width:240px; max-width:100%; image-rendering:pixelated; }
.captcha { max-width:500px; margin:12px auto; }
.captcha iframe { width:100%; height:430px; border:0; }
.official-link { color:var(--el-color-primary); font-weight:600; }
.muted { font-size:13px; color:var(--el-text-color-secondary); }
.error { color:var(--el-color-danger); line-height:1.6; }
.footnote { margin-bottom:0; line-height:1.7; }
.file-label { color:var(--el-color-primary); cursor:pointer; font-size:14px; }
.file-label input { display:none; }
.masked-cookie :deep(textarea) { -webkit-text-security:disc; }
@media(max-width:600px) { :deep(.el-tabs__item) { padding:0 10px; } }
</style>
