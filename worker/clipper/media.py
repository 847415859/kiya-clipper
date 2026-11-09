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


def audio_loudness(video: Path) -> np.ndarray:
    """每秒的响度（dBFS，RMS）。没有音轨时返回空数组。"""
    cmd = [_need("ffmpeg"), "-nostdin", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1",
           "-ar", str(SR), "-f", "s16le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    out: list[float] = []
    block = SR * 2  # 1 秒，16bit
    buf = b""
    assert proc.stdout is not None
    while True:
        chunk = proc.stdout.read(block - len(buf))
        if not chunk:
            break
        buf += chunk
        if len(buf) == block:
            x = np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768.0
            out.append(20 * np.log10(np.sqrt(np.mean(x * x)) + 1e-6))
            buf = b""
    if len(buf) >= 2 * SR // 4:  # 末尾不足 1 秒但超过 0.25 秒也算
        x = np.frombuffer(buf[: len(buf) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
        out.append(20 * np.log10(np.sqrt(np.mean(x * x)) + 1e-6))
    proc.wait()
    return np.array(out, dtype=np.float32)


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


def extract_session(segs: list[Segment], with_video: bool = True, log=print) -> dict:
    """对一整场提取特征，按整场时间轴（秒）拼接。"""
    ends = []
    per_seg = []
    for s in segs:
        v = s.video_path
        if not v.exists() or v.stat().st_size == 0:
            log(f"  跳过（没有视频文件）：{v.name}")
            per_seg.append(None)
            ends.append(s.offset + s.last_t)
            continue
        loud = audio_loudness(v)
        kt, kd = (keyframe_changes(v) if with_video else (np.array([]), np.array([])))
        log(f"  {v.name}：音频 {len(loud)} 秒，关键帧 {len(kt)} 个")
        per_seg.append((loud, kt, kd))
        ends.append(s.offset + max(len(loud), s.last_t))
    total = int(np.ceil(max(ends))) + 1 if ends else 0
    audio = np.full(total, np.nan, dtype=np.float32)
    scene = np.zeros(total, dtype=np.float32)
    for s, feat in zip(segs, per_seg):
        if feat is None:
            continue
        loud, kt, kd = feat
        o = int(round(s.offset))
        n = min(len(loud), total - o)
        audio[o:o + n] = loud[:n]
        for t, d in zip(kt, kd):
            i = int(o + t)
            if 0 <= i < total:
                scene[i] += d
    return {"audio_db": audio, "scene": scene,
            "segments": [{"xml": str(s.xml_path), "offset": s.offset} for s in segs]}


def save_features(path: Path, feat: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, audio_db=feat["audio_db"], scene=feat["scene"],
                        meta=np.array(json.dumps({"segments": feat["segments"]}, ensure_ascii=False)))


def load_features(path: Path) -> dict | None:
    if not path.exists():
        return None
    d = np.load(path, allow_pickle=False)
    return {"audio_db": d["audio_db"], "scene": d["scene"], "meta": json.loads(str(d["meta"]))}
