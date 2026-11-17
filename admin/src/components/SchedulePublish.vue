<template>
  <div class="schedule-publish">
    <div class="controls"><el-checkbox :model-value="enabled" @update:model-value="$emit('update:enabled',$event)">在简介和视频片尾介绍剩余日程</el-checkbox>
      <router-link to="/schedule">编辑主播日程</router-link></div>
    <div v-if="enabled">
      <div class="controls"><span>预计公开时间（北京时间）</span><el-date-picker :model-value="at" @update:model-value="$emit('update:at',$event || '')" type="datetime" value-format="YYYY-MM-DDTHH:mm" placeholder="留空按渲染时的现在" /></div>
      <p class="hint">仅使用对应周已确认、尚未结束的安排。定时发布请先选好公开时间再渲染；修改时间或日程后，可能需要重新渲染片尾。</p>
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <pre v-else>{{ loading ? '正在计算剩余日程…' : preview?.description_block || preview?.reason }}</pre>
      <el-alert v-if="preview?.overflow" type="error" :title="preview.error" :closable="false" />
      <p class="hint" v-if="preview?.enabled">紫色手账片尾增加约 {{ preview.duration }} 秒，每页 2 条、停留 7 秒，最多展示 8 条；长标题缩略，简介保留完整安排。人物图可在直播日程页上传更换。</p>
      <div class="controls"><el-button :loading="loading" @click="refresh">刷新预览</el-button>
        <el-button :disabled="loading || !preview?.enabled || !!error || preview?.overflow || rendering" @click="$emit('render-tail')">试渲染日程片尾</el-button>
        <el-button :disabled="loading || !!error || preview?.overflow || !preview" @click="copy">复制完整简介（含日程）</el-button></div>
    </div>
  </div>
</template>
<script setup>
import {onBeforeUnmount,ref,watch} from 'vue'
import {ElMessage} from 'element-plus'
import {api} from '../api'
const props=defineProps({enabled:Boolean,at:String,description:String,rendering:Boolean})
defineEmits(['update:enabled','update:at','render-tail'])
const preview=ref(null),error=ref(''),loading=ref(false)
let timer,request=0
async function refresh(){
  const n=++request;loading.value=true;error.value=''
  try{const r=await api.schedulePreview({enabled:props.enabled,at:props.at || '',description:props.description || ''});if(n===request)preview.value=r}
  catch(e){if(n===request){preview.value=null;error.value=e.response?.data?.error || '日程预览失败，请刷新后再试'}}
  finally{if(n===request)loading.value=false}
}
watch(()=>[props.enabled,props.at,props.description],()=>{clearTimeout(timer);++request;preview.value=null;loading.value=true;timer=setTimeout(refresh,350)},{immediate:true})
async function copy(){await navigator.clipboard.writeText(preview.value.description);ElMessage.success('已复制包含日程的完整简介')}
onBeforeUnmount(()=>{clearTimeout(timer);++request})
</script>
<style scoped>
.schedule-publish{border:1px solid var(--el-border-color);border-radius:8px;padding:14px;margin:12px 0}.controls{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.hint{font-size:12px;color:var(--el-text-color-secondary);line-height:1.6}pre{white-space:pre-wrap;font:inherit;line-height:1.6;background:var(--el-fill-color-light);padding:12px}
</style>
