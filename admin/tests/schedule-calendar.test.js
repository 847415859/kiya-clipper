import {test} from 'node:test'
import assert from 'node:assert/strict'
import {calendarWeeks,categoryOf} from '../src/schedule-calendar.js'
const weeks=[{week_start:'2026-12-28',active:true,published:{entries:[{date:'2027-01-01',title:'跨年歌回',category:'sing'}]},draft:{entries:[{date:'2026-12-29',title:'草稿游戏',category:'game'}]}}]
test('周一到周日跨年排列，未填写日期保持空白',()=>{const days=calendarWeeks(weeks,'published')[0].days;assert.equal(days[0].date,'2026-12-28');assert.equal(days[6].date,'2027-01-03');assert.equal(days[0].entries.length,0);assert.equal(days[4].entries[0].title,'跨年歌回')})
test('搜索和分类交集，不将草稿混入确认版本',()=>{assert.equal(calendarWeeks(weeks,'published','游戏').length,0);assert.equal(calendarWeeks(weeks,'draft','游戏','game').length,1);assert.equal(calendarWeeks(weeks,'published','跨年','game').length,0);assert.equal(categoryOf({}).label,'其他')})
