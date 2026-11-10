import axios from 'axios'
import { ElMessage } from 'element-plus'

// 会话 Cookie 登录；CSRF：axios 默认读 XSRF-TOKEN Cookie、带 X-XSRF-TOKEN 请求头，与后端约定一致
const http = axios.create({ timeout: 120000 })

http.interceptors.response.use(
  (res) => res.data,
  (err) => {
    const status = err.response && err.response.status
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
  me: () => http.get('/api/auth/me', { quiet: true }),
  login: (username, password) =>
    http.post('/api/auth/login', new URLSearchParams({ username, password }), { quiet: true }),
  logout: () => http.post('/api/auth/logout'),
  changePassword: (oldPassword, newPassword) => http.post('/api/auth/password', { oldPassword, newPassword }),

  streamers: () => http.get('/api/streamers'),
  switchStreamer: (id) => http.post('/api/streamers/current', { streamer: id }),

  sessions: () => http.get('/api/sessions'),
  scan: () => http.post('/api/sessions/scan', null, { timeout: 200000 }),
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
  saveEdit: (name, rank, edit, styleId, restoredAi = false) => http.put(`/api/sessions/${encodeURIComponent(name)}/edits/${rank}`, { edit, styleId, restoredAi }),
  chooseVariant: (name, rank, key) => http.post(`/api/sessions/${encodeURIComponent(name)}/edits/${rank}/variant`, { key }),
  finalUrl: (name, file) => `/api/sessions/${encodeURIComponent(name)}/finals/${encodeURIComponent(file)}`,
  stickers: () => http.get('/api/stickers'),
  stickerUrl: (n) => `/api/stickers/${encodeURIComponent(n)}`,
  uploadSticker: (file) => {
    const fd = new FormData()
    fd.append('file', file)
    return http.post('/api/stickers', fd)
  },
  deleteSticker: (n) => http.delete(`/api/stickers/${encodeURIComponent(n)}`),

  words: () => http.get('/api/config/words'),
  saveWords: (body) => http.put('/api/config/words', body),
  hotwordsPreview: () => http.get('/api/config/hotwords-preview'),
  evalResults: () => http.get('/api/eval/results'),

  publishPack: (name, rank) => http.get(`/api/sessions/${encodeURIComponent(name)}/publish/${rank}`),
  savePublishPack: (name, rank, body) => http.put(`/api/sessions/${encodeURIComponent(name)}/publish/${rank}`, body),
  publishCover: (name, rank, body) => http.post(`/api/sessions/${encodeURIComponent(name)}/publish/${rank}/cover`, body, { timeout: 150000 }),
  regeneratePublish: (name, rank) => http.post(`/api/sessions/${encodeURIComponent(name)}/publish/${rank}/regenerate`),
  coverUrl: (name, rank, v) => `/api/sessions/${encodeURIComponent(name)}/publish/${rank}/cover.jpg?v=${v || 0}`,
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
