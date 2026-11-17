import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createHistory, compareEdits, sentencePosition, updateSentenceOverride } from '../src/editor-tools.js'

test('history snapshots are independent and new edits replace the redo branch', () => {
  const h = createHistory(3), first = { items: [] }
  h.reset(first); first.items.push('outside mutation')
  h.record({ items: ['a'] }); h.record({ items: ['b'] })
  const previous = h.undo(); assert.deepEqual(previous, { items: ['a'] })
  previous.items.push('outside mutation')
  assert.deepEqual(h.redo(), { items: ['b'] })
  assert.deepEqual(h.undo(), { items: ['a'] })
  h.record({ items: ['c'] }); assert.equal(h.canRedo, false)
  h.record({ items: ['d'] }); h.undo(); h.undo()
  assert.equal(h.canUndo, false)
})
test('history no-op records preserve redo', () => {
  const h=createHistory(); h.reset({a:1}); h.record({a:2}); h.undo()
  assert.equal(h.record({a:1}), false); assert.equal(h.canRedo, true)
})
test('sentence positions stay tied to session time across window changes', () => {
  const s={t0:98,t1:103}
  assert.deepEqual(sentencePosition(s,{start:100,end:160}),{start:-2,end:3,complete:false})
  assert.deepEqual(sentencePosition(s,{start:90,end:160}),{start:8,end:13,complete:true})
})
test('sentence overrides replace only their own sentence and can be restored', () => {
  const s={t0:100,t1:103,text:'原话'}, other={t0:110,t1:113,original:'原话',text:'另一句'}
  const rows=updateSentenceOverride([other],s,'修订')
  assert.equal(rows.length,2); assert.equal(rows[0].original,'原话')
  assert.deepEqual(updateSentenceOverride(rows,s,'原话'),[other])
})
test('comparison accounts for window origins and matches stable IDs after a large move', () => {
  const a={window:{start:100,end:160},trim:{start:0,end:30},items:[{id:'a',type:'text',start:10,text:'旧字'}]}
  const b={window:{start:90,end:160},trim:{start:10,end:40},items:[{id:'a',type:'text',start:20,text:'新字'}]}
  const c=compareEdits(a,b)
  assert.deepEqual(c.trim.before,c.trim.after)
  assert.deepEqual(c.changes[0].fields,['text'])
  b.items[0].start=40
  assert.equal(compareEdits(a,b).changes[0].kind,'changed')
  assert.equal(a.items[0].text,'旧字')
})
