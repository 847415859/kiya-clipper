"""音频响度、画面变化特征提取（只依赖 ffmpeg / ffprobe 和 numpy）。

为了在 2 核 2G 的录制机上也能跑：
- 音频：ffmpeg 解码成 16kHz 单声道，按 1 秒一块流式读取，内存占用很小；
- 画面：只解码关键帧（-skip_frame nokey），缩成 64x36 灰度图，比逐帧解码快一个数量级。

结果按“整场时间轴（秒）”拼接：第 i 个元素是开播后第 i 秒的值，断流的空档为 NaN。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from .danmaku import Segment

SR = 16000
FEATURE_VERSION = 2       # 2：加了高频占比（尖叫）和音高稳定比例（唱歌 / 音乐）
THUMB_W, THUMB_H = 64, 36


def _need(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise RuntimeError(f"找不到 {tool}，请先安装 ffmpeg（apt install -y ffmpeg）")
    return path


def probe_duration(video: Path) -> float | None:
    try:
        r = subprocess.run([_need("ffprobe"), "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", str(video)], capture_output=True, text=True, timeout=60)
        return float(r.stdout.strip())
    except (ValueError, subprocess.SubprocessError):
        return None


FRAME = SR // 20          # 50 ms 一帧（音高分析）
F0_MIN, F0_MAX = 80, 1000


def _block_features(x: np.ndarray) -> tuple[float, float, np.ndarray]:
    """一秒音频 → (响度 dBFS, 高频能量占比, 每 50ms 帧的音高 Hz（不是浊音为 0）)。"""
    db = 20 * np.log10(np.sqrt(np.mean(x * x)) + 1e-6)
    spec = np.abs(np.fft.rfft(x)) ** 2
    freqs = np.fft.rfftfreq(len(x), 1 / SR)
    total = spec[freqs > 80].sum() + 1e-12
    hf = float(spec[freqs > 2500].sum() / total)          # 尖叫、高音大笑：高频能量突然变多
    n = len(x) // FRAME
    f0 = np.zeros(n, dtype=np.float32)
    if n:
        fr = x[: n * FRAME].reshape(n, FRAME)
        fr = fr - fr.mean(axis=1, keepdims=True)
        energy = (fr * fr).sum(axis=1)
        sp = np.fft.rfft(fr, 2048, axis=1)
        ac = np.fft.irfft(np.abs(sp) ** 2, axis=1)[:, :FRAME]
        lo, hi = SR // F0_MAX, SR // F0_MIN
        seg = ac[:, lo:hi]
        lag = seg.argmax(axis=1) + lo
        peak = seg.max(axis=1) / (ac[:, 0] + 1e-9)
        voiced = (peak > 0.55) & (energy > 1e-4 * FRAME)
        f0[voiced] = SR / lag[voiced]
    return float(db), hf, f0


def sustain_ratio(f0: np.ndarray, min_run: int = 6, tol: float = 0.03) -> np.ndarray:
    """每帧是否处在“音高稳定地拖长”的片段里（≥ min_run 帧、相邻帧音高相差 < tol）。
    唱歌和乐器的长音很多，说话很少：按秒平均后就是音乐 / 唱歌的程度。"""
    out = np.zeros(len(f0), dtype=np.float32)
    i = 0
    while i < len(f0):
        if f0[i] <= 0:
            i += 1
            continue
        j = i + 1
        while j < len(f0) and f0[j] > 0 and abs(f0[j] / f0[j - 1] - 1) < tol:
            j += 1
        if j - i >= min_run:
            out[i:j] = 1
        i = j
    return out


def audio_features(video: Path) -> dict:
    """每秒的 响度 dB、高频占比、音高稳定比例（0~1）。没有音轨时返回空数组。"""
    cmd = [_need("ffmpeg"), "-nostdin", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1",
           "-ar", str(SR), "-f", "s16le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    dbs: list[float] = []
    hfs: list[float] = []
    f0s: list[np.ndarray] = []
    block = SR * 2  # 1 秒，16bit
    buf = b""
    assert proc.stdout is not None

    def add(b: bytes):
        x = np.frombuffer(b[: len(b) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
        db, hf, f0 = _block_features(x)
        dbs.append(db)
        hfs.append(hf)
        f0s.append(np.pad(f0, (0, 20 - len(f0))) if len(f0) < 20 else f0[:20])

    while True:
        chunk = proc.stdout.read(block - len(buf))
        if not chunk:
            break
        buf += chunk
        if len(buf) == block:
            add(buf)
            buf = b""
    if len(buf) >= 2 * SR // 4:  # 末尾不足 1 秒但超过 0.25 秒也算
        add(buf)
    proc.wait()
    f0 = np.concatenate(f0s) if f0s else np.zeros(0, dtype=np.float32)
    sus = sustain_ratio(f0).reshape(-1, 20).mean(axis=1) if len(f0) else np.zeros(0, dtype=np.float32)
    return {"db": np.array(dbs, dtype=np.float32), "hf": np.array(hfs, dtype=np.float32),
            "sustain": sus.astype(np.float32)}


def audio_loudness(video: Path) -> np.ndarray:
    """每秒的响度（dBFS，RMS）。没有音轨时返回空数组。"""
    return audio_features(video)["db"]


def keyframe_changes(video: Path) -> tuple[np.ndarray, np.ndarray]:
    """返回 (关键帧时间秒, 与上一关键帧的平均像素差 0–255)。"""
    with tempfile.TemporaryFile() as err:
        cmd = [_need("ffmpeg"), "-nostdin", "-skip_frame", "nokey", "-i", str(video), "-an",
               "-vf", f"scale={THUMB_W}:{THUMB_H},format=gray,showinfo", "-fps_mode", "passthrough",
               "-f", "rawvideo", "-"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err)
        assert proc.stdout is not None
        frames = []
        size = THUMB_W * THUMB_H
        while True:
            b = proc.stdout.read(size)
            if len(b) < size:
                break
            frames.append(np.frombuffer(b, dtype=np.uint8).astype(np.int16))
        proc.wait()
        err.seek(0)
        times = [float(m) for m in re.findall(rb"pts_time:\s*([0-9.]+)", err.read())]
    n = min(len(frames), len(times))
    if n < 2:
        return np.array([]), np.array([])
    t = np.array(times[:n])
    diffs = np.array([0.0] + [float(np.mean(np.abs(frames[i] - frames[i - 1]))) for i in range(1, n)])
    return t, diffs


def segment_features(v: Path, with_video: bool = True, cache_dir: Path | None = None, log=print):
    """一个分段的 (音频特征, 关键帧时间, 关键帧差)。

    给了 cache_dir 就按“文件名 + 大小 + 版本 + 是否含画面”缓存到 cache_dir 下：已经录完的分段不会变，
    边播边筛和下播后的完整粗筛因此共用同一份结果，下播后只需要提取最后一段。
    文件还在写的时候大小一直变，缓存命中不了，不会用到半截数据。
    """
    cf = None
    if cache_dir is not None:
        cf = cache_dir / f"{v.stem}_{v.stat().st_size}_v{FEATURE_VERSION}{'v' if with_video else 'n'}.npz"
        if cf.exists():
            try:
                d = np.load(cf, allow_pickle=False)
                af = {"db": d["db"], "hf": d["hf"], "sustain": d["sustain"]}
                log(f"  {v.name}：用缓存（音频 {len(af['db'])} 秒）")
                return af, d["kt"], d["kd"]
            except (OSError, ValueError, KeyError):
                pass                                    # 缓存坏了就重算
    af = audio_features(v)
    kt, kd = (keyframe_changes(v) if with_video else (np.array([]), np.array([])))
    log(f"  {v.name}：音频 {len(af['db'])} 秒，关键帧 {len(kt)} 个")
    if cf is not None and len(af["db"]):
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = cf.with_name(cf.name + ".tmp")
        with open(tmp, "wb") as f:
            np.savez_compressed(f, db=af["db"], hf=af["hf"], sustain=af["sustain"], kt=kt, kd=kd)
        tmp.replace(cf)
        for old in cache_dir.glob(f"{v.stem}_*.npz"):    # 同一个分段的旧大小版本没用了
            if old != cf:
                old.unlink(missing_ok=True)
    return af, kt, kd


def extract_session(segs: list[Segment], with_video: bool = True, log=print, cache_dir: Path | None = None) -> dict:
    """对一整场提取特征，按整场时间轴（秒）拼接。cache_dir：分段特征缓存目录（见 segment_features）。"""
    ends = []
    per_seg = []
    for s in segs:
        v = s.video_path
        if not v.exists() or v.stat().st_size == 0:
            log(f"  跳过（没有视频文件）：{v.name}")
            per_seg.append(None)
            ends.append(s.offset + s.last_t)
            continue
        af, kt, kd = segment_features(v, with_video, cache_dir, log)
        per_seg.append((af, kt, kd))
        ends.append(s.offset + max(len(af["db"]), s.last_t))
    total = int(np.ceil(max(ends))) + 1 if ends else 0
    audio = np.full(total, np.nan, dtype=np.float32)
    hf = np.full(total, np.nan, dtype=np.float32)
    sustain = np.full(total, np.nan, dtype=np.float32)
    scene = np.zeros(total, dtype=np.float32)
    for s, feat in zip(segs, per_seg):
        if feat is None:
            continue
        af, kt, kd = feat
        loud = af["db"]
        o = int(round(s.offset))
        n = min(len(loud), total - o)
        audio[o:o + n] = loud[:n]
        hf[o:o + n] = af["hf"][:n]
        sustain[o:o + n] = af["sustain"][:n]
        for t, d in zip(kt, kd):
            i = int(o + t)
            if 0 <= i < total:
                scene[i] += d
    return {"audio_db": audio, "scene": scene, "hf": hf, "sustain": sustain,
            "segments": [{"xml": str(s.xml_path), "offset": s.offset} for s in segs]}


def save_features(path: Path, feat: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, audio_db=feat["audio_db"], scene=feat["scene"], hf=feat["hf"], sustain=feat["sustain"],
                        meta=np.array(json.dumps({"segments": feat["segments"], "version": FEATURE_VERSION},
                                                 ensure_ascii=False)))


def load_features(path: Path) -> dict | None:
    """读缓存；旧版本缓存（没有高频 / 音高特征）返回 None，让调用方重新提取。"""
    if not path.exists():
        return None
    d = np.load(path, allow_pickle=False)
    if "hf" not in d or "sustain" not in d:
        return None
    return {"audio_db": d["audio_db"], "scene": d["scene"], "hf": d["hf"], "sustain": d["sustain"],
            "meta": json.loads(str(d["meta"]))}
