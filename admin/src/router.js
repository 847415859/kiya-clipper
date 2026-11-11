import { createRouter, createWebHashHistory } from 'vue-router'
import { ensureAuth } from './auth'
import { api } from './api'
import Layout from './views/Layout.vue'
import Login from './views/Login.vue'
import Sessions from './views/Sessions.vue'
import Review from './views/Review.vue'
import Jobs from './views/Jobs.vue'
import Styles from './views/Styles.vue'
import Eval from './views/Eval.vue'
import Settings from './views/Settings.vue'
import Editor from './views/Editor.vue'
import Memory from './views/Memory.vue'
import Weekly from './views/Weekly.vue'
import Memes from './views/Memes.vue'
import Stickers from './views/Stickers.vue'

// hash 路由：刷新页面不需要后端做路由回退
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/login', component: Login, meta: { public: true } },
    {
      path: '/',
      component: Layout,
      children: [
        { path: '', redirect: '/sessions' },
        { path: 'sessions', component: Sessions, meta: { title: '场次' } },
        {
          // 顶部菜单“审核”：直接打开最近一场有候选片段的审核页；还没有就回场次页
          path: 'review',
          component: Sessions,
          beforeEnter: async () => {
            try {
              const list = await api.sessions()
              const s = list.find((x) => x.hasCandidates && x.candidateCount > 0)
              return s ? `/sessions/${s.name}/review` : '/sessions'
            } catch (e) {
              return '/sessions'
            }
          }
        },
        { path: 'sessions/:name/review', component: Review, meta: { title: '审核' } },
        { path: 'sessions/:name/edit/:rank', component: Editor, meta: { title: '成片编辑' } },
        { path: 'jobs', component: Jobs, meta: { title: '任务' } },
        { path: 'styles', component: Styles, meta: { title: '字幕样式' } },
        { path: 'eval', component: Eval, meta: { title: '评测' } },
        { path: 'memory', component: Memory, meta: { title: '精排记忆' } },
        { path: 'memes', component: Memes, meta: { title: '梗库' } },
        { path: 'stickers', component: Stickers, meta: { title: '素材库' } },
        { path: 'weekly', component: Weekly, meta: { title: '主播周报' } },
        { path: 'settings', component: Settings, meta: { title: '配置' } }
      ]
    },
    { path: '/:pathMatch(.*)*', redirect: '/sessions' }
  ]
})

router.beforeEach(async (to) => {
  if (to.meta.public) return true
  return (await ensureAuth()) ? true : { path: '/login', query: { next: to.fullPath } }
})

export default router
