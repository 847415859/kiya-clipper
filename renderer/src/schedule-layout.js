// Single-page layout in the card's 1920x1080 design coordinates.
// Use conservative glyph widths so the result is deterministic before fonts load.
const graphemes = text => typeof Intl.Segmenter === 'function'
  ? Array.from(new Intl.Segmenter('zh', {granularity: 'grapheme'}).segment(String(text || '')), x => x.segment)
  : Array.from(String(text || ''));

const glyphUnits = glyph => /^\s+$/u.test(glyph) ? 0.35 : /^[MWmw@#%&]$/u.test(glyph) ? 1.08 : /^[\x20-\x7e]+$/u.test(glyph) ? 0.82 : 1.06;

export function wrapScheduleText(text, fontSize, width) {
  const lines = [];
  let line = '', used = 0;
  for (const glyph of graphemes(text)) {
    const size = glyphUnits(glyph) * fontSize;
    if (line && used + size > width) {
      lines.push(line);
      line = '';
      used = 0;
    }
    line += glyph;
    used += size;
  }
  if (line) lines.push(line);
  return lines;
}

export function fitScheduleTitle(text, width, height, maxSize) {
  for (let size = maxSize; size >= 18; size--) {
    const lines = wrapScheduleText(text, size, width - 12);
    if (lines.length * size * 1.22 <= height) return {fontSize: size, lines};
  }
  return {fontSize: 18, lines: wrapScheduleText(text, 18, width - 12)};
}

export function scheduleLayout(entries) {
  const dense = entries.length > 7;
  const columns = dense ? 2 : 1;
  const rows = Math.max(1, Math.ceil(entries.length / columns));
  const left = dense ? 108 : 795, top = dense ? 295 : 280;
  const width = dense ? 1704 : 1005, bottom = 928, columnGap = 42;
  const columnWidth = (width - columnGap * (columns - 1)) / columns;
  const rowHeight = (bottom - top) / rows;
  const dateWidth = dense ? 112 : 126, gap = dense ? 18 : 24;
  const maxSize = entries.length <= 3 ? 46 : dense ? 36 : 39;
  return {dense, columns, left, top, width, bottom, rows, rowHeight,
    items: entries.map((row, i) => {
      const column = Math.floor(i / rows), index = i % rows;
      const titleWidth = columnWidth - dateWidth - gap;
      const title = fitScheduleTitle(row.title, titleWidth, rowHeight - 35, maxSize);
      return {row, left: left + column * (columnWidth + columnGap), top: top + index * rowHeight,
        width: columnWidth, height: rowHeight, dateWidth, gap, title};
    })};
}
