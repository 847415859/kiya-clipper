import React from 'react';
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from 'remotion';
import { activeEvent, outlineShadow, splitKeywords } from '../anim';

// 字幕：样式字段与 config/schema/subtitle_style.schema.json 一致（尺寸以 1080p 为基准按画面高度缩放）。
// 渲染在运镜层之外，放大运镜时字幕不跟着放大。
export const Subtitles = ({ style, events, keywords }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const t = frame / fps;
  const ev = activeEvent(events, t);
  if (!ev || !style) return null;
  const k = height / 1080;
  const pos = style.position || 'bottom';
  const wrap = {
    position: 'absolute', left: style.margin_h * k, right: style.margin_h * k, display: 'flex', justifyContent: 'center',
    ...(pos === 'top' ? { top: style.margin_v * k } : pos === 'middle' ? { top: 0, bottom: 0, alignItems: 'center' } : { bottom: style.margin_v * k })
  };
  const text = {
    fontFamily: `"${style.font}", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif`,
    fontSize: style.font_size * k,
    fontWeight: style.bold ? 800 : 500,
    letterSpacing: (style.spacing || 0) * k,
    color: style.color,
    lineHeight: 1.25,
    textAlign: 'center',
    whiteSpace: 'pre-wrap',
    maxWidth: width - 2 * style.margin_h * k
  };
  if (style.box) {
    Object.assign(text, { background: style.box_color, padding: `${style.box_padding * k * 0.6}px ${style.box_padding * k}px`, borderRadius: 4 * k });
  } else {
    text.textShadow = outlineShadow(style.outline * k, style.outline_color, style.shadow * k, style.shadow_color);
  }

  let children;
  if (style.karaoke && ev.words && ev.words.length) {
    // 逐字变色：读到的词用 karaoke_color
    children = ev.words.map((w, i) => (
      <span key={i} style={{ color: t >= w[0] ? style.karaoke_color : style.color }}>{w[2]}</span>
    ));
  } else if (style.highlight) {
    children = splitKeywords(ev.text, keywords).map((p, i) => (
      <span key={i} style={p.hit ? { color: style.highlight_color } : undefined}>{p.text}</span>
    ));
  } else {
    children = ev.text;
  }
  return (
    <AbsoluteFill>
      <div style={wrap}><div style={text}>{children}</div></div>
    </AbsoluteFill>
  );
};
