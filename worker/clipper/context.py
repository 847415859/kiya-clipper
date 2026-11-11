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


def pick_danmaku(dm: list[Event], limit: int, delay: float = 0.0, peak: float | None = None, hit=None) -> list[Event]:
    """弹幕超过 limit 条时的取舍。没给 peak / hit：均匀抽样（老办法）。
    给了：约 2/3 的名额按“命中情绪词 / 专属梗 + 离粗筛高峰近”打分取最高的（笑点处那条“绷不住了”别漏掉），
    剩下 1/3 在其余弹幕里均匀抽样，保留整体氛围。返回按时间排序。"""
    if len(dm) <= limit:
        return dm
    if peak is None and hit is None:
        step = len(dm) / limit
        return [dm[int(i * step)] for i in range(limit)]

    def score(e: Event) -> float:
        s = 2.0 if hit is not None and hit(e.text) else 0.0
        if peak is not None:
            s += 1.5 / (1 + abs(e.t - delay - peak) / 8)
        return s

    k = limit * 2 // 3
    order = sorted(range(len(dm)), key=lambda i: -score(dm[i]))
    chosen = set(order[:k])
    rest = [i for i in range(len(dm)) if i not in chosen]
    n_rest = limit - k
    if rest and n_rest > 0:
        step = len(rest) / n_rest
        chosen |= {rest[int(j * step)] for j in range(min(n_rest, len(rest)))}
    return [dm[i] for i in sorted(chosen)]


def danmaku_lines(events: list[Event], a: float, b: float, origin: float, delay: float, limit: int = 60,
                  peak: float | None = None, hit=None) -> tuple[str, str]:
    """(弹幕采样, 礼物 / SC / 上舰)。弹幕按延迟前移，与画面对齐；超过 limit 条时的取舍见 pick_danmaku；
    礼物超过 30 条时 SC（文字常常就是这一段的起因）和上舰优先保留。"""
    dm = [e for e in events if e.kind == "danmaku" and a <= e.t - delay < b and e.text]
    dm = pick_danmaku(dm, limit, delay, peak, hit)
    dm_txt = "\n".join(f"[{e.t - delay - origin:.0f}] {e.text}" for e in dm) or "（无）"
    names = {"gift": "礼物", "sc": "SC", "guard": "上舰"}
    gifts = [e for e in events if e.kind in names and a <= e.t < b]
    if len(gifts) > 30:
        pri = {"sc": 0, "guard": 1, "gift": 2}
        gifts = sorted(sorted(gifts, key=lambda e: pri[e.kind])[:30], key=lambda e: e.t)
    g_txt = "\n".join(f"[{e.t - origin:.0f}] {names[e.kind]} {e.text or ''} {e.value:g}".rstrip() for e in gifts[:30]) or "（无）"
    return dm_txt, g_txt


def locate(segments: list[dict], t: float) -> tuple[Path, float] | None:
    """整场时间 → (分段视频, 文件内秒数)。"""
    best = None
    for s in segments:
        if s["offset"] <= t and Path(s["video"]).exists():
            best = (Path(s["video"]), t - s["offset"])
    return best


def frame_times(a: float, b: float, n: int, peak: float | None = None, mode: str = "uniform") -> list[float]:
    """抽帧的时刻（整场秒），恰好 n 个。uniform：均匀。peak：一半集中在粗筛高峰前后（笑点的表情多半在那里），
    另一半在其余位置均匀取（看全貌，和高峰帧至少隔 2 秒）。窗口短于 18 秒 / 没有高峰 / 帧数少于 4 时退回均匀。"""
    uniform = [a + (b - a) * (i + 0.5) / n for i in range(n)]
    if mode != "peak" or peak is None or not (a <= peak < b) or n < 4 or b - a < 18:
        return uniform
    n_pk = n // 2
    n_uni = n - n_pk
    spread = min(8.0, (b - a) / 6)
    pk = [min(max(peak - spread + 2 * spread * k / (n_pk - 1), a), b - 0.1) for k in range(n_pk)]
    m = n_uni * 4
    eligible = [t for t in (a + (b - a) * (i + 0.5) / m for i in range(m)) if all(abs(t - x) >= 2.0 for x in pk)]
    if len(eligible) < n_uni:
        return uniform
    step = len(eligible) / n_uni
    return sorted(pk + [eligible[int(i * step)] for i in range(n_uni)])


def extract_frames(segments: list[dict], a: float, b: float, n: int, out_dir: Path, width: int = 640,
                   peak: float | None = None, mode: str = "uniform") -> list[tuple[float, Path]]:
    """按 frame_times 在 [a, b) 里抽 n 帧左右，返回 [(相对片段开头的秒数, 图片)]；录像不在本地时返回空列表。"""
    if not shutil.which("ffmpeg"):
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for i, t in enumerate(frame_times(a, b, n, peak, mode)):
        loc = locate(segments, t)
        if not loc:
            continue
        dst = out_dir / (f"f{i:02d}.jpg" if mode != "peak" else f"p{round((t - a) * 10):05d}.jpg")      # 取帧方式不同，文件名不同，不会误用旧图
        if not dst.exists():
            r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", f"{loc[1]:.2f}", "-i", str(loc[0]),
                                "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", str(dst)], capture_output=True)
            if r.returncode != 0 or not dst.exists():
                continue
        frames.append((round(t - a, 1), dst))
    return frames
