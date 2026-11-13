#!/usr/bin/env python3
"""把 candidates.json 里的候选片段剪成小视频。

用法：
  python scripts/cut.py output/20261110-2000/candidates.json              # 精确剪切（重新编码，720p）
  python scripts/cut.py output/20261110-2000/candidates.json --fast       # 快速剪切（不重新编码，起点可能偏 1–2 秒）
  python scripts/cut.py output/20261110-2000/candidates.json --ranks 1 3 5
  python scripts/cut.py output/20261110-2000/candidates.json --height 1080
  python scripts/cut.py output/20261110-2000/candidates.json --sub-style kuaile   # 换字幕样式
  python scripts/cut.py output/20261110-2000/candidates.json --no-subs           # 不加字幕
  python scripts/cut.py output/20261110-2000/candidates.json --no-refined        # 不按精排，固定剪峰值前后各 30 秒

范围：这场有精排结果（rerank.json）时，按精排修正后的起止剪（审核看到的就是将要成片的长度，30–180 秒）；
没有精排，或这个候选精排失败，就用粗筛的固定窗口。实际范围记在 clips/windows.json。

字幕：场次已用 transcribe.py 转写过时，自动把主播原话烧录进视频，样式见 config/subtitles.yaml；
同时在旁边生成同名 .srt（可作为 B 站 CC 字幕上传）和 .ass。--fast 模式不烧录，只生成字幕文件。

精确模式：片段跨两个录像分段时会自动拼接；重新编码、逐帧定位，起止时间准确到帧（字幕才对得上）。
2 核服务器上 720p、veryfast 编码大约 1–2 倍实时速度：20 段 × 1 分钟约 10–20 分钟。
输出到 output/<场次>/clips/，文件名 序号_高峰时间.mp4
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper.boiler import business_cuts, keep_ranges  # noqa: E402
from clipper.clipwin import cut_window, write_windows  # noqa: E402
from clipper.kcfg import load_cfg  # noqa: E402
from clipper.streamer import resolve_config  # noqa: E402
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
            ass: Path | None = None, removed: list[dict] | None = None) -> None:
    ff = shutil.which("ffmpeg") or sys.exit("找不到 ffmpeg")
    if removed:
        # 营业话删除后必须重新编码并拼接多个区间，--fast 的关键帧剪法无法表达这个操作。
        fast = False
        if not parts:
            raise ValueError("删除营业话后没有剩余画面")
    if fast:
        if len(parts) > 1:
            print(f"    提示：片段跨分段，--fast 模式只剪第一段")
        v, a, b = parts[0]
        cmd = [ff, "-nostdin", "-loglevel", "error", "-y", "-ss", f"{a:.2f}", "-i", str(v), "-t", f"{b - a:.2f}",
               "-c", "copy", str(dst)]
        subprocess.run(cmd, check=True)
        return
    # 每段用输入端 -ss 精确定位（重新编码时 ffmpeg 会解码到准确的帧），再用 concat 滤镜拼接。
    # 不用 concat 分离器的 inpoint：那样会从起点前最近的关键帧开始，片段会多出 1–2 秒，字幕就对不上了。
    inputs, chains = [], []
    for i, (v, a, b) in enumerate(parts):
        inputs += ["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(v)]
        chains.append(f"[{i}:v:0][{i}:a:0]")
    vf = f"scale=-2:'min({height},ih)'" + (f",{ass_filter(ass)}" if ass else "")
    fc = "".join(chains) + f"concat=n={len(parts)}:v=1:a=1[cv][ca];[cv]{vf}[v]"
    cmd = [ff, "-nostdin", "-loglevel", "error", "-y", *inputs, "-filter_complex", fc, "-map", "[v]", "-map", "[ca]",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "128k",
           "-movflags", "+faststart", str(dst)]
    subprocess.run(cmd, check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("candidates", help="screen.py 输出的 candidates.json")
    ap.add_argument("--ranks", type=int, nargs="+", help="只剪这些序号")
    ap.add_argument("--fast", action="store_true", help="不重新编码（快，但起点可能偏 1–2 秒）")
    ap.add_argument("--height", type=int, default=720, help="输出高度，默认 720")
    ap.add_argument("--no-subs", action="store_true", help="不加字幕")
    ap.add_argument("--no-refined", action="store_true", help="不按精排修正后的起止，固定剪粗筛窗口")
    ap.add_argument("--sub-style", help="字幕样式名（config/subtitles.yaml 的 styles），默认用 active")
    ap.add_argument("--sub-style-json", help="直接给一套样式的 JSON 文件（以后后台管理下发任务时用，结构见 schema）")
    ap.add_argument("--config", default=None, help="主播配置（用于关键词换色）；不填时按 candidates.json 所在目录推导主播")
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
    transcript = load_transcript(path.parent / "transcript.json")
    tr = None if args.no_subs else transcript
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
            cfg_path = resolve_config(args.config, path.parent)
            streamer = load_cfg(cfg_path) if cfg_path.exists() else {}
            subs = {"style": style, "corrections": sub_cfg.get("corrections") or {}, "keywords": lore_keywords(streamer)}
            print(f"字幕：样式 {style_name}（{style['name']}）" + ("，--fast 模式只生成字幕文件不烧录" if args.fast else ""))
            if not font_available(style["font"]):
                print(f"  注意：没找到字体 {style['font']}，字幕可能显示成方块。安装方法见 worker/README.md“字幕”一节")
    elif not args.no_subs:
        print("提示：这场还没有转写（transcript.json），剪出来的视频没有字幕。先运行 transcribe.py")
    refined_by_rank: dict[int, dict] = {}
    if not args.no_refined:
        try:
            rr = json.loads((path.parent / "rerank.json").read_text(encoding="utf-8"))
            refined_by_rank = {r["rank"]: r["refined"] for r in rr.get("candidates", [])
                               if isinstance(r.get("refined"), dict)
                               and abs(float(r.get("clip_start", -1e9)) - next(
                                   (c["clip_start"] for c in data["candidates"] if c["rank"] == r["rank"]), 1e9)) <= 1}
        except (OSError, ValueError, KeyError):
            refined_by_rank = {}
    windows: dict[str, dict] = {}
    ok = 0

    def compact_events(events: list[dict], origin: float, removed: list[dict]) -> list[dict]:
        """删除营业话后，把字幕事件和逐字时间映射到拼接后的视频时间轴。"""
        def shift(t: float) -> float:
            return sum(max(0.0, min(t, float(q["end"])) - float(q["start"]))
                    for q in removed if t > float(q["start"]))

        out = []
        for event in events:
            ea, eb = origin + float(event["t0"]), origin + float(event["t1"])
            if any(ea < float(q["end"]) and eb > float(q["start"]) for q in removed):
                continue
            e = dict(event)
            e["t0"] = round(ea - origin - shift(ea), 3)
            e["t1"] = round(eb - origin - shift(eb), 3)
            e["words"] = [[round(origin + float(a) - origin - shift(origin + float(a)), 3),
                            round(origin + float(b) - origin - shift(origin + float(b)), 3), w]
                           for a, b, w in event.get("words", [])
                           if not any(origin + float(a) < float(q["end"]) and origin + float(b) > float(q["start"])
                                      for q in removed)]
            if e["t1"] > e["t0"] and e.get("words"):
                out.append(e)
        return out

    for c in cands:
        refined = refined_by_rank.get(c["rank"])
        cs, ce, by_refined = cut_window(c, refined)
        removed = (refined or {}).get("removed") or []
        if not removed and transcript:
            # 没有精排结果时也过滤候选窗口里的纯营业话；有精排结果则使用其经过边界修正的结果。
            removed = business_cuts(transcript["sentences"], cs, ce)
        kept = keep_ranges(cs, ce, removed)
        parts = [part for a, b in kept for part in pieces(data["segments"], a, b)]
        dst = out_dir / f"{c['rank']:02d}_{hhmmss(c['t']).replace(':', '-')}{'.mp4' if removed else ('.flv' if args.fast else '.mp4')}"
        if not parts:
            print(f"  #{c['rank']:>2} 跳过：找不到对应的录像文件（可能已同步到 OSS 并从本地删除）")
            continue
        ass = None
        if subs:
            events = build_events(tr["sentences"], cs, ce, subs["style"], subs["corrections"])
            if removed:
                events = compact_events(events, cs, removed)
            w, h = probe_size(parts[0][0])
            dst.with_suffix(".srt").write_text(to_srt(events), encoding="utf-8")
            ass = dst.with_suffix(".ass")
            ass.write_text(to_ass(events, subs["style"], w, h, subs["keywords"], subs["corrections"]), encoding="utf-8")
            if args.fast or not events:
                ass = None
        try:
            cut_one(parts, dst, args.fast, args.height, ass, removed)
            ok += 1
            windows[f"{c['rank']:02d}"] = {"start": round(cs, 2), "end": round(ce, 2), "refined": by_refined,
                                             "removed": removed, "output_duration": round(sum(b - a for a, b in kept), 2)}
            print(f"  #{c['rank']:>2} {hhmmss(cs)}–{hhmmss(ce)}（{ce - cs:.0f} 秒{'，按精排' if by_refined else ''}）→ {dst.name}"
                  + (f"（跨 {len(parts)} 个分段）" if len(parts) > 1 else ""))
        except subprocess.CalledProcessError as e:
            print(f"  #{c['rank']:>2} 剪切失败：{e}")
    if windows:
        write_windows(path.parent, windows)
    print(f"完成 {ok}/{len(cands)} 段，输出在 {out_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
