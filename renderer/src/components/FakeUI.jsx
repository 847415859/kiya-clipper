import React from 'react';
import { useCurrentFrame, useVideoConfig } from 'remotion';
import { enterExit } from '../anim';
import { fakeUiView } from '../fakeui';
import { FxNode } from './FxNode';

// 伪 UI：系统弹窗 / 聊天消息 / 成就解锁 / 任务通知条（通用假界面，不仿冒真实平台）。
export const FakeUI = ({ item, seed }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const a = enterExit(item.preset || 'pop', frame / fps, item.duration, width, height, seed);
  return <FxNode node={fakeUiView(item, width, height, a)} />;
};
