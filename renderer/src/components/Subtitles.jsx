import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
import {subtitleView} from '../subtitle-view.js';
import {FxNode} from './FxNode';

export const Subtitles = ({style, events, keywords}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const node = subtitleView({enabled: true, style, events, keywords}, frame / fps, width, height);
  return node ? <AbsoluteFill><FxNode node={node}/></AbsoluteFill> : null;
};
