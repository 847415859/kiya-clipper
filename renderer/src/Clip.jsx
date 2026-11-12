import React, { useEffect, useState } from 'react';
import { AbsoluteFill, Audio, OffthreadVideo, Sequence, continueRender, delayRender, useCurrentFrame, useVideoConfig } from 'remotion';
import { bgmVolume, cameraAt } from './anim';
import { FakeUI } from './components/FakeUI';
import { Narration } from './components/Narration';
import { Subtitles } from './components/Subtitles';
import { Sticker } from './components/Sticker';
import { TextFx } from './components/TextFx';

// 一个切片的成片：源视频（已裁好时间段、不带字幕）+ 放大运镜 + 字幕 + 花字 + 贴图 + 吐槽旁白 + 伪 UI + 背景音乐（可选）。
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

// 背景音乐：主播说话（字幕事件）时压低，开头淡入、结尾淡出；曲子比成片短就循环。
const Bgm = ({ music, events, duration, assetBase }) => {
  const { fps } = useVideoConfig();
  return (
    <Audio
      src={url(assetBase, music.file)}
      startFrom={Math.round((music.start || 0) * fps)}
      volume={(f) => bgmVolume(music, events, f / fps, duration)}
      loop
    />
  );
};

// 前情（跨场次呼应）：成片开头插一小段旧场次的画面，带旧字幕和“11月3日｜她说：…”标签，画面略降饱和，和正片区分开。
// props.callback = { video, duration, label, subtitles }；总时长 props.duration = 前情 + 正片，正片时长是 props.mainDuration。
const Callback = ({ cb, assetBase }) => {
  const item = { type: 'narration', text: cb.label, tag: '前情', style: 'strip', preset: 'fade', x: 0.5, y: 0.12, size: 46, color: '#7C5CFF', duration: cb.duration };
  const subs = cb.subtitles || {};
  return (
    <AbsoluteFill style={{ backgroundColor: 'black' }}>
      <OffthreadVideo src={url(assetBase, cb.video)} style={{ width: '100%', height: '100%', objectFit: 'contain', filter: 'saturate(0.75) contrast(1.05)' }} />
      {subs.enabled !== false && subs.events && <Subtitles style={subs.style} events={subs.events} keywords={subs.keywords} />}
      <Narration item={item} seed={0} />
    </AbsoluteFill>
  );
};

export const Clip = (props) => {
  const { fps } = useVideoConfig();
  const { assetBase, items = [], subtitles = {}, fonts = [], music = null, callback = null } = props;
  const cbFrames = callback ? Math.max(1, Math.round(callback.duration * fps)) : 0;
  const mainDuration = props.mainDuration ?? props.duration;
  useFonts(fonts, assetBase);
  const zooms = items.filter((i) => i.type === 'zoom');
  const overlays = items.filter((i) => i.type !== 'zoom');
  return (
    <AbsoluteFill style={{ backgroundColor: 'black' }}>
      {callback && (
        <Sequence from={0} durationInFrames={cbFrames}>
          <Callback cb={callback} assetBase={assetBase} />
        </Sequence>
      )}
      <Sequence from={cbFrames} layout="none">
      <Camera zooms={zooms}>
        <OffthreadVideo src={url(assetBase, props.video || 'source.mp4')} />
      </Camera>
      {subtitles.enabled !== false && subtitles.events && (
        <Subtitles style={subtitles.style} events={subtitles.events} keywords={subtitles.keywords} />
      )}
      {overlays.map((it, i) => (
        <Sequence key={it.id || i} from={Math.round(it.start * fps)} durationInFrames={Math.max(1, Math.round(it.duration * fps))} layout="none">
          {it.type === 'text' ? <TextFx item={it} seed={i + 1} />
            : it.type === 'narration' ? <Narration item={it} seed={i + 1} />
            : it.type === 'fakeui' ? <FakeUI item={it} seed={i + 1} />
            : <Sticker item={it} seed={i + 1} src={url(assetBase, 'stickers/' + it.asset)} />}
        </Sequence>
      ))}
      {music && <Bgm music={music} events={subtitles.enabled !== false ? subtitles.events : []} duration={mainDuration} assetBase={assetBase} />}
      </Sequence>
    </AbsoluteFill>
  );
};
