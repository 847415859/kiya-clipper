import { test } from 'node:test'
import assert from 'node:assert/strict'
import { outputToSource, sourceToOutput, mappedSeek, editChanges, versionMapping } from '../src/versionCompare.js'
const before = [{ source_start: 100, source_end: 110, output_start: 2, output_end: 12 }]
const after = [{ source_start: 103, source_end: 110, output_start: 0, output_end: 7 }]
test('changed timelines sync same source anchor rather than equal seconds', () => {
  assert.equal(outputToSource(7, before), 105)
  assert.equal(sourceToOutput(105, after), 2)
  assert.equal(mappedSeek(7, before, after), 2)
})
test('removed anchors, callback and unknown/ambiguous mapping disable sync', () => {
  assert.equal(mappedSeek(3, before, after), null)
  assert.equal(mappedSeek(0, before, after), null)
  assert.equal(mappedSeek(7, [], after), null)
  assert.equal(mappedSeek(7, before.concat(before), after), null)
  assert.equal(mappedSeek(Number.NaN, before, after), null)
})
test('receipt callback offset applied once', () => {
  assert.deepEqual(versionMapping({ evidence: { data: { source_to_output: after, callback_offset: 2 } } }),
    [{ source_start: 103, source_end: 110, output_start: 2, output_end: 9 }])
  assert.deepEqual(versionMapping({ mapping: before }), before)
})
test('diff is stable-ID bound and distinguishes removed and changed effects', () => {
  const old = { items: [{ id: 'a', y: .2 }, { id: 'b', scale: 1.5 }], trim: { start: 0, end: 10 } }
  const next = { items: [{ id: 'b', scale: 1.2 }], trim: { start: 1, end: 10 } }
  const rows = editChanges(old, next)
  assert.deepEqual(rows.map(row => row.target), ['a', 'b', 'trim'])
  assert.equal(rows[0].action, '移除效果')
  assert.equal(old.items.length, 2)
})
