#!/usr/bin/env python3
"""字幕工具：查看样式、出预览图、只生成字幕文件。

用法：
  python scripts/subtitle.py styles                                   # 列出样式，检查字体装没装
  python scripts/subtitle.py preview --all                            # 每套样式出一张预览图（纯色背景）
  python scripts/subtitle.py preview --style kuaile --video output/<场次>/clips/01_xxx.mp4 --at 12
  python scripts/subtitle.py preview --style box --text "人类酱晚上好 舰长寄来了律师函？"
  python scripts/subtitle.py make output/<场次>/candidates.json       # 只生成 .srt / .ass（不重新剪视频）

预览图保存到 output/sub_preview/<样式>.png。样式在 config/subtitles.yaml 里改，字段说明见
config/schema/subtitle_style.schema.json。改完先出预览图看效果，满意了再用 cut.py 剪。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from clipper.asr import load_transcript  # noqa: E402
from clipper.clipwin import read_windows  # noqa: E402
from clipper.streamer import resolve_config, streamer_from_args  # noqa: E402
from clipper.subtitles import (StyleError, ass_filter, build_events, font_available, get_style,  # noqa: E402
                               load_config, lore_keywords, to_ass, to_srt, validate_style)

SAMPLE = "有没有做过什么对不起舰长的事儿啊？人类酱晚上好喵，舰长寄来了律师函！"


def streamer_cfg(path: str) -> dict:
    p = Path(path)
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}


def cmd_styles(args) -> int:
    cfg = load_config()
    for name in cfg.get("styles") or {}:
        try:
            _, st = get_style(name, cfg)
            font = "字体已安装" if font_available(st["font"]) else "⚠ 字体未安装"
            print(f"  {'*' if name == cfg.get('active') else ' '} {name:<10} {st['name']:<10} {st['font']}（{font}）")
        except StyleError as e:
            print(f"    {name:<10} 配置有误：{e}")
    print("  * 为当前默认样式（config/subtitles.yaml 的 active）")
    return 0


def sample_event(text: str, dur: float) -> dict:
    chars = list(text)
    step = dur / max(1, len(chars))
    words = [[i * step, (i + 1) * step, ch] for i, ch in enumerate(chars)]
    return {"t0": 0.0, "t1": 5.0, "words": words, "text": text}


def render_preview(style: dict, out: Path, video: str | None, at: float, text: str, keywords: list[str],
                   corrections: dict) -> None:
    step = 0.12                                             # 每字 0.12 秒，1 秒时逐字变色样式正好读到一半左右
    sent = {"t0": 0.0, "t1": step * len(text), "text": text,
            "words": [[i * step, (i + 1) * step, ch] for i, ch in enumerate(text)]}
    events = build_events([sent], 0.0, 5.0, style, corrections)
    events = events[:1] or [sample_event(text, 1.6)]
    events[0]["t0"], events[0]["t1"] = 0.0, 5.0            # 预览只显示第一条
    if video:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "csv=p=0:s=x", video], capture_output=True, text=True)
        w, h = (int(x) for x in r.stdout.strip().split("x")[:2])
        src = ["-ss", str(at), "-i", video, "-t", "2"]
    else:
        w, h = 1920, 1080
        src = ["-f", "lavfi", "-i", "color=c=0x6f8fb3:s=1920x1080:d=2", "-t", "2"]
    with tempfile.TemporaryDirectory() as tmp:
        ass = Path(tmp) / "p.ass"
        ass.write_text(to_ass(events, style, w, h, keywords, corrections), encoding="utf-8")
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", *src, "-vf", ass_filter(ass),
               "-ss", "1.0", "-frames:v", "1", str(out)]
        subprocess.run(cmd, check=True)


def cmd_preview(args) -> int:
    cfg = load_config()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    kw = lore_keywords(streamer_cfg(resolve_config(args.config, streamer=streamer_from_args(args.streamer))))
    if args.style_json:                     # 后台编辑中的样式：直接给 JSON，不必先存进配置
        try:
            st = validate_style(json.loads(Path(args.style_json).read_text(encoding="utf-8")))
        except (StyleError, ValueError) as e:
            print(f"样式有误：{e}", file=sys.stderr)
            return 1
        out = out_dir / f"{args.name or Path(args.style_json).stem}.png"
        render_preview(st, out, args.video, args.at, args.text, kw, cfg.get("corrections") or {})
        print(f"  → {out}")
        return 0
    names = list(cfg.get("styles") or {}) if args.all else [args.style] if args.style else [None]
    for name in names:
        try:
            name, st = get_style(name, cfg)
        except StyleError as e:
            print(f"  {name}：配置有误，跳过：{e}")
            continue
        out = out_dir / f"{name}.png"
        render_preview(st, out, args.video, args.at, args.text, kw, cfg.get("corrections") or {})
        warn = "" if font_available(st["font"]) else f"（⚠ 没装字体 {st['font']}，图里可能是方块或替代字体）"
        print(f"  {name}（{st['name']}）→ {out}{warn}")
    return 0


def cmd_make(args) -> int:
    path = Path(args.candidates)
    data = json.loads(path.read_text(encoding="utf-8"))
    tr = load_transcript(path.parent / "transcript.json")
    if not tr:
        print("这场还没有转写，先运行 transcribe.py", file=sys.stderr)
        return 1
    cfg = load_config()
    try:
        name, st = get_style(args.style, cfg)
    except StyleError as e:
        print(e, file=sys.stderr)
        return 1
    kw = lore_keywords(streamer_cfg(resolve_config(args.config, streamer=streamer_from_args(args.streamer))))
    out_dir = path.parent / "clips"
    out_dir.mkdir(exist_ok=True)
    wins = read_windows(path.parent)
    for c in data["candidates"]:
        if args.ranks and c["rank"] not in args.ranks:
            continue
        w = wins.get(f"{c['rank']:02d}") or {}           # 审核片段的实际范围（按精排剪时和候选窗口不同）
        ev = build_events(tr["sentences"], w.get("start", c["clip_start"]), w.get("end", c["clip_end"]), st,
                          cfg.get("corrections") or {})
        stem = out_dir / f"{c['rank']:02d}_{_hms(c['t'])}"
        stem.with_suffix(".srt").write_text(to_srt(ev), encoding="utf-8")
        stem.with_suffix(".ass").write_text(to_ass(ev, st, keywords=kw, corrections=cfg.get("corrections")),
                                            encoding="utf-8")
        print(f"  #{c['rank']:>2} {len(ev)} 条字幕 → {stem.name}.srt / .ass")
    print(f"样式 {name}；输出在 {out_dir}")
    return 0


def _hms(t: float) -> str:
    t = max(0, int(round(t)))
    return f"{t // 3600:02d}-{t % 3600 // 60:02d}-{t % 60:02d}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None, help="主播配置（用于关键词换色）；不填时按 --streamer 找 config/streamers/<id>.yaml")
    ap.add_argument("--streamer", default=None, help="主播标识（默认 KIYA_STREAMER 环境变量，再默认 kiya）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("styles", help="列出样式")
    p = sub.add_parser("preview", help="出预览图")
    p.add_argument("--style")
    p.add_argument("--all", action="store_true")
    p.add_argument("--style-json", help="直接给一套样式的 JSON 文件（后台预览用）")
    p.add_argument("--name", help="配合 --style-json：输出图片名")
    p.add_argument("--video", help="用这个视频的画面做背景")
    p.add_argument("--at", type=float, default=5.0, help="取视频第几秒的画面")
    p.add_argument("--text", default=SAMPLE)
    p.add_argument("--out", default=str(ROOT / "output" / "sub_preview"))
    m = sub.add_parser("make", help="只生成字幕文件")
    m.add_argument("candidates")
    m.add_argument("--style")
    m.add_argument("--ranks", type=int, nargs="+")
    args = ap.parse_args()
    return {"styles": cmd_styles, "preview": cmd_preview, "make": cmd_make}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
