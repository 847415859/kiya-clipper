import {activeEvent, outlineShadow, splitKeywords} from './anim.js';

// DOM style tree shared by Vue preview and Remotion, outside the camera layer.
export function subtitleView(subs, t, width, height) {
  const style = subs?.style, ev = activeEvent(subs?.events || [], t);
  if (!subs?.enabled || !style || !ev) return null;
  const k = height / 1080, pos = style.position || 'bottom';
  const wrap = {position: 'absolute', left: style.margin_h * k, right: style.margin_h * k, display: 'flex', justifyContent: 'center',
    ...(pos === 'top' ? {top: style.margin_v * k} : pos === 'middle' ? {top: 0, bottom: 0, alignItems: 'center'} : {bottom: style.margin_v * k})};
  const text = {fontFamily: `"${style.font}", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif`,
    fontSize: style.font_size * k, fontWeight: style.bold ? 800 : 500, letterSpacing: (style.spacing || 0) * k,
    color: style.color, lineHeight: 1.25, textAlign: 'center', whiteSpace: 'pre-wrap', maxWidth: width - 2 * style.margin_h * k};
  if (style.box) Object.assign(text, {background: style.box_color, padding: `${style.box_padding * k * 0.6}px ${style.box_padding * k}px`, borderRadius: 4 * k});
  else text.textShadow = outlineShadow(style.outline * k, style.outline_color, style.shadow * k, style.shadow_color);
  let children;
  if (style.karaoke && ev.words?.length) children = ev.words.map(w => ({text: w[2], style: {display: 'inline', color: t >= w[0] ? style.karaoke_color : style.color}}));
  else if (style.highlight) children = splitKeywords(ev.text, subs.keywords || []).map(p => ({text: p.text, style: {display: 'inline', ...(p.hit ? {color: style.highlight_color} : {})}}));
  else children = [{text: ev.text}];
  return {style: wrap, children: [{style: text, children}]};
}
