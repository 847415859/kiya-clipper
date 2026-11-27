// Version comparisons align content through source anchors, never equal output seconds.
export function outputToSource(seconds, mapping = []) {
  if (!Number.isFinite(seconds)) return null
  const matches = mapping.filter(row => seconds >= row.output_start && seconds < row.output_end)
  if (matches.length !== 1) return null
  return matches[0].source_start + seconds - matches[0].output_start
}
export function sourceToOutput(seconds, mapping = []) {
  if (!Number.isFinite(seconds)) return null
  const matches = mapping.filter(row => seconds >= row.source_start && seconds < row.source_end)
  if (matches.length !== 1) return null
  return matches[0].output_start + seconds - matches[0].source_start
}
export function mappedSeek(seconds, from = [], to = []) {
  const anchor = outputToSource(seconds, from)
  return anchor === null ? null : sourceToOutput(anchor, to)
}
export function editChanges(before = {}, after = {}) {
  const rows = []
  const oldItems = new Map((before.items || []).map(item => [item.id, item]))
  const newItems = new Map((after.items || []).map(item => [item.id, item]))
  for (const [id, old] of oldItems) {
    const next = newItems.get(id)
    if (!next) rows.push({ target: id, action: '移除效果', before: old, after: null })
    else if (JSON.stringify(old) !== JSON.stringify(next)) rows.push({ target: id, action: '调整效果', before: old, after: next })
  }
  for (const [id, next] of newItems) if (!oldItems.has(id)) rows.push({ target: id, action: '新增效果', before: null, after: next })
  for (const key of ['trim', 'cuts', 'subtitles', 'required_evidence']) {
    if (JSON.stringify(before[key]) !== JSON.stringify(after[key])) rows.push({ target: key, action: '调整', before: before[key] ?? null, after: after[key] ?? null })
  }
  return rows
}
export function versionMapping(version) {
  // Backend mapping already includes callback offset; raw receipt fallback applies it once.
  if (Array.isArray(version?.mapping)) return version.mapping
  const receipt = version?.evidence?.data
  return (receipt?.source_to_output || []).map(row => ({ ...row,
    output_start: row.output_start + (receipt.callback_offset || 0),
    output_end: row.output_end + (receipt.callback_offset || 0) }))
}
