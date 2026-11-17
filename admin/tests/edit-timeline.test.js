import test from 'node:test'
import assert from 'node:assert/strict'
import {compactTime, mergeCuts, compareEdits} from '../src/editor-tools.js'
import {subtitleView} from '../../renderer/src/subtitle-view.js'

test('overlapping deletions count once; deleted time collapses to the splice', () => {
  const cuts = mergeCuts([{start: 2, end: 5}, {start: 4, end: 8}], 0, 20)
  assert.deepEqual(cuts, [{start: 2, end: 8}])
  assert.equal(compactTime(6, cuts), 2)
  assert.equal(compactTime(12, cuts), 6)
})
test('subtitle preview preserves inline karaoke and keeps manual text without word highlighting', () => {
  const subs = {enabled: true, style: {font: 'sans-serif', font_size: 60, margin_h: 10, margin_v: 20, karaoke: true, color: '#fff', karaoke_color: '#ff0'},
    events: [{t0: 0, t1: 2, text: '校对字幕', words: []}]}
  let node = subtitleView(subs, 1, 640, 360)
  assert.equal(node.children[0].children[0].text, '校对字幕')
  subs.events[0].words = [[0, 1, '前'], [1, 2, '后']]
  node = subtitleView(subs, 0.5, 640, 360)
  assert.equal(node.children[0].children[0].style.display, 'inline')
  assert.equal(node.children[0].children[0].style.color, '#ff0')
  assert.equal(node.children[0].children[1].style.color, '#fff')
  assert.equal(subtitleView(subs, 3, 640, 360), null)
})
test('AI comparison reports interior cuts and automatic deletion changes', () => {
  const ai = {trim: {start: 0, end: 20}, items: [], subtitles: {enabled: true}}
  const result = compareEdits(ai, {...ai, cuts: [{start: 3, end: 5}], remove_business: false})
  assert.deepEqual(result.settings, ['片中删除', '自动删除营业话'])
})
