<template>
  <el-card shadow="never" class="posters">
    <template #header><b>生成周计划图片 · Seedream</b></template>
    <p class="muted">参考已保存的人物图生成主播风格底图，再准确排入整周日期和安排。不会改变日程确认状态；只有点击生成才会调用生图服务。</p>
    <div class="controls"><el-select v-model="source" style="width:200px" :disabled="generating"><el-option label="已确认日程" value="published"/><el-option label="已保存草稿" value="draft"/></el-select>
      <el-button type="primary" :loading="generating" :disabled="dirty || busy || loading" @click="generate">生成新版本</el-button><el-button :disabled="generating" @click="load">刷新历史</el-button><span v-if="dirty" class="muted">请先保存当前修改</span></div>
    <el-input v-model="supplement" aria-label="周计划生图补充要求" type="textarea" :rows="3" maxlength="1500" show-word-limit placeholder="补充风格：例如粉紫色手账、保留猫耳与发色、加入星星和蝴蝶结" style="margin-top:14px" :disabled="generating" />
    <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-top:12px"/>
    <el-alert v-if="generating" title="正在生成，可能需要几分钟。期间修改日程不会改变本次图片；本次使用点击生成时的已保存版本。" type="info" :closable="false" style="margin-top:12px"/>
    <p class="muted">历史生成记录 · {{ versions.length }} 个版本。修改日程后需生成新版本，旧图保留原内容。生成失败时不会覆盖历史图片。</p>
    <el-alert v-for="w in warnings" :key="w" :title="w" type="warning" :closable="false"/>
    <div class="versions" v-loading="loading"><article v-for="v in versions" :key="v.id">
      <el-image :src="api.schedulePosterUrl(week,v.id)" :preview-src-list="[api.schedulePosterUrl(week,v.id)]" preview-teleported fit="contain" />
      <div><b>{{ new Date(v.created_at*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}) }}</b><el-tag size="small">{{ v.source==='draft'?'草稿快照':'确认版本快照' }}</el-tag></div>
      <p class="muted">{{ v.model }} · {{ v.width }} × {{ v.height }}</p><p class="muted supplement">{{ v.supplement || '默认主播风格' }}</p>
      <a :href="api.schedulePosterUrl(week,v.id,true)">下载 PNG</a>
    </article></div>
    <el-empty v-if="!loading && !versions.length" description="这一周还没有生成过图片" />
  </el-card>
</template>
<script setup>
import {onBeforeUnmount,ref,watch} from 'vue'
import {ElMessage} from 'element-plus'
import {api} from '../api'
const props=defineProps({week:String,revision:String,dirty:Boolean,busy:Boolean})
const source=ref('published'),supplement=ref(''),versions=ref([]),warnings=ref([]),error=ref(''),loading=ref(false),generating=ref(false)
let request=0,alive=true
async function load(){const n=++request;loading.value=true;error.value='';try{const r=await api.schedulePosters(props.week);if(n===request){versions.value=r.versions;warnings.value=r.warnings||[]}}catch(e){if(n===request)error.value=e.response?.data?.error||'历史记录读取失败，请刷新重试'}finally{if(n===request)loading.value=false}}
watch(()=>props.week,()=>{versions.value=[];load()},{immediate:true})
async function generate(){
  if(generating.value || props.dirty || props.busy)return
  generating.value=true;error.value='';const week=props.week
  try{await api.generateSchedulePoster(week,{source:source.value,supplement:supplement.value,expectedRevision:props.revision});if(alive && props.week===week){await load();ElMessage.success('周计划图片已生成，历史版本已保留')}}
  catch(e){if(alive)error.value=e.response?.data?.error||'生成未完成，请刷新历史记录确认结果后再重试'}
  finally{generating.value=false}
}
onBeforeUnmount(()=>{alive=false;++request})
</script>
<style scoped>
.posters{margin-top:16px}.controls{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.muted{font-size:12px;color:var(--el-text-color-secondary);line-height:1.7}.versions{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:16px}.versions article{border:1px solid var(--el-border-color);border-radius:10px;padding:12px}.versions .el-image{width:100%;height:280px;background:#f5f1f7}.versions b{font-size:13px;margin-right:8px}.supplement{white-space:pre-wrap;overflow-wrap:anywhere}
</style>
