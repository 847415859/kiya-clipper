import React from 'react';
import { useCurrentFrame, useVideoConfig } from 'remotion';
import { enterExit, outlineShadow } from '../anim';

// 花字：放在 <Sequence> 里，frame 从 0 开始计。位置 x/y 是画面比例（0~1，指文字中心）。
export const TextFx = ({ item, seed }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const t = frame / fps;
  const a = enterExit(item.preset || 'pop', t, item.duration, width, height, seed);
  const k = height / 1080;
  const size = (item.size || 96) * k;
  return (
    <div
      style={{
        position: 'absolute',
        left: item.x * width,
        top: item.y * height,
        transform: `translate(-50%, -50%) translate(${a.dx}px, ${a.dy}px) rotate(${(item.rotate || 0) + a.rotate}deg) scale(${a.scale})`,
        opacity: a.opacity,
        fontFamily: `"${item.font || 'Noto Sans CJK SC'}", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif`,
        fontSize: size,
        fontWeight: 900,
        color: item.color || '#FFE14D',
        textShadow: outlineShadow((item.stroke_width ?? 8) * k, item.stroke || '#E0457B', 6 * k, 'rgba(0,0,0,0.45)'),
        whiteSpace: 'pre',
        lineHeight: 1.15,
        textAlign: 'center'
      }}
    >
      {item.text}
    </div>
  );
};
