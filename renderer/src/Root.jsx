import React from 'react';
import { Composition } from 'remotion';
import { Clip } from './Clip';

// Remotion Studio 里预览用的示例数据；真正渲染时由 render.mjs 传入 inputProps
const defaultProps = {
  width: 1280, height: 720, fps: 30, duration: 6, assetBase: '', video: 'source.mp4',
  subtitles: { enabled: false, events: [], keywords: [] },
  items: [{ id: 'demo', type: 'text', start: 0.5, duration: 2.5, text: '示例花字！', preset: 'pop', x: 0.5, y: 0.35, size: 110 }],
  fonts: []
};

export const RemotionRoot = () => (
  <Composition
    id="Clip"
    component={Clip}
    defaultProps={defaultProps}
    durationInFrames={180}
    fps={30}
    width={1280}
    height={720}
    calculateMetadata={({ props }) => ({
      durationInFrames: Math.max(1, Math.round(props.duration * props.fps)),
      fps: props.fps,
      width: props.width,
      height: props.height
    })}
  />
);
