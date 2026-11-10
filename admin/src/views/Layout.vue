<template>
  <el-container class="layout">
    <el-header class="header">
      <div class="brand">切片后台</div>
      <el-select v-model="current" size="small" class="streamer" @change="switchStreamer" :title="'当前主播：' + (currentName || current)">
        <el-option v-for="s in streamers" :key="s.id" :label="s.name || s.id" :value="s.id">
          <span>{{ s.name || s.id }}</span><span class="muted" v-if="s.name">（{{ s.id }}）</span>
        </el-option>
      </el-select>
      <el-menu mode="horizontal" :default-active="active" router :ellipsis="true" class="menu">
        <el-menu-item index="/sessions">场次</el-menu-item>
        <el-menu-item index="/review">审核</el-menu-item>
        <el-menu-item index="/jobs">任务</el-menu-item>
        <el-menu-item index="/styles">字幕样式</el-menu-item>
        <el-menu-item index="/eval">评测</el-menu-item>
        <el-menu-item index="/memory">精排记忆</el-menu-item>
        <el-menu-item index="/memes">梗库</el-menu-item>
        <el-menu-item index="/weekly">主播周报</el-menu-item>
        <el-menu-item index="/settings">配置</el-menu-item>
      </el-menu>
      <div class="user">
        <span class="muted">{{ auth.username }}</span>
        <el-button link @click="logout">退出</el-button>
      </div>
    </el-header>
    <el-main class="main"><router-view /></el-main>
  </el-container>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { auth } from '../auth'

const route = useRoute()
const router = useRouter()
const streamers = ref([])
const current = ref('')
const currentName = computed(() => {
  const s = streamers.value.find((x) => x.id === current.value)
  return s ? s.name : ''
})

onMounted(async () => {
  try {
    const r = await api.streamers()
    streamers.value = r.streamers || []
    current.value = r.current
  } catch (e) { /* 加载失败不阻塞页面 */ }
})

async function switchStreamer(id) {
  try {
    await api.switchStreamer(id)
    location.reload()   // 切换后整页刷新，场次 / 审核 / 评测等全部按新主播重新加载
  } catch (e) {
    const r = await api.streamers().catch(() => null)
    current.value = r ? r.current : id
  }
}

const active = computed(() => {
  if (/\/(review|edit)(\/|$)/.test(route.path)) return '/review'
  return '/' + (route.path.split('/')[1] || 'sessions')
})

async function logout() {
  try { await api.logout() } finally {
    auth.authenticated = false
    auth.username = ''
    router.replace('/login')
  }
}
</script>

<style scoped>
.layout { height: 100%; }
.header { display: flex; align-items: center; gap: 16px; border-bottom: 1px solid var(--el-border-color-light); padding: 0 16px; }
.brand { font-weight: 600; white-space: nowrap; }
.streamer { width: 150px; flex-shrink: 0; }
.menu { flex: 1; border-bottom: none; min-width: 0; }
.user { display: flex; align-items: center; gap: 8px; white-space: nowrap; }
.main { padding: 0; background: #fafbfc; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
@media (max-width: 768px) { .brand { display: none; } }
</style>
