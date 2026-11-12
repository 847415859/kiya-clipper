import React from 'react';
import { useCurrentFrame, useVideoConfig } from 'remotion';
import { enterExit } from '../anim';
import { narrationView } from '../fakeui';
import { FxNode } from './FxNode';

// 吐槽旁白：画面上的一行文字旁白（不配音）。放在 <Sequence> 里，frame 从 0 开始计。
export const Narration = ({ item, seed }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const a = enterExit(item.preset || 'fade', frame / fps, item.duration, width, height, seed);
  return <FxNode node={narrationView(item, width, height, a)} />;
};
