<template>
  <el-container class="layout">
    <el-header class="header">
      <div class="brand">kiya 切片后台</div>
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
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { auth } from '../auth'

const route = useRoute()
const router = useRouter()
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
.menu { flex: 1; border-bottom: none; min-width: 0; }
.user { display: flex; align-items: center; gap: 8px; white-space: nowrap; }
.main { padding: 0; background: #fafbfc; }
@media (max-width: 768px) { .brand { display: none; } }
</style>
