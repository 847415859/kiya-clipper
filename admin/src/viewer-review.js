// 客观提醒与精排意见分别呈现；不会把“没有提醒”称为观众验收通过。
export function openingCaption(edit, caption) {
  if (!caption?.trim()) return null
  const { start, end } = edit.trim
  return (edit.items || []).find(i => i.type === 'text' && i.text?.includes(caption.trim()) &&
    i.start >= start && i.start <= start + 1 &&
    Math.min(end, i.start + i.duration) - i.start + 0.001 >= Math.min(2, end - start - 0.2)) || null
}

export function viewerIssues(edit, llm) {
  const issues = []
  const add = (code, text) => issues.push({ code, text })
  if (!llm || llm.error) add('unjudged', '尚无可用精排结论，请人工检查人物关系、起因和反应是否讲清楚。')
  else {
    if (llm.needsContext || llm.standalone === false) add('context', '精排认为路人需要更多前情；补字幕后仍需人工确认能否独立看懂。')
    if (llm.complete === false) add('story', '精排认为铺垫或反应不完整，请检查成片起止。')
    if (llm.asrDoubt) add('asr', '笑点相关原话识别存疑，请听原声核对人名、否定词和谐音。')
    if (llm.hook === '弱' || llm.hook === '无') add('hook', llm.hookNote || '精排认为开头偏弱，请复看前 3 秒，保留理解起因所需的信息。')
    if (llm.contextCaption && !openingCaption(edit, llm.contextCaption)) add('caption', '精排建议的完整前情未在成片开头充分显示。')
  }
  if (edit.subtitles?.enabled === false) add('subtitles', '字幕已关闭，请静音复看是否仍能理解原话。')
  const visible = (edit.items || []).filter(i => i.start < edit.trim.end && i.start + i.duration > edit.trim.start)
  if (visible.some(i => i.type !== 'zoom' && i.y >= 0.78)) add('bottom', '有装饰效果位于画面底部；若字幕也在底部，请检查重叠和遮挡。')
  if (visible.some(i => ['text', 'narration'].includes(i.type) && i.size < 60)) add('small', '部分花字或旁白字号较小，请在手机宽度下检查能否读清。')
  if (edit.music && edit.music.duck === false) add('audio', '背景音乐未开启说话时压低，请带声音复看原话是否清楚。')
  return issues
}

export function evidenceTime(llm, evidence, window) {
  if (!Number.isFinite(llm?.clipStart) || !Number.isFinite(evidence?.t)) return null
  const time = llm.clipStart + evidence.t - window.start
  return time >= 0 && time <= window.end - window.start ? time : null
}

export function makeCaption(edit, caption) {
  const text = caption?.trim()
  if (!text || text.length > 40 || openingCaption(edit, text)) return null
  const duration = Math.round(Math.min(Math.max(3, text.length / 7), edit.trim.end - edit.trim.start - 0.2) * 100) / 100
  return { type: 'text', text, start: Math.round((edit.trim.start + 0.2) * 100) / 100, duration,
    preset: 'fade', x: 0.5, y: 0.12, size: 70, color: '#FFFFFF', stroke: '#202020', stroke_width: 6,
    font: 'Noto Sans CJK SC', rotate: 0 }
}
