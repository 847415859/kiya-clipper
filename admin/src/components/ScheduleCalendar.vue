<template>
  <section class="calendar" v-loading="loading">
    <div class="heading"><div><span>Schedule</span><h2>活动 / 直播日程汇总</h2></div><el-button @click="refresh">刷新日历</el-button></div>
    <div class="filters"><el-input v-model="query" clearable placeholder="搜索标题、日期或分类" aria-label="搜索日程" style="width:270px" />
      <el-select v-model="category" clearable placeholder="全部分类" style="width:140px"><el-option v-for="c in categories" :key="c.id" :label="c.label" :value="c.id" /></el-select>
      <el-radio-group v-model="source"><el-radio-button value="published">已确认日程</el-radio-button><el-radio-button value="draft">已保存草稿</el-radio-button></el-radio-group><el-button @click="$emit('edit',today)">编辑本周</el-button></div>
    <p class="help">北京时间 · 按周从新到旧排列 · 点击日期编辑。未填写的日期不会自动记为休息。</p>
    <div class="counts"><b>共 {{ count }} 场</b><span v-for="c in categories" :key="c.id" :style="{color:c.color}">{{ c.label }} {{ counts[c.id] || 0 }}</span><span>休息 {{ rest }} 天 · 取消 {{ cancelled }} 条</span></div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" /><el-alert v-for="w in warnings" :key="w" :title="w" type="warning" :closable="false" />
    <div class="scroll"><div class="grid"><div v-for="d in '一二三四五六日'" :key="d" class="weekday">周{{ d }}</div>
      <template v-for="week in visible" :key="week.week_start"><div class="weekbar"><b>{{ week.week_start }} 这一周</b><el-button link type="danger" @click="$emit('delete-week',week)">删除整周</el-button></div><div v-for="(day,i) in week.days" :key="day.date" class="day" :class="{today:day.date===today}">
        <button class="date edit-date" :aria-label="`编辑 ${day.date} 的日程`" @click="$emit('edit',day.date)">{{ day.date.slice(2).replaceAll('-','/') }} 周{{ '一二三四五六日'[i] }} <small v-if="day.date===today">今天</small></button>
        <small v-if="!week.active && source==='published'" class="muted">已暂停用于发布</small>
        <div v-if="!day.entries.length" class="muted">{{ query || category ? '无匹配安排' : '未填写' }}</div>
        <div v-for="e in day.entries" :key="e.id" class="event" :style="eventStyle(e)"><div><span class="badge">{{ e.status==='rest'?'休息':e.status==='cancelled'?'已取消':categoryOf(e).label }}</span> {{ e.status==='rest'?'':e.start_time || '时间待定' }}{{ e.status==='tbd' && e.start_time?' · 待定':'' }}</div><strong>{{ e.title }}</strong><small v-if="e.end_time">至 {{ e.end_time }}{{ e.end_time < e.start_time?'（次日）':'' }}</small><el-button link type="danger" :aria-label="`删除日程 ${e.title}`" @click="$emit('delete-entry',{week:week.week_start,revision:week.revision,entry:e})">删除日程</el-button></div>
      </div></template>
    </div></div>
    <el-empty v-if="!loading && !filtered.length && !error" description="暂无匹配日程，可先编辑本周并保存" />
    <div class="bottom"><span>显示 {{ visible.length }} / {{ filtered.length }} 周 · 统计覆盖全部筛选结果，不含未保存输入</span><el-button v-if="limit < filtered.length" @click="limit+=8">加载更多周</el-button></div>
  </section>
</template>
<script setup>
import {computed,onMounted,ref,watch} from 'vue'
import {api} from '../api'
import {shanghaiDay} from '../schedule-tools'
import {categories,categoryOf,calendarWeeks} from '../schedule-calendar'
defineEmits(['edit','delete-week','delete-entry'])
const weeks=ref([]),warnings=ref([]),error=ref(''),loading=ref(false),query=ref(''),category=ref(''),source=ref('published'),limit=ref(8),today=shanghaiDay()
const filtered=computed(()=>calendarWeeks(weeks.value,source.value,query.value,category.value)),visible=computed(()=>filtered.value.slice(0,limit.value))
const entries=computed(()=>filtered.value.flatMap(w=>w.days.flatMap(d=>d.entries)))
const counts=computed(()=>{const out={};for(const e of entries.value)if(!['rest','cancelled'].includes(e.status)){const id=categoryOf(e).id;out[id]=(out[id]||0)+1}return out})
const count=computed(()=>Object.values(counts.value).reduce((a,b)=>a+b,0)),rest=computed(()=>new Set(entries.value.filter(e=>e.status==='rest').map(e=>e.date)).size),cancelled=computed(()=>entries.value.filter(e=>e.status==='cancelled').length)
watch([source,query,category],()=>limit.value=8)
function eventStyle(e){const color=['rest','cancelled'].includes(e.status)?'#858b96':categoryOf(e).color;return {color,background:color+'14',textDecoration:e.status==='cancelled'?'line-through':'none'}}
let request=0
async function refresh(){const n=++request;loading.value=true;error.value='';try{const r=await api.scheduleCalendar();if(n!==request)return;weeks.value=r.weeks||[];warnings.value=[...(r.warnings||[]),...(r.truncated?['仅加载最近520份周表']:[])]}catch{if(n===request)error.value='日历读取失败，请刷新重试'}finally{if(n===request)loading.value=false}}
defineExpose({refresh});onMounted(refresh)
</script>
<style scoped>
.weekbar{grid-column:1/-1;display:flex;justify-content:space-between;align-items:center;font-size:13px;color:#607779;padding-top:10px}.edit-date{background:none;border:0;padding:0;color:inherit;cursor:pointer;text-align:left}
.calendar{padding:26px;border-radius:18px;background:linear-gradient(135deg,#d9f3eb,#eee7fa);margin:10px 0 24px}.heading{display:flex;justify-content:space-between;align-items:center}.heading span{font-size:42px;font-weight:800;color:#335d58}.heading h2{margin:4px 0 20px;font-size:21px}.filters,.counts{display:flex;gap:14px;align-items:center;flex-wrap:wrap}.help,.bottom{color:#607779;font-size:12px;line-height:1.8}.counts{margin:14px 0}.scroll{overflow:auto}.grid{display:grid;grid-template-columns:repeat(7,minmax(145px,1fr));gap:7px;min-width:1060px}.weekday{text-align:center;color:#607779;padding:8px}.day{font:inherit;text-align:left;border:1px solid #ffffff80;border-radius:8px;min-height:128px;background:#ffffffe0;padding:10px;cursor:pointer;color:#586868}.day:hover{box-shadow:0 0 0 2px #af94c9 inset}.day.today{border:2px solid #9571bd}.date{font-size:13px;margin-bottom:7px}.date small{color:#9470bd}.event{border-radius:5px;padding:7px;margin-top:5px;font-size:12px}.event strong{display:block;font-size:15px;line-height:1.5;overflow-wrap:anywhere}.event small{display:block}.badge{border-radius:20px;padding:1px 6px;background:#ffffffb0}.muted{font-size:12px;color:#97a5a5}.bottom{display:flex;justify-content:space-between;margin-top:16px}
</style>
