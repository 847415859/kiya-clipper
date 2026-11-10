import React, { useEffect, useState } from 'react';
import { AbsoluteFill, OffthreadVideo, Sequence, continueRender, delayRender, useCurrentFrame, useVideoConfig } from 'remotion';
import { cameraAt } from './anim';
import { Subtitles } from './components/Subtitles';
import { Sticker } from './components/Sticker';
import { TextFx } from './components/TextFx';

// 一个切片的成片：源视频（已裁好时间段、不带字幕）+ 放大运镜 + 字幕 + 花字 + 贴图。
// 所有文件（源视频、贴图、字体）由 render.mjs 起的本地 HTTP 服务提供，地址前缀是 assetBase。
const url = (base, name) => `${base || ''}/${encodeURI(name)}`;

const useFonts = (fonts, base) => {
  const [handle] = useState(() => (fonts && fonts.length ? delayRender('加载字体') : null));
  useEffect(() => {
    if (handle === null) return;
    Promise.all(
      fonts.map((f) => new FontFace(f.family, `url(${url(base, f.file)})`).load().then((ff) => document.fonts.add(ff)).catch(() => null))
    ).then(() => continueRender(handle));
  }, [handle]);
};

const Camera = ({ zooms, children }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const c = cameraAt(frame / fps, zooms, width, height);
  return (
    <AbsoluteFill
      style={{
        transformOrigin: `${c.x * 100}% ${c.y * 100}%`,
        transform: `translate(${c.dx}px, ${c.dy}px) scale(${c.scale})`
      }}
    >
      {children}
    </AbsoluteFill>
  );
};

export const Clip = (props) => {
  const { fps } = useVideoConfig();
  const { assetBase, items = [], subtitles = {}, fonts = [] } = props;
  useFonts(fonts, assetBase);
  const zooms = items.filter((i) => i.type === 'zoom');
  const overlays = items.filter((i) => i.type === 'text' || i.type === 'sticker');
  return (
    <AbsoluteFill style={{ backgroundColor: 'black' }}>
      <Camera zooms={zooms}>
        <OffthreadVideo src={url(assetBase, props.video || 'source.mp4')} />
      </Camera>
      {subtitles.enabled !== false && subtitles.events && (
        <Subtitles style={subtitles.style} events={subtitles.events} keywords={subtitles.keywords} />
      )}
      {overlays.map((it, i) => (
        <Sequence key={it.id || i} from={Math.round(it.start * fps)} durationInFrames={Math.max(1, Math.round(it.duration * fps))} layout="none">
          {it.type === 'text' ? <TextFx item={it} seed={i + 1} /> : <Sticker item={it} seed={i + 1} src={url(assetBase, 'stickers/' + it.asset)} />}
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
