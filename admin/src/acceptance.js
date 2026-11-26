// Presentation only: no network calls or queue mutations.
const formatter = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'medium', timeZone: 'Asia/Shanghai' })
export function acceptanceTime(value) {
  if (!value) return '未知'
  if (!/(Z|[+-]\d{2}:?\d{2})$/i.test(String(value))) return `${value}（原记录时间，时区未记录）`
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? '时间格式未知' : `${formatter.format(date)}（北京时间）`
}
export function money(value) { return typeof value === 'number' && Number.isFinite(value) ? `¥${(value / 100).toFixed(2)}` : '未知' }
export function statusLabel(value) {
  return ({ verify: '待核对', not_queued: '已记录下播，暂未入队', queued: '已入队，等待制作', processing: '制作中', waiting_for_budget: '等待预算', waiting_for_resources: '等待资源', needs_review: '候选待复核', no_candidates: '处理完成，无成片', has_clips: '已有合格成片', failed: '处理失败', prepared: '处理完成，无成片' })[value] || '状态未知'
}
export function rangeLabel(range, timeline) {
  return `${timeline === 'source' ? '整场原片秒数' : timeline === 'output' ? '成片秒数' : '时间轴未记录'} ${Array.isArray(range) && range.length === 2 ? range.join('–') : '区间未知'}`
}
export function stageLabel(value) {
  const label = ({ recording: '核对录播', transcribe: '语音转写', screen: '粗筛', rerank: '精排', produce: '候选制作', topic: '选题', inventory: '入库', inputs: '核对输入', 'initial-edit': '生成初稿', queue: '等待领取' })[value]
  return label || (/^evidence-/.test(value || '') ? '补证据' : /^render-/.test(value || '') ? '渲染' : /^review-/.test(value || '') ? '审片' : /^draft-/.test(value || '') ? '草稿' : value || '未知')
}
