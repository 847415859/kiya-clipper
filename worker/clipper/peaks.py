"""弹幕信号 → 高峰候选。

做法（对应技术方案“模块二 第一步：粗筛”的弹幕部分）：
1. 按 bucket_seconds 分桶，统计每桶弹幕数、情绪词命中数、礼物价值。
2. 弹幕数、情绪词数都和“前 baseline_seconds 的滑动均值”比，得到相对倍数，
   避免开播打招呼、热度整体变化带来的误判。
3. 各信号做稳健标准化（中位数 / MAD），按权重加和，再平滑。
4. 峰值检测，相邻太近的合并，取前 top_n 个。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np
from scipy.signal import find_peaks

from .danmaku import Event, Segment


@dataclass
class Peak:
    rank: int
    t: float                 # 高峰时间（整场秒数，已按弹幕延迟前移）
    score: float
    danmaku_per_min: float
    emotion_hits: int
    gift_value: float
    top_danmaku: list[str]
    clip_start: float        # 候选片段起点（整场秒数）
    clip_end: float
    file_index: int
    local_start: float       # 在对应分段视频里的起点秒数


def hhmmss(sec: float) -> str:
    sec = max(0, int(round(sec)))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def _robust_z(x: np.ndarray) -> np.ndarray:
    med = np.median(x)
    mad = np.median(np.abs(x - med))
    scale = mad * 1.4826 if mad > 1e-9 else (np.std(x) or 1.0)
    return (x - med) / scale


def _trailing_mean(x: np.ndarray, window: int, floor: float) -> np.ndarray:
    """每个位置前 window 个桶（不含自身）的均值；开头不足时用已有部分，最低为 floor。"""
    csum = np.concatenate([[0.0], np.cumsum(x)])
    out = np.empty_like(x, dtype=float)
    for i in range(len(x)):
        lo = max(0, i - window)
        n = i - lo
        out[i] = (csum[i] - csum[lo]) / n if n > 0 else floor
    return np.maximum(out, floor)


def _smooth(x: np.ndarray, window: int) -> np.ndarray:
    if window <= 1:
        return x
    k = np.ones(window) / window
    return np.convolve(x, k, mode="same")


def emotion_matcher(words_by_group: dict[str, list[str]], extra: list[str] | None = None):
    # YAML 里没加引号的数字（如 233）会被读成 int，统一转成字符串
    words = [str(w).lower() for ws in words_by_group.values() for w in (ws or [])]
    words += [str(w).lower() for w in (extra or [])]
    words = [w for w in words if w]

    def hit(text: str) -> bool:
        t = text.lower()
        return any(w in t for w in words)

    return hit


def compute_signals(events: list[Event], cfg: dict):
    a = cfg["analysis"]
    bucket = float(a["bucket_seconds"])
    end_t = max((e.t for e in events), default=0.0)
    n = int(end_t // bucket) + 1
    count = np.zeros(n)
    emo = np.zeros(n)
    gift = np.zeros(n)
    hit = emotion_matcher(cfg.get("emotion_words", {}), cfg.get("lore_words", []))
    for e in events:
        i = int(e.t // bucket)
        if e.kind == "danmaku":
            count[i] += 1
            if hit(e.text):
                emo[i] += 1
        elif e.kind == "gift":
            gift[i] += e.value * 0.1      # 普通礼物多为小额，按个数折算一个小权重
        else:                              # sc / guard：按金额（元）
            gift[i] += e.value

    base_win = max(1, int(a["baseline_seconds"] // bucket))
    floor = max(1.0, float(np.median(count)))
    base = _trailing_mean(count, base_win, floor)
    density_ratio = count / base
    emotion_ratio = emo / base

    w = cfg["weights"]
    score = (w["density"] * _robust_z(density_ratio)
             + w["emotion"] * _robust_z(emotion_ratio)
             + w["gift"] * _robust_z(np.log1p(gift)))
    score = _smooth(score, max(1, int(a["smooth_seconds"] // bucket)))
    times = (np.arange(n) + 0.5) * bucket
    return {"times": times, "score": score, "count": count, "emotion": emo, "gift": gift, "bucket": bucket}


def find_top_peaks(events: list[Event], segs: list[Segment], cfg: dict) -> tuple[list[Peak], dict]:
    a = cfg["analysis"]
    sig = compute_signals(events, cfg)
    bucket = sig["bucket"]
    score = sig["score"]
    if len(score) < 3:
        return [], sig
    dist = max(1, int(a["min_peak_gap_seconds"] // bucket))
    idx, _ = find_peaks(score, distance=dist)
    idx = sorted(idx, key=lambda i: score[i], reverse=True)[: int(a["top_n"])]

    delay = float(a["danmaku_delay_seconds"])
    before = float(a["clip_before_seconds"])
    after = float(a["clip_after_seconds"])
    danmaku = [e for e in events if e.kind == "danmaku"]
    dm_times = np.array([e.t for e in danmaku])

    peaks: list[Peak] = []
    for rank, i in enumerate(idx, start=1):
        t_raw = float(sig["times"][i])
        t = max(0.0, t_raw - delay)
        # 高峰前后 15 秒内出现最多的弹幕，帮助快速判断发生了什么
        lo, hi = np.searchsorted(dm_times, [t_raw - 15, t_raw + 15]) if len(dm_times) else (0, 0)
        top = [txt for txt, _ in Counter(e.text for e in danmaku[lo:hi] if e.text).most_common(3)]
        clip_start = max(0.0, t - before)
        clip_end = t + after
        seg_i = max((k for k, s in enumerate(segs) if s.offset <= clip_start), default=0)
        win = slice(max(0, i - 3), i + 3)
        peaks.append(Peak(
            rank=rank, t=t, score=float(score[i]),
            danmaku_per_min=float(sig["count"][i]) * 60.0 / bucket,
            emotion_hits=int(sig["emotion"][win].sum()),
            gift_value=float(sig["gift"][win].sum()),
            top_danmaku=top, clip_start=clip_start, clip_end=clip_end,
            file_index=seg_i, local_start=clip_start - segs[seg_i].offset if segs else clip_start,
        ))
    return peaks, sig
