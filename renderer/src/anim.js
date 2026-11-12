// 动画数学：纯函数，不依赖 Remotion，方便单独测试。时间单位都是秒。

export const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
export const lerp = (a, b, t) => a + (b - a) * t;

export const easeOutCubic = (t) => 1 - Math.pow(1 - clamp(t, 0, 1), 3);
export const easeInOutCubic = (t) => {
  t = clamp(t, 0, 1);
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
};
export const easeOutBack = (t, s = 1.9) => {
  t = clamp(t, 0, 1) - 1;
  return t * t * ((s + 1) * t + s) + 1;
};
export const easeOutBounce = (t) => {
  t = clamp(t, 0, 1);
  const n = 7.5625, d = 2.75;
  if (t < 1 / d) return n * t * t;
  if (t < 2 / d) return n * (t -= 1.5 / d) * t + 0.75;
  if (t < 2.5 / d) return n * (t -= 2.25 / d) * t + 0.9375;
  return n * (t -= 2.625 / d) * t + 0.984375;
};

// 可复现的“随机”抖动（同一帧每次渲染结果一样，Remotion 多线程渲染也不会错乱）
export function shake(t, amp, seed = 0, freq = 30) {
  const a = Math.sin(t * freq + seed * 12.9898) + 0.5 * Math.sin(t * freq * 2.3 + seed * 78.233);
  const b = Math.cos(t * freq * 1.37 + seed * 3.7) + 0.5 * Math.sin(t * freq * 3.1 + seed * 5.1);
  return { x: (amp * a) / 1.5, y: (amp * b) / 1.5 };
}

export const ENTER = 0.32; // 入场时长
export const EXIT = 0.2;   // 出场淡出时长

/**
 * 花字 / 贴图的入场和出场。t = 出现后经过的秒数，dur = 总时长，size = 画面高度（像素，抖动幅度按它算）。
 * 返回 { scale, opacity, dx, dy, rotate }，dx/dy 单位像素。
 */
export function enterExit(preset, t, dur, W, H, seed = 0) {
  let scale = 1, opacity = 1, dx = 0, dy = 0, rotate = 0;
  const p = t / ENTER;
  switch (preset) {
    case 'slam': { // 从很大砸下来，落地瞬间震一下
      const hit = 0.18;
      scale = t < hit ? lerp(2.6, 1, easeOutCubic(t / hit)) : 1;
      opacity = clamp(t / 0.06, 0, 1);
      if (t >= hit && t < hit + 0.35) {
        const s = shake(t, H * 0.012 * (1 - (t - hit) / 0.35), seed, 45);
        dx = s.x; dy = s.y;
      }
      break;
    }
    case 'shake': { // 弹出后持续抖动
      scale = easeOutBack(p);
      const s = shake(t, H * 0.006, seed, 38);
      dx = s.x; dy = s.y;
      rotate = Math.sin(t * 22 + seed) * 2;
      break;
    }
    case 'bounce': { // 从上方掉下来弹两下
      dy = -(1 - easeOutBounce(t / 0.6)) * H * 0.45;
      opacity = clamp(t / 0.08, 0, 1);
      break;
    }
    case 'slide': { // 从右侧滑入
      dx = (1 - easeOutCubic(p)) * W * 0.6;
      opacity = clamp(p * 2, 0, 1);
      break;
    }
    case 'fade': {
      opacity = clamp(p, 0, 1);
      break;
    }
    case 'pop':
    default: { // 弹出放大再回弹
      scale = easeOutBack(p);
      opacity = clamp(p * 3, 0, 1);
      break;
    }
  }
  // 出场：最后 EXIT 秒淡出并略缩小
  const e = clamp((t - (dur - EXIT)) / EXIT, 0, 1);
  if (e > 0) {
    opacity *= 1 - e;
    scale *= 1 - 0.12 * e;
  }
  return { scale: Math.max(0, scale), opacity, dx, dy, rotate };
}

/**
 * 放大运镜：在 [start, start+dur] 里推近到 scale 倍、停住、拉回。t 为该运镜开始后的秒数。
 * 返回推近程度 0~1（乘到 scale-1 上）。
 */
export function zoomEnvelope(t, dur, easeIn = 0.25, easeOut = 0.3) {
  if (t < 0 || t > dur) return 0;
  const i = Math.min(easeIn, dur / 2);
  const o = Math.min(easeOut, dur / 2);
  if (t < i) return easeInOutCubic(t / i);
  if (t > dur - o) return easeInOutCubic((dur - t) / o);
  return 1;
}

/** 当前时间所有运镜叠加后的镜头：取推近程度最大的那个。time 为片段内秒数。 */
export function cameraAt(time, zooms, W, H) {
  let best = null, env = 0;
  for (const z of zooms) {
    const e = zoomEnvelope(time - z.start, z.duration, z.ease_in ?? 0.25, z.ease_out ?? 0.3);
    if (e > env) { env = e; best = z; }
  }
  if (!best) return { scale: 1, x: 0.5, y: 0.5, dx: 0, dy: 0 };
  const scale = 1 + (best.scale - 1) * env;
  let dx = 0, dy = 0;
  if (best.shake > 0) {
    const s = shake(time, H * 0.01 * best.shake * env, 7, 34);
    dx = s.x; dy = s.y;
  }
  // 抖动会露出画面边缘：多放大一点盖住
  const pad = best.shake > 0 ? 1 + (2 * H * 0.01 * best.shake) / Math.min(W, H) : 1;
  return { scale: scale * pad, x: best.x, y: best.y, dx, dy };
}

/** 用一圈 text-shadow 模拟描边（Chrome 的 -webkit-text-stroke 会往字里面描，效果差）。 */
export function outlineShadow(width, color, shadow = 0, shadowColor = 'rgba(0,0,0,0.5)') {
  const parts = [];
  if (width > 0) {
    const steps = Math.max(12, Math.round(width * 6));
    for (let i = 0; i < steps; i++) {
      const a = (2 * Math.PI * i) / steps;
      parts.push(`${(Math.cos(a) * width).toFixed(2)}px ${(Math.sin(a) * width).toFixed(2)}px 0 ${color}`);
    }
  }
  if (shadow > 0) parts.push(`${shadow}px ${shadow}px ${Math.max(1, shadow)}px ${shadowColor}`);
  return parts.join(', ') || 'none';
}

/** 字幕：当前时间的那一条（时间区间互不重叠，取第一个命中的）。 */
export function activeEvent(events, time) {
  for (const e of events || []) {
    if (time >= e.t0 && time < e.t1) return e;
  }
  return null;
}

/** 把文字按关键词切成片段：[{text, hit}]，长词优先匹配。 */
export function splitKeywords(text, keywords) {
  const kws = [...new Set((keywords || []).filter(Boolean))].sort((a, b) => b.length - a.length);
  if (!kws.length) return [{ text, hit: false }];
  const out = [];
  let i = 0, buf = '';
  const lower = text.toLowerCase();
  while (i < text.length) {
    const k = kws.find((w) => lower.startsWith(w.toLowerCase(), i));
    if (k) {
      if (buf) { out.push({ text: buf, hit: false }); buf = ''; }
      out.push({ text: text.slice(i, i + k.length), hit: true });
      i += k.length;
    } else {
      buf += text[i++];
    }
  }
  if (buf) out.push({ text: buf, hit: false });
  return out;
}

/**
 * 背景音乐说话时压低：t 在某句字幕（说话）里 = low；离说话边缘不到 ramp 秒线性渐变，其余 = 1。
 * events 是字幕事件 [{t0, t1}]（相对成片开头）。
 */
export function duckGain(events, t, low = 0.25, ramp = 0.15) {
  let d = Infinity;
  for (const e of events || []) {
    const dist = t >= e.t0 && t < e.t1 ? 0 : t < e.t0 ? e.t0 - t : t - e.t1;
    if (dist < d) d = dist;
    if (d === 0) break;
  }
  return d >= ramp ? 1 : low + (1 - low) * (d / ramp);
}

/** 背景音乐音量：基础音量 × 说话压低 × 开头 0.3 秒淡入 × 结尾 1.5 秒淡出。 */
export function bgmVolume(music, events, t, duration) {
  let v = music.volume;
  if (music.duck && events && events.length) v *= duckGain(events, t);
  v *= clamp(t / 0.3, 0, 1);
  v *= clamp((duration - t) / 1.5, 0, 1);
  return v;
}
