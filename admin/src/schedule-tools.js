export function shanghaiDay(now = new Date()) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(now).map(p=>[p.type,p.value]))
  return `${parts.year}-${parts.month}-${parts.day}`
}
export function addDays(value, days) {
  const date = new Date(value + 'T00:00:00Z')
  date.setUTCDate(date.getUTCDate() + days)
  return date.toISOString().slice(0,10)
}
export function monday(value = shanghaiDay()) {
  const day = new Date(value + 'T00:00:00Z').getUTCDay()
  return addDays(value, -((day + 6) % 7))
}
export function emptyEntry(date) {
  return {id: Array.from(crypto.getRandomValues(new Uint8Array(8)),b=>b.toString(16).padStart(2,'0')).join(''),date,start_time:'',end_time:'',title:'',note:'',status:'planned'}
}
