import React from 'react';
import { Img, useCurrentFrame, useVideoConfig } from 'remotion';
import { enterExit } from '../anim';

// 表情包 / 贴图：scale 是图片宽度占画面宽度的比例。
export const Sticker = ({ item, src, seed }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const a = enterExit(item.preset || 'pop', frame / fps, item.duration, width, height, seed);
  return (
    <Img
      src={src}
      style={{
        position: 'absolute',
        left: item.x * width,
        top: item.y * height,
        width: (item.scale || 0.25) * width,
        transform: `translate(-50%, -50%) translate(${a.dx}px, ${a.dy}px) rotate(${(item.rotate || 0) + a.rotate}deg) scale(${a.scale})`,
        opacity: a.opacity
      }}
    />
  );
};
