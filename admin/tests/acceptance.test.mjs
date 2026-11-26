import { test } from 'node:test'
import assert from 'node:assert/strict'
import { acceptanceTime, money, rangeLabel, statusLabel } from '../src/acceptance.js'

test('unqualified times retain timezone uncertainty', () => {
  assert.match(acceptanceTime('2026-11-26 18:00:00'), /时区未记录/)
  assert.match(acceptanceTime('2026-11-26T10:00:00Z'), /北京时间/)
  assert.equal(acceptanceTime(null), '未知')
})
test('unknown cost is never rendered as zero', () => {
  assert.equal(money(null), '未知')
  assert.equal(money(undefined), '未知')
  assert.equal(money(0), '¥0.00')
  assert.equal(money(123), '¥1.23')
})
test('source and output time ranges remain distinct', () => {
  assert.equal(rangeLabel([10.11, 12.11], 'output'), '成片秒数 10.11–12.11')
  assert.equal(rangeLabel([47, 49], 'source'), '整场原片秒数 47–49')
  assert.match(rangeLabel([47, 49]), /时间轴未记录/)
})
test('prepared alone does not claim qualified media', () => {
  assert.equal(statusLabel('prepared'), '处理完成，无成片')
  assert.equal(statusLabel('no_candidates'), '处理完成，无成片')
  assert.equal(statusLabel('has_clips'), '已有合格成片')
})
