"""鼓点检测：只用 numpy + ffmpeg（不装 librosa，免得拖重 2G 服务器）。

流程：解码成单声道 → 谱通量得到“起音强度” → 自相关估计 BPM（60–180）→ 按相位对齐得到整首的鼓点时刻。
假定曲子节拍稳定（BGM 一般如此）；没有清晰节拍的曲子 confidence 很低，不要拿来吸附（SNAP_MIN_CONFIDENCE）。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np

SR = 22050
N_FFT = 1024
HOP = 512
BPM_MIN, BPM_MAX = 60.0, 180.0
ONSET_DELAY = 0.025                 # 起音在谱通量里出现得比真实时刻早约 25ms，用合成节拍校准（真实音乐的起音更缓，误差约在 ±50ms 内）
SNAP_MIN_CONFIDENCE = 0.35          # 低于它的曲子不用来吸附


def decode_mono(path: Path, sr: int = SR) -> np.ndarray:
    ff = shutil.which("ffmpeg")
    if not ff:
        raise RuntimeError("找不到 ffmpeg")
    r = subprocess.run([ff, "-nostdin", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                       capture_output=True)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"解码失败：{r.stderr.decode('utf-8', 'replace')[-200:]}")
    return np.frombuffer(r.stdout, dtype=np.float32)


def onset_envelope(y: np.ndarray, sr: int = SR) -> tuple[np.ndarray, float]:
    """谱通量（只算 100Hz–5kHz 的频段，对鼓点最敏感）。返回 (强度, 每秒帧数)。"""
    if len(y) < N_FFT * 4:
        return np.zeros(0), sr / HOP
    win = np.hanning(N_FFT).astype(np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(y, N_FFT)[::HOP]
    mag = np.abs(np.fft.rfft(frames * win, axis=1))
    lo, hi = int(100 * N_FFT / sr), int(5000 * N_FFT / sr)
    mag = np.log1p(10.0 * mag[:, lo:hi])
    flux = np.maximum(mag[1:] - mag[:-1], 0).sum(axis=1)
    flux = np.concatenate([[0.0], flux])
    flux -= np.convolve(flux, np.ones(21) / 21, mode="same")          # 去掉缓慢的底噪起伏
    flux = np.maximum(flux, 0)
    peak = flux.max()
    return (flux / peak if peak > 0 else flux), sr / HOP


def estimate_tempo(env: np.ndarray, fps: float) -> tuple[float, float]:
    """自相关估计周期（帧，可以是小数）和峰值强度（0–1）。偏好 90–150 BPM（避免二倍 / 半速）。"""
    x = env - env.mean()
    n = len(x)
    ac = np.correlate(x, x, mode="full")[n - 1:]
    if ac[0] <= 0:
        return 0.0, 0.0
    ac = ac / ac[0]
    lo, hi = int(fps * 60 / BPM_MAX), int(fps * 60 / BPM_MIN)
    hi = min(hi, len(ac) - 2)
    if hi <= lo + 1:
        return 0.0, 0.0
    lags = np.arange(lo, hi + 1)
    bpm = 60 * fps / lags
    prior = np.exp(-0.5 * (np.log2(bpm / 120.0) / 0.6) ** 2)          # 以 120 BPM 为中心的先验
    score = ac[lo:hi + 1] * prior
    i = int(np.argmax(score))
    lag = float(lags[i])
    if 0 < i < len(lags) - 1:                                          # 抛物线插值，得到小数周期
        a, b, c = ac[lags[i] - 1], ac[lags[i]], ac[lags[i] + 1]
        d = a - 2 * b + c
        if d < 0:
            lag += 0.5 * (a - c) / d
    return lag, float(max(ac[lags[i]], 0.0))


def _comb_score(env_d: np.ndarray, lag: float, phase: float) -> float:
    idx = np.round(np.arange(phase, len(env_d), lag)).astype(int)
    idx = idx[idx < len(env_d)]
    return float(env_d[idx].mean()) if len(idx) else 0.0


def beats_from_envelope(env: np.ndarray, fps: float) -> dict:
    if len(env) < fps * 4:
        return {"bpm": 0.0, "beats": [], "confidence": 0.0}
    lag0, peak = estimate_tempo(env, fps)
    if lag0 <= 0:
        return {"bpm": 0.0, "beats": [], "confidence": 0.0}
    env_d = np.maximum(env, np.maximum(np.roll(env, 1), np.roll(env, -1)))   # 容忍 ±1 帧（23ms）的量化误差
    best = (-1.0, lag0, 0.0)
    for lag in np.linspace(lag0 * 0.97, lag0 * 1.03, 61):                  # 周期在估计值附近精调：节拍越多，微小的周期误差累计越大
        for phase in np.arange(0, lag, 0.5):
            sc = _comb_score(env_d, float(lag), float(phase))
            if sc > best[0]:
                best = (sc, float(lag), float(phase))
    score, lag, phase = best
    ratio = score / (env_d.mean() + 1e-9)                                  # 鼓点处比平均强多少倍
    confidence = float(np.clip((ratio - 1.0) / 3.0, 0, 1) * np.clip(peak * 2.5, 0, 1))
    times = [round(float(t) + ONSET_DELAY, 3) for t in np.arange(phase, len(env), lag) / fps]
    return {"bpm": round(60 * fps / lag, 1), "beats": [t for t in times if t >= 0], "confidence": round(confidence, 3)}


def detect_beats(path: Path) -> dict:
    y = decode_mono(Path(path))
    env, fps = onset_envelope(y)
    out = beats_from_envelope(env, fps)
    out["duration"] = round(len(y) / SR, 2)
    out["snap_ok"] = bool(out["beats"]) and out["confidence"] >= SNAP_MIN_CONFIDENCE
    return out
