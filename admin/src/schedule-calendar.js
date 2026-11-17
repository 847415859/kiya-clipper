import {addDays} from './schedule-tools.js'
export const categories=[['chat','杂谈','#e99216'],['game','游戏','#3988f5'],['watch','观影','#a252ed'],['submission','投稿','#15b66b'],['sing','歌回','#ed529a'],['fitness','健身','#ef5757'],['radio','电台','#8ab524'],['collab','联动','#ed792f'],['special','特殊','#c6a315'],['other','其他','#7c8798']].map(([id,label,color])=>({id,label,color}))
export const categoryOf=row=>categories.find(c=>c.id===row.category)||categories.at(-1)
export function calendarWeeks(weeks,source,query='',category=''){
  const text=query.trim().toLowerCase()
  return weeks.flatMap(week=>{
    const plan=week[source];if(!plan || !plan.entries?.length)return []
    const entries=plan.entries.filter(r=>(!category || (r.category||'other')===category) && (!text || `${r.title} ${r.date} ${categoryOf(r).label}`.toLowerCase().includes(text)))
    if((text||category)&&!entries.length)return []
    return [{...week,days:Array.from({length:7},(_,i)=>{const date=addDays(week.week_start,i);return {date,entries:entries.filter(r=>r.date===date)}})}]
  })
}
