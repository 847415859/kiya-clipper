<template>
  <div class="page schedule-page" v-loading="loading">
    <ScheduleCalendar ref="calendar" @edit="editCalendarDay" />
    <div class="toolbar">
      <b>直播日程</b>
      <el-button @click="changeWeek(addDays(week, -7))">上一周</el-button>
      <el-date-picker :model-value="week" type="date" value-format="YYYY-MM-DD" :clearable="false" @update:model-value="changeWeek" aria-label="周起始日期" />
      <el-button @click="changeWeek(addDays(week, 7))">下一周</el-button>
      <span>{{ week }} 至 {{ addDays(week, 6) }} · 北京时间</span>
      <el-tag :type="active ? 'success' : 'info'">{{ active ? '已有确认版本用于发布' : '尚未用于发布' }}</el-tag>
      <el-tag v-if="dirty" type="warning">草稿有未保存修改</el-tag>
      <el-tag v-else-if="unconfirmed" type="warning">当前草稿尚未确认</el-tag>
      <el-button :disabled="loading || saving || uploading || extracting" @click="changeWeek(week)">重新读取已保存版本</el-button>
    </div>
    <el-alert type="info" :closable="false" title="这里填写本周计划，与“主播周报”的历史数据统计分开。保存草稿不会改变已确认版本；核对后点击确认，作品简介和视频片尾才会使用。" />
    <el-card shadow="never" class="card">
      <template #header><b>周表图片（可选）</b></template>
      <el-upload :show-file-list="false" :auto-upload="false" accept="image/png,image/jpeg" :on-change="uploadImage" :disabled="uploading || extracting">
        <el-button :loading="uploading">上传 PNG / JPG 周表</el-button>
      </el-upload>
      <div v-if="plan.image_id" class="image-row">
        <a :href="api.scheduleImageUrl(plan.image_id)" target="_blank" rel="noopener"><img :src="api.scheduleImageUrl(plan.image_id)" alt="主播周表原图" /></a>
        <div>
          <el-button :loading="extracting" @click="extractImage">识别图片日程</el-button>
          <el-button @click="plan.image_id = ''">从草稿移除图片</el-button>
          <p class="muted">先选对周再识别。图片识别使用现有视觉模型；结果只作为建议，不会自动保存或发布。</p>
        </div>
      </div>
      <div v-if="suggestion" class="suggestion">
        <b>识别建议 · {{ suggestion.plan.entries.length }} 条，待核对</b>
        <el-alert v-for="(w, i) in suggestion.warnings" :key="i" :title="w" type="warning" :closable="false" />
        <ul><li v-for="r in suggestion.plan.entries" :key="r.id">{{ r.date }} {{ r.start_time || '时间待定' }} {{ r.title }}</li></ul>
        <el-button @click="applySuggestion(false)">追加到草稿表格</el-button>
        <el-button @click="applySuggestion(true)">用识别建议替换草稿表格</el-button>
        <el-button link @click="suggestion = null">放弃建议</el-button>
      </div>
    </el-card>
    <el-card shadow="never" class="card">
      <template #header><b>片尾人物图（可选）</b></template>
      <div class="image-row">
        <img v-if="plan.portrait_id" :src="api.scheduleImageUrl(plan.portrait_id)" alt="片尾人物图" />
        <div><el-upload :show-file-list="false" :auto-upload="false" accept="image/png,image/jpeg" :on-change="uploadPortrait" :disabled="uploading || extracting">
          <el-button :loading="uploading">上传片尾人物图</el-button></el-upload>
          <el-button v-if="plan.portrait_id" link @click="plan.portrait_id = ''">恢复主播默认人物图</el-button>
          <p class="muted">片尾采用紫色手账风格：人物相框、姓名卡和剩余日程。人物图与用于识别的周表分别上传；建议使用竖版、人物居中的图片。未上传时沿用主播配置的图片。</p>
        </div>
      </div>
    </el-card>
    <el-card shadow="never" class="card">
      <template #header><b>本周安排</b><span class="muted"> 每周最多 14 条。开始时间不确定可留空；结束早于开始表示跨午夜。</span></template>
      <el-table :data="plan.entries" empty-text="还没有安排，点击下方添加" row-key="id">
        <el-table-column label="日期" width="155"><template #default="{row}"><el-select v-model="row.date" aria-label="日程日期"><el-option v-for="(d,i) in dates" :key="d" :label="`${d.slice(5)} 周${'一二三四五六日'[i]}`" :value="d" /></el-select></template></el-table-column>
        <el-table-column label="状态" width="115"><template #default="{row}"><el-select v-model="row.status"><el-option label="直播 / 活动" value="planned" /><el-option label="待定" value="tbd" /><el-option label="休息" value="rest" /><el-option label="已取消" value="cancelled" /></el-select></template></el-table-column>
        <el-table-column label="开始" width="125"><template #default="{row}"><el-time-picker v-model="row.start_time" format="HH:mm" value-format="HH:mm" placeholder="待定" :disabled="row.status === 'rest'" style="width:110px" /></template></el-table-column>
        <el-table-column label="结束" width="125"><template #default="{row}"><el-time-picker v-model="row.end_time" format="HH:mm" value-format="HH:mm" placeholder="可不填" :disabled="row.status === 'rest'" style="width:110px" /></template></el-table-column>
        <el-table-column label="分类" width="120"><template #default="{row}"><el-select :model-value="row.category || 'other'" @update:model-value="row.category=$event" aria-label="日程分类"><el-option v-for="c in categories" :key="c.id" :label="c.label" :value="c.id" /></el-select></template></el-table-column>
        <el-table-column label="内容" min-width="220"><template #default="{row}"><el-input v-model="row.title" maxlength="60" aria-label="日程内容" placeholder="例如：20点游戏回，或休息" /></template></el-table-column>
        <el-table-column label="备注（不写入预告）" min-width="200"><template #default="{row}"><el-input v-model="row.note" maxlength="300" /></template></el-table-column>
        <el-table-column width="70"><template #default="{row}"><el-button link type="danger" @click="plan.entries = plan.entries.filter(r=>r.id !== row.id)">删除</el-button></template></el-table-column>
      </el-table>
      <el-button :disabled="plan.entries.length >= 14" @click="plan.entries.push(emptyEntry(week))">添加安排</el-button>
      <p class="muted">已取消的安排不会介绍；今天已经结束的场次会排除。没有结束时间且开始已过的场次不再预告。图片不会整张烧到片尾，片尾只展示剩余的结构化安排。</p>
      <div class="toolbar">
        <el-button :loading="saving" @click="save(false)">保存草稿</el-button>
        <el-checkbox v-model="checked">我已核对这一周的日期、时间和内容</el-checkbox>
        <el-button type="primary" :loading="saving" :disabled="!checked" @click="save(true)">确认并用于发布</el-button>
        <el-button v-if="active" type="warning" plain :disabled="saving" @click="deactivate">暂停使用已确认日程</el-button>
      </div>
      <p v-if="savedDuringTyping" class="muted">已保存提交时的内容，之后的输入仍留在页面，请再次保存。</p>
    </el-card>
    <el-card shadow="never" class="card">
      <template #header><b>现在发布时的剩余安排</b><el-button link @click="refreshPreview">刷新预览</el-button></template>
      <pre>{{ preview?.description_block || preview?.reason || '正在读取…' }}</pre>
      <p class="muted">此处使用已确认版本。每条作品可在发布卡片里选择预计公开时间、关闭日程介绍，并试渲染日程片尾。</p>
    </el-card>
    <SchedulePosters :week="week" :revision="revision" :dirty="dirty" :busy="saving || uploading || extracting || loading" />
  </div>
</template>

<script setup>
import {computed, onMounted, reactive, ref, watch} from 'vue'
import {onBeforeRouteLeave} from 'vue-router'
import {ElMessage, ElMessageBox} from 'element-plus'
import {api} from '../api'
import {addDays, emptyEntry, monday} from '../schedule-tools'
import ScheduleCalendar from '../components/ScheduleCalendar.vue'
import SchedulePosters from '../components/SchedulePosters.vue'
import {categories} from '../schedule-calendar'
const calendar=ref(null)
async function editCalendarDay(day){await changeWeek(day);document.querySelector('.schedule-page > .toolbar')?.scrollIntoView({behavior:'smooth',block:'start'})}
const week = ref(monday()), revision = ref('none'), active = ref(false), dirty = ref(false)
const loading = ref(false), saving = ref(false), uploading = ref(false), extracting = ref(false), checked = ref(false)
const plan = reactive({week_start:week.value,timezone:'Asia/Shanghai',image_id:'',portrait_id:'',entries:[]})
const suggestion = ref(null), preview = ref(null), savedDuringTyping = ref(false)
const confirmedPlan = ref(null)
const publicSignature = value => JSON.stringify({image_id:value?.image_id || '',portrait_id:value?.portrait_id || '',entries:value?.entries || []})
const unconfirmed = computed(()=>plan.entries.length > 0 && (!active.value || publicSignature(plan)!==publicSignature(confirmedPlan.value)))
const dates = computed(()=>Array.from({length:7},(_,i)=>addDays(week.value,i)))
let baseline = '', ready = false, request = 0
const signature = ()=>JSON.stringify(plan)
watch(plan,()=>{if(ready){dirty.value=signature()!==baseline;checked.value=false}}, {deep:true})
async function load(target = week.value) {
  loading.value=true;ready=false;const n=++request
  try {
    const r=await api.schedule(target)
    if(n!==request)return
    week.value=r.week_start;revision.value=r.revision;active.value=r.active;confirmedPlan.value=r.published
    Object.assign(plan,{portrait_id:'',...r.draft});baseline=signature();dirty.value=false;suggestion.value=null;checked.value=false
    await refreshPreview()
  } finally {if(n===request){loading.value=false;ready=true}}
}
async function changeWeek(day) {
  if(saving.value || uploading.value || extracting.value)return
  if(dirty.value) {try{await ElMessageBox.confirm('当前草稿有未保存修改，切换周会丢弃这些输入。','切换周',{confirmButtonText:'切换'})}catch{return}}
  await load(monday(day))
}
async function save(confirm) {
  if(saving.value || (confirm && !checked.value))return
  saving.value=true;const sent=JSON.parse(signature()), submitted=JSON.stringify(sent)
  try {
    const r=await api.saveSchedule(week.value,{plan:sent,expectedRevision:revision.value,confirm})
    revision.value=r.revision;active.value=r.active;confirmedPlan.value=r.published
    if(signature()===submitted){ready=false;Object.assign(plan,r.draft);baseline=signature();dirty.value=false;await Promise.resolve();ready=true;savedDuringTyping.value=false}
    else{baseline=JSON.stringify(r.draft);dirty.value=signature()!==baseline;savedDuringTyping.value=true}
    checked.value=false
    ElMessage.success(confirm?'已确认，后续作品可使用这周日程':'草稿已保存，已确认版本不变')
    refreshPreview()
    calendar.value?.refresh()
  }finally{saving.value=false}
}
async function deactivate(){
  const r=await api.deactivateSchedule(week.value,revision.value);revision.value=r.revision;active.value=false;refreshPreview();calendar.value?.refresh()
}
async function uploadImage(file){
  if(!file.raw)return
  uploading.value=true
  try{const r=await api.uploadScheduleImage(file.raw);plan.image_id=r.image_id;suggestion.value=null}finally{uploading.value=false}
}
async function extractImage(){
  if(!plan.image_id || extracting.value)return
  extracting.value=true;const image=plan.image_id, selectedWeek=week.value
  try{const r=await api.extractSchedule(selectedWeek,image);if(plan.image_id===image && week.value===selectedWeek)suggestion.value=r}finally{extracting.value=false}
}
async function uploadPortrait(file){
  if(!file.raw)return
  uploading.value=true
  try{const r=await api.uploadScheduleImage(file.raw);plan.portrait_id=r.image_id}finally{uploading.value=false}
}
function applySuggestion(replace){
  const rows=JSON.parse(JSON.stringify(suggestion.value.plan.entries))
  if(!replace && plan.entries.length+rows.length>14)return ElMessage.warning('合计超过14条，请删减后追加或选择替换')
  plan.entries=replace?rows:[...plan.entries,...rows];suggestion.value=null
}
async function refreshPreview(){preview.value=await api.schedulePreview({enabled:true,description:''})}
onBeforeRouteLeave(async()=>{
  if(!dirty.value)return true
  try{await ElMessageBox.confirm('日程草稿尚未保存，确定离开？','离开日程',{confirmButtonText:'离开'});return true}catch{return false}
})
onMounted(()=>load())
</script>

<style scoped>
.toolbar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:12px 0}.card{margin-top:16px}
.muted{font-size:12px;color:var(--el-text-color-secondary);line-height:1.7}.image-row{display:flex;align-items:center;gap:20px;margin-top:16px}.image-row img{max-width:320px;max-height:320px;object-fit:contain}.suggestion{padding:16px;background:var(--el-fill-color-light);margin-top:16px}pre{white-space:pre-wrap;font:inherit;line-height:1.8}
</style>
