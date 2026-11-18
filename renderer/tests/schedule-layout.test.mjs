import assert from 'node:assert/strict';
import test from 'node:test';
import {scheduleLayout, wrapScheduleText} from '../src/schedule-layout.js';

for (const count of [1, 2, 5, 6, 7, 8, 14]) {
  test(`${count} entries fit a single card without lost titles`, () => {
    const entries = Array.from({length:count}, (_,i) => ({id:String(i), title:'日程内容'.repeat(15)}));
    const layout = scheduleLayout(entries);
    assert.equal(layout.items.length, count);
    assert.equal(layout.columns, count > 7 ? 2 : 1);
    for (const item of layout.items) {
      assert.equal(item.title.lines.join(''), item.row.title);
      assert.ok(item.title.lines.length * item.title.fontSize * 1.22 <= item.height - 35);
      assert.ok(item.top + item.height <= layout.bottom + .001);
      assert.ok(item.left + item.width <= 1812 + .001);
      assert.ok(item.title.fontSize >= 18);
    }
  });
}

test('mixed text and emoji graphemes stay intact', () => {
  const text = '👩‍🎤周末联动 WWW 游戏直播 & 聊天';
  assert.equal(wrapScheduleText(text,32,230).join(''), text);
  assert.ok(wrapScheduleText(text,32,230)[0].startsWith('👩‍🎤'));
});
