import { test } from 'node:test'
import assert from 'node:assert/strict'
import { evidenceTime, makeCaption, openingCaption, viewerIssues } from '../src/viewer-review.js'
import { fakeUiView } from '../../renderer/src/fakeui.js'

const edit = () => ({ window: { start: 100, end: 160 }, trim: { start: 5, end: 50 }, subtitles: { enabled: true }, items: [] })

test('old judgments stay reviewable and do not claim audience acceptance', () => {
  assert.equal(viewerIssues(edit(), null)[0].code, 'unjudged')
  assert.deepEqual(viewerIssues(edit(), { standalone: true }), [])
})
test('caption must be complete, visible and readable after trimming', () => {
  const e = edit()
  e.items.push({ type: 'text', text: '她输了赌约', start: 0, duration: 3 })
  assert.equal(openingCaption(e, '她输了赌约'), null)
  e.items.push(makeCaption(e, '她输了赌约'))
  assert.ok(openingCaption(e, '她输了赌约'))
  assert.equal(makeCaption(e, '她输了赌约'), null)
  e.trim.start = 12
  assert.equal(openingCaption(e, '她输了赌约'), null)
})
test('evidence uses original candidate coordinates, not refined start or final zero', () => {
  assert.equal(evidenceTime({ clipStart: 110 }, { t: 2 }, edit().window), 12)
  assert.equal(evidenceTime({ clipStart: 90 }, { t: 2 }, edit().window), null)
  assert.equal(evidenceTime({}, { t: 2 }, edit().window), null)
})
test('cropped out decorations do not trigger current edit warnings', () => {
  const e = edit()
  e.items.push({ type: 'text', start: 0, duration: 1, y: 0.9, size: 30 })
  assert.deepEqual(viewerIssues(e, { standalone: true }), [])
  e.items[0].start = 10
  assert.deepEqual(viewerIssues(e, { standalone: true }).map(x => x.code), ['bottom', 'small'])
})
test('chat effects visibly distinguish editorial fiction from real audience evidence', () => {
  const view = fakeUiView({ kind: 'chat', x: 0.5, y: 0.5, scale: 0.4, title: '路人', text: '哈哈哈' }, 1920, 1080)
  assert.match(JSON.stringify(view), /剪辑演绎/)
})
