"""多信号 → 高峰候选（技术方案“模块二 第一步：粗筛”）。

信号（都按 bucket_seconds 分桶）：
  density   弹幕数 ÷ 前 baseline_seconds 的滑动均值（相对热度，消除开播打招呼、整体热度变化）
  emotion   情绪词 / 专属梗命中数 ÷ 同一滑动均值
  gift      礼物 / SC / 上舰金额（取对数）
  audio     桶内最大响度 − 前 baseline_seconds 的响度中位数（dB，相对变响了多少）
  scene     桶内关键帧画面变化之和（取对数）
  laugh     主播原话里的笑声（“哈哈哈”“嘿嘿”“笑死”…，需要先转写）
  scream    尖叫 / 高音大笑：响度突增且高频能量占比突增
另有 music（音高稳定拖长的比例，唱歌 / 纯音乐高）：不参与打分，用于标记和（可选）过滤歌回片段。
每个信号做稳健标准化（中位数 / MAD），按权重加和后平滑、峰值检测。
没有录像特征时自动只用弹幕三项，权重按可用信号重新归一化。

弹幕延迟：观众反应比画面慢几秒。有音频时用“弹幕热度 vs 响度”的互相关自动估计，
否则用配置里的固定值。弹幕在分桶前就整体前移，所以高峰时间直接对应画面时间。
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field

import numpy as np
from scipy.signal import find_peaks

from .danmaku import Event, Segment

SIGNALS = ("density", "emotion", "gift", "audio", "scene", "laugh", "scream")
LAUGH_RE = re.compile(r"(哈{2,}|[嘿呵嘻]{2,}|h{3,}|笑死|笑不活|笑疯)", re.I)


@dataclass
class Peak:
    rank: int
    t: float                 # 高峰时间（整场秒数，已校正弹幕延迟）
    score: float
    danmaku_per_min: float
    emotion_hits: int
    gift_value: float
    top_danmaku: list[str]
    clip_start: float        # 候选片段起点（整场秒数）
    clip_end: float
    file_index: int
    local_start: float       # 在对应分段视频里的起点秒数
    contrib: dict = field(default_factory=dict)   # 各信号在该点的贡献（加权后的 z 值）
    music: float = 0.0       # 片段内唱歌 / 纯音乐的程度 0~1

    def to_dict(self) -> dict:
        return asdict(self)


def hhmmss(sec: float) -> str:
    sec = max(0, int(round(sec)))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def robust_z(x: np.ndarray) -> np.ndarray:
    x = np.nan_to_num(np.asarray(x, dtype=float), nan=0.0)
    med = np.median(x)
    mad = np.median(np.abs(x - med))
    scale = mad * 1.4826 if mad > 1e-9 else (np.std(x) or 1.0)
    return (x - med) / scale


def trailing_mean(x: np.ndarray, window: int, floor: float) -> np.ndarray:
    """每个位置前 window 个元素（不含自身）的均值；开头不足时用已有部分，最低为 floor。"""
    csum = np.concatenate([[0.0], np.cumsum(x)])
    idx = np.arange(len(x))
    lo = np.maximum(0, idx - window)
    n = idx - lo
    out = np.where(n > 0, (csum[idx] - csum[lo]) / np.maximum(n, 1), floor)
    return np.maximum(out, floor)


def trailing_median(x: np.ndarray, window: int) -> np.ndarray:
    out = np.empty(len(x))
    for i in range(len(x)):
        w = x[max(0, i - window):i]
        w = w[~np.isnan(w)]
        out[i] = np.median(w) if len(w) else np.nan
    return out


def smooth(x: np.ndarray, window: int) -> np.ndarray:
    if window <= 1:
        return x
    return np.convolve(x, np.ones(window) / window, mode="same")


def emotion_matcher(words_by_group: dict, extra: list | None = None):
    words = [str(w).lower() for ws in (words_by_group or {}).values() for w in (ws or [])]
    words += [str(w).lower() for w in (extra or [])]
    words = [w for w in words if w]

    def hit(text: str) -> bool:
        t = text.lower()
        return any(w in t for w in words)

    return hit


def estimate_delay(events: list[Event], audio_db: np.ndarray | None, cfg: dict) -> tuple[float, str]:
    """返回 (弹幕延迟秒, 说明)。互相关不可靠时退回配置值。"""
    a = cfg["analysis"]
    default = float(a.get("danmaku_delay_seconds", 5))
    if a.get("delay_mode", "auto") != "auto" or audio_db is None or np.all(np.isnan(audio_db)):
        return default, "固定值"
    lo, hi = a.get("delay_range_seconds", [1, 15])
    n = len(audio_db)
    dm = np.zeros(n)
    for e in events:
        if e.kind == "danmaku" and 0 <= int(e.t) < n:
            dm[int(e.t)] += 1
    if dm.sum() < 100:
        return default, "弹幕太少，用固定值"
    valid = ~np.isnan(audio_db)
    base = trailing_median(np.where(valid, audio_db, np.nan)[::5], 60)  # 降采样算基线，省时间
    base = np.repeat(base, 5)[:n]
    loud = np.where(valid, audio_db - np.nan_to_num(base, nan=np.nanmedian(audio_db)), 0)
    # 用“起势”（平滑后一阶差分的正部分）做互相关：观众反应是持续一段时间的，
    # 直接比较整体曲线会把延迟估成反应持续时间的中点；比较起势更接近真实延迟
    x = np.maximum(np.diff(smooth(loud, 3), prepend=0), 0)
    y = np.maximum(np.diff(smooth(dm, 3), prepend=0), 0)
    best, best_lag = -1.0, default
    for lag in range(int(lo), int(hi) + 1):
        c = float(np.corrcoef(x[: n - lag], y[lag:])[0, 1]) if n - lag > 60 else -1
        if c > best:
            best, best_lag = c, float(lag)
    if best < 0.05:
        return default, f"互相关太弱（{best:.2f}），用固定值"
    return best_lag, f"自动估计（相关 {best:.2f}）"


def laugh_counts(sentences: list[dict], n: int, bucket: float) -> np.ndarray:
    """每个桶里主播原话的笑声次数（按句子时长平摊）。"""
    out = np.zeros(n)
    for s in sentences or []:
        k = len(LAUGH_RE.findall(s.get("text", "")))
        if not k:
            continue
        a, b = int(s["t0"] // bucket), int(max(s["t0"], s["t1"] - 1e-6) // bucket)
        span = range(max(0, a), min(n - 1, b) + 1)
        for i in span:
            out[i] += k / max(1, len(span))
    return out


def compute_signals(events: list[Event], cfg: dict, features: dict | None = None, delay: float = 0.0,
                    sentences: list[dict] | None = None) -> dict:
    a = cfg["analysis"]
    bucket = float(a["bucket_seconds"])
    end_t = max((e.t for e in events), default=0.0)
    if features is not None:
        end_t = max(end_t, len(features["audio_db"]))
    n = int(end_t // bucket) + 1
    count, emo, gift = np.zeros(n), np.zeros(n), np.zeros(n)
    hit = emotion_matcher(cfg.get("emotion_words", {}), cfg.get("lore_words", []))
    for e in events:
        t = e.t - delay if e.kind == "danmaku" else e.t
        i = int(max(0.0, t) // bucket)
        if i >= n:
            continue
        if e.kind == "danmaku":
            count[i] += 1
            if hit(e.text):
                emo[i] += 1
        elif e.kind == "gift":
            gift[i] += e.value * 0.1          # 普通礼物多为小额，按个数折算一个小权重
        else:                                  # sc / guard：按金额（元）
            gift[i] += e.value

    base_win = max(1, int(a["baseline_seconds"] // bucket))
    floor = max(1.0, float(np.median(count)))
    base = trailing_mean(count, base_win, floor)
    raw = {"density": count / base, "emotion": emo / base, "gift": np.log1p(gift)}

    if features is not None:
        b = int(bucket)
        audio = np.asarray(features["audio_db"], dtype=float)
        pad = n * b - len(audio)
        audio = np.concatenate([audio, np.full(max(0, pad), np.nan)])[: n * b].reshape(n, b)
        with np.errstate(all="ignore"):
            amax = np.nanmax(np.where(np.isnan(audio), -np.inf, audio), axis=1)
        amax[np.isinf(amax)] = np.nan
        abase = trailing_median(amax, base_win)
        raw["audio"] = np.nan_to_num(amax - abase, nan=0.0)
        scene = np.asarray(features["scene"], dtype=float)
        scene = np.concatenate([scene, np.zeros(max(0, n * b - len(scene)))])[: n * b].reshape(n, b).sum(axis=1)
        raw["scene"] = np.log1p(scene)
        if features.get("hf") is not None and len(features["hf"]):
            # 尖叫：这一秒比前 5 分钟中位数响 6 dB 以上，且高频占比也明显升高
            loud = np.asarray(features["audio_db"], dtype=float)
            hf = np.asarray(features["hf"], dtype=float)
            m = min(len(loud), len(hf), n * b)
            base_l = np.repeat(trailing_median(loud[:m][::5], 60), 5)[:m]
            base_h = np.repeat(trailing_median(hf[:m][::5], 60), 5)[:m]
            ex = np.where((loud[:m] - np.nan_to_num(base_l, nan=-90)) > 6, np.nan_to_num(hf[:m] - base_h, nan=0), 0)
            ex = np.concatenate([np.clip(ex, 0, None), np.zeros(n * b - m)]).reshape(n, b).max(axis=1)
            raw["scream"] = ex
        if features.get("sustain") is not None and len(features["sustain"]):
            sus = np.asarray(features["sustain"], dtype=float)
            sus = np.concatenate([sus, np.full(max(0, n * b - len(sus)), np.nan)])[: n * b].reshape(n, b)
            with np.errstate(all="ignore"):
                music = np.nan_to_num(np.nanmean(sus, axis=1), nan=0.0)
    if sentences:
        raw["laugh"] = np.log1p(laugh_counts(sentences, n, bucket))

    w_cfg = cfg["weights"]
    avail = [k for k in SIGNALS if k in raw and w_cfg.get(k, 0) > 0]
    total_w = sum(w_cfg[k] for k in avail) or 1.0
    weights = {k: w_cfg[k] / total_w for k in avail}
    # 限幅：防止某一路信号（比如几乎恒定、偶尔突变的音频）的 z 值爆炸，压过其他信号
    zs = {k: np.clip(robust_z(raw[k]), -4.0, 8.0) for k in avail}
    score = sum(weights[k] * zs[k] for k in avail)
    sm = max(1, int(a["smooth_seconds"] // bucket))
    score = smooth(score, sm)
    times = (np.arange(n) + 0.5) * bucket
    if features is None or features.get("sustain") is None or not len(features.get("sustain", [])):
        music = np.zeros(n)
    music = smooth(music, max(1, int(30 // bucket)))       # 唱歌是持续几分钟的事，按 30 秒平滑
    return {"times": times, "score": score, "count": count, "emotion": emo, "gift": gift, "bucket": bucket,
            "z": {k: smooth(zs[k], sm) for k in avail}, "weights": weights, "raw": raw, "music": music}


def find_top_peaks(events: list[Event], segs: list[Segment], cfg: dict,
                   features: dict | None = None, sentences: list[dict] | None = None) -> tuple[list[Peak], dict]:
    a = cfg["analysis"]
    delay, delay_note = estimate_delay(events, features["audio_db"] if features else None, cfg)
    sig = compute_signals(events, cfg, features, delay, sentences)
    sig["delay"], sig["delay_note"] = delay, delay_note
    bucket = sig["bucket"]
    score = sig["score"]
    if len(score) < 3:
        return [], sig
    dist = max(1, int(a["min_peak_gap_seconds"] // bucket))
    idx, _ = find_peaks(score, distance=dist)
    idx = sorted(idx, key=lambda i: score[i], reverse=True)
    music = sig["music"]
    sig["music_filtered"] = 0
    if a.get("music_filter"):                    # 歌回过滤：片段大部分时间在唱歌 / 放音乐的不要
        th = float(a.get("music_threshold", 0.45))
        keep = [i for i in idx if music[i] < th]
        sig["music_filtered"] = len(idx) - len(keep)
        idx = keep
    idx = idx[: int(a["top_n"])]

    before = float(a["clip_before_seconds"])
    after = float(a["clip_after_seconds"])
    danmaku = [e for e in events if e.kind == "danmaku"]
    dm_times = np.array([e.t for e in danmaku])

    peaks: list[Peak] = []
    for rank, i in enumerate(idx, start=1):
        t = float(sig["times"][i])
        # 高峰对应的弹幕在 t + delay 附近
        lo, hi = np.searchsorted(dm_times, [t + delay - 15, t + delay + 15]) if len(dm_times) else (0, 0)
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
            contrib={k: round(float(sig["weights"][k] * sig["z"][k][i]), 2) for k in sig["z"]},
            music=round(float(music[i]), 2),
        ))
    return peaks, sig
