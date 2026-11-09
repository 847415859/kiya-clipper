<template>
  <div class="login-wrap">
    <el-card class="login-card" shadow="never">
      <h2>kiya 切片后台</h2>
      <el-form @submit.prevent="submit" label-position="top">
        <el-form-item label="用户名">
          <el-input v-model="username" autocomplete="username" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="password" type="password" show-password autocomplete="current-password" @keyup.enter="submit" />
        </el-form-item>
        <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon style="margin-bottom: 12px" />
        <el-button type="primary" :loading="loading" style="width: 100%" @click="submit">登录</el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { auth } from '../auth'

const router = useRouter()
const route = useRoute()
const username = ref('admin')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function submit() {
  if (!username.value || !password.value) return
  loading.value = true
  error.value = ''
  try {
    await api.me() // 确保拿到 XSRF-TOKEN Cookie
    const r = await api.login(username.value, password.value)
    auth.authenticated = true
    auth.username = r.username
    auth.checked = true
    password.value = ''
    router.replace(route.query.next || '/sessions')
  } catch (e) {
    const d = e.response && e.response.data
    error.value = (d && d.error) || (e.response && e.response.status === 403 ? '页面已过期，请刷新后重试' : '登录失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap { min-height: 100%; display: flex; align-items: center; justify-content: center; background: #f4f5f7; padding: 16px; box-sizing: border-box; }
.login-card { width: 360px; max-width: 100%; }
h2 { margin: 0 0 16px; font-size: 20px; }
</style>
