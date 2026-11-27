import { test } from 'node:test'
import assert from 'node:assert/strict'
import { subtitleView } from '../src/subtitle-view.js'
const style = { position: 'bottom', margin_h: 30, margin_v: 60, font: 'sans-serif', font_size: 40, color: '#fff', outline: 0, shadow: 0 }
function view(event) { return subtitleView({ enabled: true, style, events: [{ t0: 0, t1: 2, text: '原话', ...event }] }, 1, 1920, 1080) }
test('sentence layout changes only target event and normal layout unchanged', () => {
  const normal = view({})
  assert.equal(normal.style.bottom, 60)
  assert.equal(normal.style.left, 30)
  const moved = view({ display_x: .3, display_y: .4 })
  assert.equal(moved.style.left, 576)
  assert.equal(moved.style.top, 432)
  assert.equal(moved.style.bottom, 'auto')
  assert.equal(moved.style.transform, 'translateX(-50%) translateY(-50%)')
  assert.equal(moved.children[0].children[0].text, '原话')
  assert.equal(style.position, 'bottom')
})
test('one-axis override retains default axis and manual parts have no synthetic karaoke', () => {
  assert.equal(view({ display_x: .5 }).style.bottom, 60)
  assert.equal(view({ display_y: .2 }).style.left, 30)
  const split = subtitleView({ enabled: true, style: { ...style, karaoke: true }, events: [
    { t0: 0, t1: .5, text: '原', manual: true, words: [] }, { t0: .7, t1: 1.5, text: '话', manual: true, words: [] }
  ] }, .6, 1920, 1080)
  assert.equal(split, null)
})
