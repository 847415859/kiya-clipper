export const clone = value => JSON.parse(JSON.stringify(value))
export const sentenceKey = s => `${Number(s.t0).toFixed(2)}:${Number(s.t1).toFixed(2)}`

export function mergeCuts(cuts, start, end) {
  const out = []
  for (const q of [...cuts].sort((a, b) => a.start - b.start)) {
    const a = Math.max(start, q.start), b = Math.min(end, q.end)
    if (b <= a) continue
    if (out.length && a <= out.at(-1).end) out.at(-1).end = Math.max(b, out.at(-1).end)
    else out.push({start: a, end: b})
  }
  return out
}
export function compactTime(t, cuts) {
  return t - cuts.reduce((n, q) => n + Math.max(0, Math.min(t, q.end) - q.start), 0)
}

export function sentencePosition(s, window) {
  return { start: s.t0 - window.start, end: s.t1 - window.start,
    complete: s.t0 >= window.start && s.t1 <= window.end }
}

export function updateSentenceOverride(rows, sentence, text) {
  if (typeof text !== 'string' || text.length > 1000) throw new Error('字幕内容最多 1000 字')
  const next = rows.filter(r => sentenceKey(r) !== sentenceKey(sentence))
  if (text.trim() !== sentence.text) next.push({ t0: sentence.t0, t1: sentence.t1, original: sentence.text, text: text.trim() })
  return next.sort((a, b) => a.t0 - b.t0)
}

export function createHistory(limit = 60) {
  let states = [], index = -1
  return {
    reset(value) { states = [clone(value)]; index = 0 },
    record(value) {
      const next = clone(value)
      if (JSON.stringify(states[index]) === JSON.stringify(next)) return false
      states.splice(index + 1)
      states.push(next)
      if (states.length > limit) states.shift()
      index = states.length - 1
      return true
    },
    undo() { return index > 0 ? clone(states[--index]) : null },
    redo() { return index < states.length - 1 ? clone(states[++index]) : null },
    get canUndo() { return index > 0 },
    get canRedo() { return index < states.length - 1 },
  }
}

// 对比的是当前编辑数据，不会切换或覆盖 AI 原稿。优先用稳定 ID，兼容没有 ID 的旧脚本。
export function compareEdits(ai, current, candidateStart = 0) {
  if (!ai?.trim || !current?.trim) return null
  const origin = e => e.window?.start ?? candidateStart
  const absolute = e => (e.items || []).map(i => ({ ...i, start: Math.round((origin(e) + i.start) * 100) / 100 }))
  const before = absolute(ai), after = absolute(current), free = new Set(after.map((_, i) => i)), changes = []
  for (const a of before) {
    let j = [...free].find(j => a.id && a.id === after[j].id && a.type === after[j].type)
    if (j === undefined) j = [...free].filter(j => a.type === after[j].type && Math.abs(a.start - after[j].start) <= 2)
      .sort((x, y) => Math.abs(a.start - after[x].start) - Math.abs(a.start - after[y].start))[0]
    if (j === undefined) { changes.push({ kind: 'removed', before: a }); continue }
    free.delete(j)
    const b = after[j]
    const keys = [...new Set([...Object.keys(a), ...Object.keys(b)])].filter(k => !['id'].includes(k))
    const fields = keys.filter(k => JSON.stringify(a[k]) !== JSON.stringify(b[k]))
    if (fields.length) changes.push({ kind: 'changed', before: a, after: b, fields })
  }
  for (const j of free) changes.push({ kind: 'added', after: after[j] })
  const settings = []
  for (const [key, name] of [['music', '背景音乐'], ['callback', '前情呼应']]) {
    if (JSON.stringify(ai[key] || null) !== JSON.stringify(current[key] || null)) settings.push(name)
  }
  if (ai.subtitles?.enabled !== current.subtitles?.enabled) settings.push('字幕开关')
  if (JSON.stringify(ai.cuts || []) !== JSON.stringify(current.cuts || [])) settings.push('片中删除')
  if ((ai.remove_business !== false) !== (current.remove_business !== false)) settings.push('自动删除营业话')
  return { changes, settings, trim: {
    before: [origin(ai) + ai.trim.start, origin(ai) + ai.trim.end],
    after: [origin(current) + current.trim.start, origin(current) + current.trim.end]
  }, subtitleChanges: (current.subtitles?.sentence_overrides || []).length }
}
