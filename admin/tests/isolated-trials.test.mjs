import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { canSubmitTrial, isolatedTaskPath, missingText, previewVersion, trialBody, trialLink, trialTasks } from '../src/isolatedTrials.js'

test('submission requires explicit confirmation, readable media and known fixed cap', () => {
  const options = { canSubmit: true, maxCostFen: 0, blockers: [] }
  const candidate = { rank: 1, available: true, blockers: [] }
  assert.equal(canSubmitTrial(options, candidate, true), true)
  for (const patch of [{ maxCostFen: null }, { maxCostFen: -1 }, { maxCostFen: Infinity }, { canSubmit: false }, { blockers: ['disabled'] }]) assert.equal(canSubmitTrial({ ...options, ...patch }, candidate, true), false)
  assert.equal(canSubmitTrial(options, candidate, false), false)
  assert.equal(canSubmitTrial(options, { ...candidate, available: false }, true), false)
  assert.equal(canSubmitTrial(options, { ...candidate, blockers: ['missing media'] }, true), false)
})
test('requests contain identifiers and confirmation only; explicit rerun preserves parent id', () => {
  assert.deepEqual(trialBody(2), { rank: 2, confirmation: true })
  assert.deepEqual(trialBody(2, 'trial-old'), { rank: 2, confirmation: true, rerunTaskId: 'trial-old' })
  assert.equal(isolatedTaskPath('场次/a', 'task?1'), '/api/sessions/%E5%9C%BA%E6%AC%A1%2Fa/isolated-trials/task%3F1')
  assert.deepEqual(trialLink({ taskId: 't', session: 's' }), { path: '/sessions/acceptance', query: { taskId: 't', session: 's' } })
})
test('missing media and task list presentation', () => {
  assert.match(missingText({ missingSegments: ['20–40 秒', '40–60 秒'] }), /缺失 2 段/)
  assert.equal(missingText({ missingSegments: 3 }), '缺失 3 段')
  assert.deepEqual(trialTasks({ tasks: [{ taskId: 't' }] }), [{ taskId: 't' }])
  assert.deepEqual(trialTasks([]), [])
})
test('simulation preview never claims formal qualified inventory', () => {
  const current = { current: true, playable: true, mediaUrl: '/api/sessions/s/isolated-trials/t/media/1/version/hash' }
  const item = { playable: false, versions: [current] }
  assert.equal(previewVersion({ mode: 'fixture' }, item), current)
  assert.equal(previewVersion({ mode: 'live' }, item), null)
  assert.equal(previewVersion({ mode: 'fixture' }, { ...item, playable: true }), null)
  assert.equal(previewVersion({ mode: 'fixture' }, { versions: [{ ...current, playable: false }] }), null)
  assert.equal(previewVersion({ mode: 'fixture' }, { versions: [{ ...current, current: false }] }), null)
})
test('acceptance stays GET-only and task-bound for media/detail with stale-response guards', () => {
  const view = readFileSync(new URL('../src/views/Acceptance.vue', import.meta.url), 'utf8')
  assert.doesNotMatch(view, /api\.(submit|recover|scan|mark|save)|http\.post|fetch\(/)
  assert.match(view, /api\.isolatedTrialDetail\(session, taskId\)/)
  assert.match(view, /api\.isolatedTrialMedia\(name\.value, selectedTaskId\.value/)
  assert.match(view, /request === detailRequestId/)
  assert.match(view, /selectedTaskId\.value = ''/)
  assert.match(view, /route\.query\.taskId/)
})
