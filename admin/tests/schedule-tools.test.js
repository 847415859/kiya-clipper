import {test} from 'node:test'
import assert from 'node:assert/strict'
import {shanghaiDay,addDays,monday,emptyEntry} from '../src/schedule-tools.js'
test('按北京时间跨日和跨周，与浏览器时区无关',()=>{
  assert.equal(shanghaiDay(new Date('2026-11-15T16:01:00Z')),'2026-11-16')
  assert.equal(monday('2026-11-22'),'2026-11-16')
  assert.equal(monday('2027-01-01'),'2026-12-28')
  assert.equal(addDays('2026-12-28',6),'2027-01-03')
})
test('新安排有独立标识且不捏造时间',()=>{
  const a=emptyEntry('2026-11-17'),b=emptyEntry('2026-11-17')
  assert.notEqual(a.id,b.id)
  assert.equal(a.start_time,'')
  assert.equal(a.date,'2026-11-17')
})
