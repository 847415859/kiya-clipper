#!/usr/bin/env python3
"""把 candidates.json 里的候选片段剪成小视频。

用法：
  python scripts/cut.py output/20261110-2000/candidates.json              # 精确剪切（重新编码，720p）
  python scripts/cut.py output/20261110-2000/candidates.json --fast       # 快速剪切（不重新编码，起点可能偏 1–2 秒）
  python scripts/cut.py output/20261110-2000/candidates.json --ranks 1 3 5
  python scripts/cut.py output/20261110-2000/candidates.json --height 1080

精确模式：片段跨两个录像分段时会自动拼接；重新编码保证起点准确、开头不花屏。
2 核服务器上 720p、veryfast 编码大约 1–2 倍实时速度：20 段 × 1 分钟约 10–20 分钟。
输出到 output/<场次>/clips/，文件名 序号_高峰时间.mp4
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper.media import probe_duration  # noqa: E402
from clipper.peaks import hhmmss  # noqa: E402


def pieces(segments: list[dict], start: float, end: float) -> list[tuple[Path, float, float]]:
    """把整场时间 [start, end) 映射到各分段文件内的 (文件, 文件内起点, 文件内终点)。"""
    out = []
    for i, s in enumerate(segments):
        v = Path(s["video"])
        seg_start = float(s["offset"])
        nxt = float(segments[i + 1]["offset"]) if i + 1 < len(segments) else float("inf")
        if not v.exists():
            continue
        dur = probe_duration(v) or (nxt - seg_start)
        seg_end = min(seg_start + dur, nxt)
        a, b = max(start, seg_start), min(end, seg_end)
        if b - a > 0.5:
            out.append((v, a - seg_start, b - seg_start))
    return out


def cut_one(parts: list[tuple[Path, float, float]], dst: Path, fast: bool, height: int) -> None:
    ff = shutil.which("ffmpeg") or sys.exit("找不到 ffmpeg")
    if fast:
        if len(parts) > 1:
            print(f"    提示：片段跨分段，--fast 模式只剪第一段")
        v, a, b = parts[0]
        cmd = [ff, "-nostdin", "-loglevel", "error", "-y", "-ss", f"{a:.2f}", "-i", str(v), "-t", f"{b - a:.2f}",
               "-c", "copy", str(dst)]
        subprocess.run(cmd, check=True)
        return
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for v, a, b in parts:
            f.write(f"file '{v.as_posix()}'\ninpoint {a:.3f}\noutpoint {b:.3f}\n")
        lst = f.name
    cmd = [ff, "-nostdin", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
           "-vf", f"scale=-2:'min({height},ih)'", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dst)]
    try:
        subprocess.run(cmd, check=True)
    finally:
        Path(lst).unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("candidates", help="screen.py 输出的 candidates.json")
    ap.add_argument("--ranks", type=int, nargs="+", help="只剪这些序号")
    ap.add_argument("--fast", action="store_true", help="不重新编码（快，但起点可能偏 1–2 秒）")
    ap.add_argument("--height", type=int, default=720, help="输出高度，默认 720")
    args = ap.parse_args()

    path = Path(args.candidates)
    data = json.loads(path.read_text(encoding="utf-8"))
    out_dir = path.parent / "clips"
    out_dir.mkdir(exist_ok=True)
    cands = [c for c in data["candidates"] if not args.ranks or c["rank"] in args.ranks]
    ok = 0
    for c in cands:
        parts = pieces(data["segments"], c["clip_start"], c["clip_end"])
        dst = out_dir / f"{c['rank']:02d}_{hhmmss(c['t']).replace(':', '-')}{'.flv' if args.fast else '.mp4'}"
        if not parts:
            print(f"  #{c['rank']:>2} 跳过：找不到对应的录像文件（可能已同步到 OSS 并从本地删除）")
            continue
        try:
            cut_one(parts, dst, args.fast, args.height)
            ok += 1
            print(f"  #{c['rank']:>2} {hhmmss(c['clip_start'])}–{hhmmss(c['clip_end'])} → {dst.name}"
                  + (f"（跨 {len(parts)} 个分段）" if len(parts) > 1 else ""))
        except subprocess.CalledProcessError as e:
            print(f"  #{c['rank']:>2} 剪切失败：{e}")
    print(f"完成 {ok}/{len(cands)} 段，输出在 {out_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
