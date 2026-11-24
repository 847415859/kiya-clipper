import axios from 'axios'
import { ElMessage } from 'element-plus'

// 会话 Cookie 登录；CSRF：axios 默认读 XSRF-TOKEN Cookie、带 X-XSRF-TOKEN 请求头，与后端约定一致
const http = axios.create({ timeout: 120000 })
let csrfPromise = null
// Read tokens from a same-origin response rather than relying on JavaScript cookie access.
http.interceptors.request.use(async config => {
  if (['get','head','options'].includes((config.method || 'get').toLowerCase()) || !config.url?.startsWith('/api/')) return config
  if (!csrfPromise) csrfPromise = http.get('/api/auth/csrf',{quiet:true}).catch(error=>{csrfPromise=null;throw error})
  const csrf = await csrfPromise
  config.headers.set(csrf.headerName,csrf.token)
  return config
})

http.interceptors.response.use(
  (res) => {
    if (['/api/auth/login','/api/auth/logout'].includes(res.config.url)) csrfPromise=null
    return res.data
  },
  (err) => {
    const status = err.response && err.response.status
    if (status===403) csrfPromise=null
    const quiet = err.config && err.config.quiet
    if (status === 401 && !quiet) {
      if (!location.hash.startsWith('#/login')) location.hash = '#/login'
    } else if (!quiet) {
      let msg = err.message
      const data = err.response && err.response.data
      if (data && data.error) msg = data.error
      ElMessage.error(msg)
    }
    return Promise.reject(err)
  }
)

export const api = {
  pendingAi: (name, rank) => http.get(`/api/sessions/${encodeURIComponent(name)}/edits/${rank}/ai-drafts`),
  adoptAi: (name, rank, body) => http.post(`/api/sessions/${encodeURIComponent(name)}/edits/${rank}/ai-drafts/adopt`, body),
  deleteSchedule: (week,expectedRevision,entry=null)=>http.delete(`/api/schedules/${encodeURIComponent(week)}`,{data:{expectedRevision,entry}}),
  schedulePosters: week => http.get(`/api/schedules/${encodeURIComponent(week)}/posters`),
  generateSchedulePoster: (week,body) => http.post(`/api/schedules/${encodeURIComponent(week)}/posters`,body,{timeout:660000}),
  schedulePosterUrl: (week,id,download=false) => `/api/schedules/${encodeURIComponent(week)}/posters/${encodeURIComponent(id)}.png${download?'?download=true':''}`,
  scheduleCalendar: () => http.get('/api/schedules/calendar'),
  schedule: (weekStart) => http.get('/api/schedules', {params:{weekStart}}),
  saveSchedule: (week, body) => http.put(`/api/schedules/${encodeURIComponent(week)}`, body, {timeout:660000}),
  deactivateSchedule: (week, expectedRevision) => http.post(`/api/schedules/${encodeURIComponent(week)}/deactivate`, {expectedRevision}),
  schedulePreview: (body) => http.post('/api/schedules/preview', body, {quiet:true}),
  uploadScheduleImage: (file) => {const body=new FormData();body.append('file',file);return http.post('/api/schedules/images',body)},
  scheduleImageUrl: (id) => `/api/schedules/images/${encodeURIComponent(id)}`,
  extractSchedule: (week, imageId) => http.post(`/api/schedules/${encodeURIComponent(week)}/extract`,{imageId},{timeout:150000}),
  me: () => http.get('/api/auth/me', { quiet: true }),
  login: (username, password) =>
    http.post('/api/auth/login', new URLSearchParams({ username, password }), { quiet: true }),
  logout: () => http.post('/api/auth/logout'),
  publishAccount: () => http.get('/api/publish-account'),
  publishLoginStart: () => http.post('/api/publish-account/qr'),
  publishLoginPoll: (id) => http.post(`/api/publish-account/qr/${encodeURIComponent(id)}/poll`, null, { quiet: true }),
  publishLoginMethod: (method, body, id = null) => http.post(`/api/publish-account/login/${id ? encodeURIComponent(id) + '/' : ''}${encodeURIComponent(method)}`, body, { quiet: true }),
  publishLoginConfirm: (id, uid) => http.post(`/api/publish-account/qr/${encodeURIComponent(id)}/confirm`, { uid }),
  publishLoginCancel: (id) => http.post(`/api/publish-account/qr/${encodeURIComponent(id)}/cancel`, null, { quiet: true }),
  publishAccountLogout: () => http.post('/api/publish-account/logout'),
  changePassword: (oldPassword, newPassword) => http.post('/api/auth/password', { oldPassword, newPassword }),

  streamers: () => http.get('/api/streamers'),
  switchStreamer: (id) => http.post('/api/streamers/current', { streamer: id }),

  sessions: () => http.get('/api/sessions'),
  scan: () => http.post('/api/sessions/scan', null, { timeout: 200000 }),
  valueStrategy: (name) => http.get(`/api/sessions/${encodeURIComponent(name)}/value-strategy`),
  candidates: (name) => http.get(`/api/sessions/${encodeURIComponent(name)}/candidates`),
  mark: (name, rank, body) => http.put(`/api/sessions/${encodeURIComponent(name)}/candidates/${rank}/mark`, body),
  clipUrl: (name, file) => `/api/sessions/${encodeURIComponent(name)}/clips/${encodeURIComponent(file)}`,
  editPreviewUrl: (name, file) => `/api/sessions/${encodeURIComponent(name)}/edit-previews/${encodeURIComponent(file)}`,
  curveUrl: (name) => `/api/sessions/${encodeURIComponent(name)}/curve`,
  transcriptUrl: (name) => `/api/sessions/${encodeURIComponent(name)}/transcript`,

  jobs: (params) => http.get('/api/jobs', { params }),
  job: (id) => http.get(`/api/jobs/${id}`),
  submitJob: (type, session, params) => http.post('/api/jobs', { type, session, params: params || {} }),
  jobLog: (id, offset) => http.get(`/api/jobs/${id}/log`, { params: { offset } }),
  cancelJob: (id) => http.post(`/api/jobs/${id}/cancel`),

  styleSchema: () => http.get('/api/styles/schema'),
  styles: () => http.get('/api/styles'),
  createStyle: (code, config) => http.post('/api/styles', { code, config }),
  updateStyle: (id, config) => http.put(`/api/styles/${id}`, { config }),
  deleteStyle: (id) => http.delete(`/api/styles/${id}`),
  defaultStyle: (id) => http.post(`/api/styles/${id}/default`),
  previewStyle: (body) => http.post('/api/styles/preview', body, { responseType: 'blob', timeout: 90000, quiet: true }),

  getEdit: (name, rank) => http.get(`/api/sessions/${encodeURIComponent(name)}/edits/${rank}`),
  editSpeech: (name, rank, start, end) => http.get(`/api/sessions/${encodeURIComponent(name)}/edits/${rank}/speech`, { params: { start, end } }),
  saveEdit: (name, rank, edit, styleId, restoredAi = false, expectedRevision) => http.put(`/api/sessions/${encodeURIComponent(name)}/edits/${rank}`, { edit, styleId, restoredAi, expectedRevision }),
  editPlan: (name, rank, edit, styleId) => http.post(`/api/sessions/${encodeURIComponent(name)}/edits/${rank}/preview-plan`, { edit, styleId }, { quiet: true }),
  editRecovery: (name, rank) => http.get(`/api/sessions/${encodeURIComponent(name)}/edits/${rank}/recovery`, { quiet: true }),
  editDraft: (name, rank, client, value) => http.put(`/api/sessions/${encodeURIComponent(name)}/edits/${rank}/drafts/${client}`, value, { quiet: true }),
  renderPreviewUrl: (name, file) => `/api/sessions/${encodeURIComponent(name)}/render-previews/${encodeURIComponent(file)}`,
  chooseVariant: (name, rank, key, expectedRevision) => http.post(`/api/sessions/${encodeURIComponent(name)}/edits/${rank}/variant`, { key, expectedRevision }),
  finalUrl: (name, file) => `/api/sessions/${encodeURIComponent(name)}/finals/${encodeURIComponent(file)}`,
  stickers: () => http.get('/api/stickers'),
  stickerUrl: (n) => `/api/stickers/${encodeURIComponent(n)}`,
  uploadSticker: (file) => {
    const fd = new FormData()
    fd.append('file', file)
    return http.post('/api/stickers', fd)
  },
  deleteSticker: (n) => http.delete(`/api/stickers/${encodeURIComponent(n)}`),
  searchSticker: (q, limit) => http.get('/api/stickers/search', { params: { q, limit: limit || 20 } }),
  fetchSticker: (url, name) => http.post('/api/stickers/fetch', { url, name }),
  stickerMeta: () => http.get('/api/stickers/meta'),
  saveStickerMeta: (name, meaning, mood, avoid) => http.put(`/api/stickers/${encodeURIComponent(name)}/meta`, { meaning, mood, avoid }),

  words: () => http.get('/api/config/words'),
  saveWords: (body) => http.put('/api/config/words', body),
  hotwordsPreview: () => http.get('/api/config/hotwords-preview'),
  evalResults: () => http.get('/api/eval/results'),

  publishPack: (name, rank) => http.get(`/api/sessions/${encodeURIComponent(name)}/publish/${rank}`),
  savePublishPack: (name, rank, body) => http.put(`/api/sessions/${encodeURIComponent(name)}/publish/${rank}`, body),
  publishCover: (name, rank, body) => http.post(`/api/sessions/${encodeURIComponent(name)}/publish/${rank}/cover`, body, { timeout: 330000 }),
  coverPrompt: (name, rank, body) => http.post(`/api/sessions/${encodeURIComponent(name)}/publish/${rank}/cover/prompt`, body),
  coverVersionUrl: (name, rank, version) => `/api/sessions/${encodeURIComponent(name)}/publish/${rank}/cover.jpg?version=${encodeURIComponent(version)}`,
  music: () => http.get('/api/music', { quiet: true }),
  musicUrl: (id) => `/api/music/${encodeURIComponent(id)}/audio`,
  musicBeats: (id) => http.get(`/api/music/${encodeURIComponent(id)}/beats`, { quiet: true }),
  biliInfo: (name, rank) => http.get(`/api/sessions/${encodeURIComponent(name)}/publish/${rank}/bili`),
  regeneratePublish: (name, rank) => http.post(`/api/sessions/${encodeURIComponent(name)}/publish/${rank}/regenerate`),
  coverUrl: (name, rank, v, variant) => `/api/sessions/${encodeURIComponent(name)}/publish/${rank}/cover.jpg?v=${v || 0}${variant ? `&variant=${encodeURIComponent(variant)}` : ''}`,
  memory: () => http.get('/api/memory'),
  memes: () => http.get('/api/memes'),
  memoryAction: (body) => http.post('/api/memory/actions', body),
  weekly: (end, days) => http.get('/api/weekly', { params: { end: end || undefined, days: days || undefined }, timeout: 150000 }),
  weeklyExportUrl: (end, days) => `/api/weekly/export?${new URLSearchParams({ ...(end ? { end } : {}), ...(days ? { days } : {}) })}`
}

export function fmtDuration(sec) {
  if (sec == null) return '—'
  const s = Math.round(sec)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return h ? `${h} 小时 ${m} 分` : `${m} 分钟`
}

export function hms(sec) {
  const s = Math.max(0, Math.round(sec || 0))
  const p = (n) => String(n).padStart(2, '0')
  return `${p(Math.floor(s / 3600))}:${p(Math.floor((s % 3600) / 60))}:${p(s % 60)}`
}

export function fmtTime(t) {
  return t ? String(t).replace('T', ' ').slice(0, 19) : '—'
}

export const STATUS = {
  PENDING: { text: '排队中', type: 'info' },
  RUNNING: { text: '运行中', type: 'warning' },
  SUCCESS: { text: '成功', type: 'success' },
  FAILED: { text: '失败', type: 'danger' },
  CANCELED: { text: '已取消', type: 'info' }
}
