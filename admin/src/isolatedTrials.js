// Pure presentation and request-shaping helpers; paths never come from candidate input.
export function isolatedTaskPath(session, taskId) {
  return `/api/sessions/${encodeURIComponent(session)}/isolated-trials/${encodeURIComponent(taskId)}`
}
export function trialLink(task) {
  return { path: '/sessions/acceptance', query: { taskId: task.taskId, session: task.session } }
}
export function reasonText(reason) {
  return typeof reason === 'string' ? reason : reason?.message || reason?.reason || JSON.stringify(reason)
}
export function missingText(candidate) {
  const missing = candidate?.missingSegments
  if (Array.isArray(missing)) return missing.length ? `缺失 ${missing.length} 段：${missing.map(reasonText).join('；')}` : ''
  return missing ? `缺失 ${missing} 段` : ''
}
export function canSubmitTrial(options, candidate, confirmed) {
  return options?.canSubmit === true && candidate?.available === true && confirmed === true &&
    Number.isFinite(options.maxCostFen) && options.maxCostFen >= 0 &&
    !(options.blockers?.length) && !(candidate.blockers?.length)
}
export function trialBody(rank, rerunTaskId = '') {
  const body = { rank, confirmation: true }
  if (rerunTaskId) body.rerunTaskId = rerunTaskId
  return body
}
export function trialTasks(result) { return Array.isArray(result) ? result : result?.tasks || [] }
export function previewVersion(detail, item) {
  if (detail?.mode !== 'fixture' || item?.playable) return null
  return item?.versions?.find(version => version.current && version.playable && version.mediaUrl) || null
}
