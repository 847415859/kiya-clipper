import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 打包到 Spring Boot 的静态目录，后端 jar 里直接带上页面
export default defineConfig({
  plugins: [vue()],
  base: './',
  build: {
    outDir: '../backend/src/main/resources/static',
    emptyOutDir: true,
    chunkSizeWarningLimit: 2000
  },
  server: {
    // 本地开发：npm run dev，接口转发到本机运行的后端
    proxy: { '/api': 'http://127.0.0.1:8080' }
  }
})
