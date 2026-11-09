#!/usr/bin/env python3
"""把 candidates.json 里的候选片段剪成小视频。

用法：
  python scripts/cut.py output/20261110-2000/candidates.json              # 精确剪切（重新编码，720p）
  python scripts/cut.py output/20261110-2000/candidates.json --fast       # 快速剪切（不重新编码，起点可能偏 1–2 秒）
  python scripts/cut.py output/20261110-2000/candidates.json --ranks 1 3 5
  python scripts/cut.py output/20261110-2000/candidates.json --height 1080
  python scripts/cut.py output/20261110-2000/candidates.json --sub-style kuaile   # 换字幕样式
  python scripts/cut.py output/20261110-2000/candidates.json --no-subs           # 不加字幕

字幕：场次已用 transcribe.py 转写过时，自动把主播原话烧录进视频，样式见 config/subtitles.yaml；
同时在旁边生成同名 .srt（可作为 B 站 CC 字幕上传）和 .ass。--fast 模式不烧录，只生成字幕文件。

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

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper.asr import load_transcript  # noqa: E402
from clipper.media import probe_duration  # noqa: E402
from clipper.subtitles import (StyleError, ass_filter, build_events, font_available, get_style,  # noqa: E402
                               load_config, lore_keywords, to_ass, to_srt, validate_style)
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


def probe_size(video: Path) -> tuple[int, int]:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0:s=x", str(video)], capture_output=True, text=True)
    try:
        w, h = r.stdout.strip().splitlines()[0].split("x")[:2]
        return int(w), int(h)
    except (ValueError, IndexError):
        return 1920, 1080


def cut_one(parts: list[tuple[Path, float, float]], dst: Path, fast: bool, height: int,
            ass: Path | None = None) -> None:
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
           "-vf", f"scale=-2:'min({height},ih)'" + (f",{ass_filter(ass)}" if ass else ""), "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
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
    ap.add_argument("--no-subs", action="store_true", help="不加字幕")
    ap.add_argument("--sub-style", help="字幕样式名（config/subtitles.yaml 的 styles），默认用 active")
    ap.add_argument("--sub-style-json", help="直接给一套样式的 JSON 文件（以后后台管理下发任务时用，结构见 schema）")
    ap.add_argument("--config", default=str(ROOT.parent / "config" / "kiya.yaml"), help="主播配置（用于关键词换色）")
    args = ap.parse_args()

    path = Path(args.candidates)
    data = json.loads(path.read_text(encoding="utf-8"))
    out_dir = path.parent / "clips"
    out_dir.mkdir(exist_ok=True)
    cands = [c for c in data["candidates"] if not args.ranks or c["rank"] in args.ranks]
    if not cands:
        print("没有要剪的候选（这场没有达到分数线的片段）" if not data["candidates"] else "没有匹配的序号")
        return 0
    subs = None
    tr = None if args.no_subs else load_transcript(path.parent / "transcript.json")
    if tr:
        try:
            sub_cfg = load_config()
            if args.sub_style_json:
                style = validate_style(json.loads(Path(args.sub_style_json).read_text(encoding="utf-8")))
                style_name = Path(args.sub_style_json).stem
            else:
                style_name, style = get_style(args.sub_style, sub_cfg)
        except (StyleError, OSError, json.JSONDecodeError) as e:
            print(f"字幕配置有误，本次不加字幕：{e}")
        else:
            streamer = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) if Path(args.config).exists() else {}
            subs = {"style": style, "corrections": sub_cfg.get("corrections") or {}, "keywords": lore_keywords(streamer)}
            print(f"字幕：样式 {style_name}（{style['name']}）" + ("，--fast 模式只生成字幕文件不烧录" if args.fast else ""))
            if not font_available(style["font"]):
                print(f"  注意：没找到字体 {style['font']}，字幕可能显示成方块。安装方法见 worker/README.md“字幕”一节")
    elif not args.no_subs:
        print("提示：这场还没有转写（transcript.json），剪出来的视频没有字幕。先运行 transcribe.py")
    ok = 0
    for c in cands:
        parts = pieces(data["segments"], c["clip_start"], c["clip_end"])
        dst = out_dir / f"{c['rank']:02d}_{hhmmss(c['t']).replace(':', '-')}{'.flv' if args.fast else '.mp4'}"
        if not parts:
            print(f"  #{c['rank']:>2} 跳过：找不到对应的录像文件（可能已同步到 OSS 并从本地删除）")
            continue
        ass = None
        if subs:
            events = build_events(tr["sentences"], c["clip_start"], c["clip_end"], subs["style"], subs["corrections"])
            w, h = probe_size(parts[0][0])
            dst.with_suffix(".srt").write_text(to_srt(events), encoding="utf-8")
            ass = dst.with_suffix(".ass")
            ass.write_text(to_ass(events, subs["style"], w, h, subs["keywords"], subs["corrections"]), encoding="utf-8")
            if args.fast or not events:
                ass = None
        try:
            cut_one(parts, dst, args.fast, args.height, ass)
            ok += 1
            print(f"  #{c['rank']:>2} {hhmmss(c['clip_start'])}–{hhmmss(c['clip_end'])} → {dst.name}"
                  + (f"（跨 {len(parts)} 个分段）" if len(parts) > 1 else ""))
        except subprocess.CalledProcessError as e:
            print(f"  #{c['rank']:>2} 剪切失败：{e}")
    print(f"完成 {ok}/{len(cands)} 段，输出在 {out_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
