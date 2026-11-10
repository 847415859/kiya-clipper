"""给大模型准备一个候选片段的上下文：主播资料、前情原话、片段原话、弹幕采样、礼物 / SC / 上舰、抽帧。

精排（rerank.py）和自动剪辑脚本（autoedit.py）共用。时间都换算成“相对片段开头”的秒数，和截图标注一致。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .danmaku import Event, all_events, load_session

TITLE_EXAMPLES = ["舰长寄来律师函：请停止你的报恩行为", "妈妈我D到傻V了", "你们都是禁言凶手！！！"]


def profile(cfg: dict) -> str:
    s = cfg.get("streamer", {})
    lore = cfg.get("lore", {})
    j = lambda k: "、".join(str(x) for x in (lore.get(k) or []))
    titles = lore.get("title_examples") or TITLE_EXAMPLES
    return (f"主播：{s.get('name', '')}（B站虚拟主播，Live2D 模型）\n"
            f"人设：{j('persona')}\n"
            f"昵称：{j('nicknames')}；观众被称为“{lore.get('audience_call', '')}”；粉丝名 {lore.get('fan_name', '')}\n"
            f"世界观：{j('world')}\n常见话题：{j('recurring_topics')}\n口头禅：{j('catchphrases')}\n"
            f"专属梗：{'、'.join(str(x) for x in [*(cfg.get('lore_words') or []), *(lore.get('memes') or [])])}\n"
            f"她的高播放切片标题示例：{'；'.join('《' + t + '》' for t in titles)}")


def load_events(segments: list[dict]) -> list[Event]:
    """candidates.json 里的分段 → 整场弹幕 / 礼物事件（与粗筛用同一套时间轴）。"""
    xmls = [Path(s["video"]).with_suffix(".xml") for s in segments]
    xmls = [x for x in xmls if x.exists()]
    return all_events(load_session(xmls)) if xmls else []


def speech_lines(sentences: list[dict], a: float, b: float, origin: float, limit: int = 2500) -> str:
    out, n = [], 0
    for s in sentences:
        if s["t1"] > a and s["t0"] < b:
            line = f"[{s['t0'] - origin:.1f}] {s['text']}"
            n += len(line)
            if n > limit:
                out.append("…")
                break
            out.append(line)
    return "\n".join(out) or "（这段没有识别到说话）"


def danmaku_lines(events: list[Event], a: float, b: float, origin: float, delay: float, limit: int = 60) -> tuple[str, str]:
    """(弹幕采样, 礼物 / SC / 上舰)。弹幕按延迟前移，与画面对齐；超过 limit 条时均匀抽样。"""
    dm = [e for e in events if e.kind == "danmaku" and a <= e.t - delay < b and e.text]
    if len(dm) > limit:
        step = len(dm) / limit
        dm = [dm[int(i * step)] for i in range(limit)]
    dm_txt = "\n".join(f"[{e.t - delay - origin:.0f}] {e.text}" for e in dm) or "（无）"
    names = {"gift": "礼物", "sc": "SC", "guard": "上舰"}
    gifts = [e for e in events if e.kind in names and a <= e.t < b]
    g_txt = "\n".join(f"[{e.t - origin:.0f}] {names[e.kind]} {e.text or ''} {e.value:g}".rstrip() for e in gifts[:30]) or "（无）"
    return dm_txt, g_txt


def locate(segments: list[dict], t: float) -> tuple[Path, float] | None:
    """整场时间 → (分段视频, 文件内秒数)。"""
    best = None
    for s in segments:
        if s["offset"] <= t and Path(s["video"]).exists():
            best = (Path(s["video"]), t - s["offset"])
    return best


def extract_frames(segments: list[dict], a: float, b: float, n: int, out_dir: Path, width: int = 640) -> list[tuple[float, Path]]:
    """在 [a, b) 里均匀抽 n 帧，返回 [(相对片段开头的秒数, 图片)]；录像不在本地时返回空列表。"""
    if not shutil.which("ffmpeg"):
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for i in range(n):
        t = a + (b - a) * (i + 0.5) / n
        loc = locate(segments, t)
        if not loc:
            continue
        dst = out_dir / f"f{i:02d}.jpg"
        if not dst.exists():
            r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", f"{loc[1]:.2f}", "-i", str(loc[0]),
                                "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", str(dst)], capture_output=True)
            if r.returncode != 0 or not dst.exists():
                continue
        frames.append((round(t - a, 1), dst))
    return frames
