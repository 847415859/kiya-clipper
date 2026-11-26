#!/usr/bin/env node
/** Mock-only browser acceptance. Never contacts a real backend and never reads .env.
 * npm exec --yes --package=playwright -- node admin/tests/acceptance-browser.mjs
 * If npm does not expose its package on PATH, set PLAYWRIGHT_MODULE to playwright's index.mjs.
 * Optional CHROME_PATH, ACCEPTANCE_PORT (default 5198).
 */
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, resolve, join, delimiter } from 'node:path'
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { spawnSync } from 'node:child_process'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const admin = join(root, 'admin')
const evidence = join(root, 'docs/acceptance-evidence')
mkdirSync(evidence, { recursive: true })
const require = createRequire(join(admin, 'package.json'))
async function loadPlaywright() {
  const candidates = [process.env.PLAYWRIGHT_MODULE, ...['playwright', 'playwright-core'].map(n => { try { return require.resolve(n) } catch { return null } }), ...process.env.PATH.split(delimiter).map(p => join(p, '../playwright/index.mjs'))].filter(Boolean)
  for (const p of candidates) if (existsSync(p)) return import(pathToFileURL(resolve(p)).href)
  throw new Error('Playwright missing. Run npm exec --yes --package=playwright -- node admin/tests/acceptance-browser.mjs, or set PLAYWRIGHT_MODULE to its index.mjs. No project dependencies are modified.')
}
const { chromium } = await loadPlaywright()
const viteModule = await import(pathToFileURL(require.resolve('vite')).href)
const { createServer } = viteModule.default || viteModule
const vueModule = await import(pathToFileURL(require.resolve('@vitejs/plugin-vue')).href)
const vue = vueModule.default || vueModule
const mp4 = join(evidence, 'mock-qualified.mp4')
const ffmpeg = spawnSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=24', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100', '-t', '6', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', mp4], { stdio: 'inherit' })
assert.equal(ffmpeg.status, 0, 'ffmpeg must generate a six-second mock MP4')
const media = readFileSync(mp4)
const emptyEnvDir = mkdtempSync(join(tmpdir(), 'acceptance-no-env-'))
const server = await createServer({ root: admin, configFile: false, envDir: emptyEnvDir, plugins: [vue()], server: { host: '127.0.0.1', port: Number(process.env.ACCEPTANCE_PORT || 5198), strictPort: true }, clearScreen: false })
const report = { kind: 'SIMULATED / MOCK API ONLY — not backend verification', generatedAt: new Date().toISOString(), assertions: [], requests: [], screenshots: [], browserErrors: [], unexpectedApi: [] }
let browser
let scenario = 'unknown'
let failRefresh = false
const now = '2026-11-26T10:00:00Z'
const rows = [
  { name: '模拟-已入队', status: 'queued', jobId: 'mock-queue-1', message: '模拟：已记录下播，Worker 尚未领取', currentStep: 'queue', clipCount: 0 },
  { name: '模拟-等待预算', status: 'waiting_for_budget', jobId: 'mock-budget-1', message: '模拟：当日预算已耗尽', currentStep: 'produce', clipCount: 0 },
  { name: '模拟-处理失败', status: 'failed', jobId: 'mock-failed-1', message: '模拟：渲染失败，保留失败信息', currentStep: 'render-v1', clipCount: 0 },
  { name: '模拟-合格MP4', status: 'has_clips', jobId: 'mock-clips-1', message: '模拟：审片与技术质检均通过，生成合格 MP4', currentStep: 'inventory', clipCount: 1, totalCandidates: 1, completedCandidates: 1 }
].map(r => ({ ...r, updatedAt: now }))
function overview() { return { observed: scenario !== 'unknown', live: scenario === 'live', apiFailures: scenario === 'stale' ? 2 : 0, lastCheck: scenario === 'unknown' ? null : now, unmatched: 0, sessions: ['states', 'stale'].includes(scenario) ? rows : [] } }
function detail(name) {
  const row = rows.find(r => r.name === name)
  if (!row) return null
  return { ...row, room: 'mock-room', budget: { day: '2026-11-26', remainingFen: row.status === 'waiting_for_budget' ? 0 : 800 }, monitor: { observed: true, live: false }, match: { endedCount: 1, jobId: row.jobId, unique: true }, timeline: [{ at: now, stage: 'queue', status: 'done' }, ...(row.status === 'queued' ? [] : [{ at: now, stage: row.currentStep, status: row.status === 'failed' ? 'failed' : row.status === 'waiting_for_budget' ? 'paused' : 'inventoried', reason: row.message }])], candidates: row.status === 'has_clips' ? [{ rank: 1, status: 'inventoried', version: 'mock-v1', stage: 'inventory', playable: true, review: { status: 'pass', bound: true, timeline: 'output', range: [0, 6] }, quality: { status: 'pass', bound: true, data: { width: 640, height: 360, durationSeconds: 6 } }, evidence: { status: 'pass' }, versions: [{ version: 'mock-v1', current: true }, { version: 'mock-v0', current: false }], attempts: [{ stage: 'render-v1', count: 1, actualCostFen: 0 }], events: [{ stage: 'inventory', status: 'done', reason: '模拟入库事件' }], findings: [] }] : [] }
}
function check(name, value = true) { assert.ok(value, name); report.assertions.push({ name, passed: true }) }
try {
  await server.listen()
  const chrome = process.env.CHROME_PATH || (process.platform === 'win32' && existsSync('C:/Program Files/Google/Chrome/Application/chrome.exe') ? 'C:/Program Files/Google/Chrome/Application/chrome.exe' : undefined)
  browser = await chromium.launch({ headless: true, ...(chrome ? { executablePath: chrome } : {}) })
  report.browserVersion = browser.version()
  const page = await browser.newPage({ viewport: { width: 1440, height: 1080 }, timezoneId: 'Asia/Shanghai' })
  page.on('pageerror', e => report.browserErrors.push(e.message))
  await page.route('**/api/**', async route => {
    const request = route.request(); const path = decodeURIComponent(new URL(request.url()).pathname)
    report.requests.push({ scenario, method: request.method(), path, range: request.headers().range || null })
    if (request.method() !== 'GET') { report.unexpectedApi.push(path); return route.fulfill({ status: 405, json: { error: 'Mock is GET-only' } }) }
    if (path === '/api/auth/me') return route.fulfill({ json: { authenticated: true, username: '模拟验收（Mock）' } })
    if (path === '/api/streamers') return route.fulfill({ json: { current: 'mock', streamers: [{ id: 'mock', name: '模拟主播（非后端）' }] } })
    if (['/api/sessions/acceptance-operations', '/api/sessions/production-acceptance'].includes(path)) return route.fulfill(failRefresh ? { status: 503, json: { error: '模拟刷新失败（503）' } } : { json: overview() })
    const match = path.match(/^\/api\/sessions\/([^/]+)\/(?:acceptance-operations|production-acceptance|operations)(?:\/media\/(\d+))?$/)
    if (match) {
      if (!match[2]) return route.fulfill({ json: { ...detail(match[1]), source: path.includes('production') ? 'production' : 'acceptance', versions: [{ version: 'mock-v1', current: true }], events: [{ stage: 'inventory', status: 'done', reason: '模拟事件' }], attempts: [{ stage: 'produce', count: 1, actualCostFen: 0 }] } })
      const range = request.headers().range?.match(/^bytes=(\d+)-(\d*)$/)
      const start = range ? Number(range[1]) : 0; const end = range?.[2] ? Math.min(Number(range[2]), media.length - 1) : media.length - 1
      return route.fulfill({ status: range ? 206 : 200, headers: { 'Content-Type': 'video/mp4', 'Accept-Ranges': 'bytes', 'Content-Length': String(end - start + 1), ...(range ? { 'Content-Range': `bytes ${start}-${end}/${media.length}` } : {}) }, body: media.subarray(start, end + 1) })
    }
    report.unexpectedApi.push(path)
    return route.fulfill({ status: 404, json: { error: `Unexpected mock API: ${path}` } })
  })
  const url = `http://127.0.0.1:${process.env.ACCEPTANCE_PORT || 5198}/#/sessions/acceptance`
  async function screenshot(name) {
    await page.evaluate(() => { let badge = document.querySelector('#mock-evidence-label'); if (!badge) { badge = document.createElement('div'); badge.id = 'mock-evidence-label'; document.body.append(badge) } badge.textContent = '模拟验收 / MOCK API + FFmpeg fixture / 非后端真实验证'; badge.style.cssText = 'position:fixed;bottom:8px;left:8px;z-index:99999;background:#7c2d12;color:white;padding:8px 14px;font:14px sans-serif;border-radius:4px;pointer-events:none' })
    const file = `${name}.png`; await page.screenshot({ path: join(evidence, file), fullPage: true }); report.screenshots.push(file)
  }
  async function visit(state, expected) { scenario = state; await page.goto(url); await page.reload(); await page.getByText(expected, { exact: true }).first().waitFor(); await page.locator('.el-loading-mask').waitFor({ state: 'hidden' }); }
  await visit('unknown', '等待监控记录'); check('No observation is unknown, not inferred offline'); await screenshot('01-mock-monitor-unknown')
  await visit('live', '已观察到直播，等待下播'); check('Live observation waits for end event'); await screenshot('02-mock-monitor-live')
  await visit('stale', '监控状态可能已过期'); check('Monitor API failures show stale-state warning'); await screenshot('03-mock-monitor-stale')
  await visit('states', '模拟-合格MP4'); check('Queue, budget, failure and qualified clip rows render', await page.locator('.session-row').count() === 4); await screenshot('04-mock-session-states')
  failRefresh = true
  await page.getByRole('button', { name: '刷新验收' }).click()
  await page.getByText('刷新失败，仍显示上次成功结果，状态可能已过期', { exact: true }).waitFor()
  check('Refresh 503 retains previous four session rows', await page.locator('.session-row').count() === 4)
  await screenshot('05-mock-refresh-failure-retained'); failRefresh = false
  for (const row of rows) {
    await page.locator('.session-row').filter({ hasText: row.name }).getByRole('button', { name: '查看详情' }).click()
    await page.locator('.el-drawer .detail-conclusion').getByText(row.message, { exact: true }).waitFor()
    check(`Detail opens: ${row.status}`)
    if (row.status === 'waiting_for_budget') { check('Budget detail shows zero balance', (await page.locator('.el-drawer').innerText()).includes('剩余 ¥0.00')); await screenshot('06-mock-budget-detail') }
    if (row.status === 'failed') await screenshot('07-mock-failed-detail')
    if (row.status === 'has_clips') {
      await page.getByText(/候选 #1/).first().click()
      const video = page.locator('video')
      await video.waitFor({ state: 'visible' })
      await page.waitForFunction(() => document.querySelector('video')?.readyState >= 1)
      const playback = await video.evaluate(async v => { v.muted = true; await v.play(); await new Promise(r => setTimeout(r, 300)); v.pause(); await new Promise((resolve, reject) => { v.addEventListener('seeked', resolve, { once: true }); v.addEventListener('error', () => reject(new Error('Media decode failed')), { once: true }); v.currentTime = 3 }); return { duration: v.duration, currentTime: v.currentTime, readyState: v.readyState, error: v.error?.message || null } })
      check('FFmpeg mock MP4 plays and seeks to 3 seconds', playback.duration >= 5.9 && Math.abs(playback.currentTime - 3) < 0.15 && !playback.error)
      report.playback = playback
      await screenshot('08-mock-qualified-mp4-seek')
    }
    await page.locator('.el-drawer__close').click()
    await page.locator('.el-drawer').waitFor({ state: 'hidden' })
  }
  await page.getByText('生产队列', { exact: true }).click()
  await page.waitForResponse(r => new URL(r.url()).pathname === '/api/sessions/production-acceptance')
  await page.locator('.session-row').filter({ hasText: '模拟-合格MP4' }).getByRole('button', { name: '查看详情' }).click()
  await page.locator('.el-drawer .detail-conclusion').getByText(rows[3].message, { exact: true }).waitFor()
  check('Production source uses read-only production detail endpoint', report.requests.some(r => r.path.includes('/模拟-合格MP4/production-acceptance')))
  await screenshot('09-mock-production-detail')
  check('All API requests remain GET-only', report.requests.every(r => r.method === 'GET'))
  check('No unknown API escaped mock contract', report.unexpectedApi.length === 0)
  check('No browser page errors', report.browserErrors.length === 0)
  check('Mock media endpoint requested', report.requests.some(r => r.path.endsWith('/media/1')))
  report.success = true
} catch (error) {
  report.success = false; report.failure = error.stack; process.exitCode = 1
} finally {
  await browser?.close(); await server.close(); rmSync(emptyEnvDir, { recursive: true, force: true })
  writeFileSync(join(evidence, 'mock-browser-report.json'), JSON.stringify(report, null, 2) + '\n')
  console.log(JSON.stringify(report, null, 2))
}
