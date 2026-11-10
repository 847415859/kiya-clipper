#!/usr/bin/env python3
"""成片编辑器用的预览视频：把素材范围（可以超出候选片段）剪成 480p、烧好字幕。

用法：
  python scripts/edit_preview.py output/<场次>/candidates.json --rank 3                     # 按剪辑脚本 / 精排建议的范围
  python scripts/edit_preview.py output/<场次>/candidates.json --rank 3 --start 5085 --end 5170   # 指定整场秒数

输出 output/<场次>/edit_preview/<序号>_<起>_<止>.mp4（同一序号只留最新一个）。素材范围就是候选片段本身时不用剪，
编辑器直接用审核片段。录像被同步脚本移走后剪不了（先从 OSS 拉回来）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from clipper.kcfg import load_cfg  # noqa: E402
from clipper.streamer import resolve_config  # noqa: E402
from clipper.asr import load_transcript  # noqa: E402
from clipper.edit import EditError, suggest_window, validate_window  # noqa: E402
from clipper.peaks import hhmmss  # noqa: E402
from clipper.subtitles import StyleError, build_events, get_style, load_config, lore_keywords, to_ass  # noqa: E402
from cut import cut_one, pieces, probe_size  # noqa: E402


def preview_name(rank: int, start: float, end: float) -> str:
    return f"{rank:02d}_{start:.1f}_{end:.1f}.mp4"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("candidates")
    ap.add_argument("--rank", type=int, required=True)
    ap.add_argument("--start", type=float, help="素材范围起点（整场秒数）")
    ap.add_argument("--end", type=float)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--config", default=None, help="主播配置；不填时按 candidates.json 所在目录推导主播")
    args = ap.parse_args()

    cpath = Path(args.candidates)
    sess = cpath.parent
    data = json.loads(cpath.read_text(encoding="utf-8"))
    cand = next((c for c in data["candidates"] if c["rank"] == args.rank), None)
    if not cand:
        print(f"没有序号 {args.rank} 的候选", file=sys.stderr)
        return 1
    try:
        if args.start is not None and args.end is not None:
            win = validate_window({"start": args.start, "end": args.end})
        else:
            edit_f = sess / "edits" / f"{args.rank:02d}.json"
            edit = json.loads(edit_f.read_text(encoding="utf-8")) if edit_f.exists() else {}
            if edit.get("window"):
                win = validate_window(edit["window"])
            else:
                rr = sess / "rerank.json"
                ref = None
                if rr.exists():
                    ref = next((r.get("refined") for r in json.loads(rr.read_text(encoding="utf-8"))["candidates"]
                                if r["rank"] == args.rank), None)
                win = suggest_window(cand, ref)
    except (EditError, ValueError) as e:
        print(f"素材范围不对：{e}", file=sys.stderr)
        return 1
    a, b = win["start"], win["end"]
    out_dir = sess / "edit_preview"
    out_dir.mkdir(exist_ok=True)
    dst = out_dir / preview_name(args.rank, a, b)
    print(f"#{args.rank} 素材范围 {hhmmss(a)}–{hhmmss(b)}（{b - a:.0f} 秒；候选片段 {hhmmss(cand['clip_start'])}–{hhmmss(cand['clip_end'])}）")
    if dst.exists():
        print(f"预览已存在：{dst.name}")
        return 0
    parts = pieces(data["segments"], a, b)
    if not parts:
        print("找不到对应的录像文件（可能已同步到 OSS 并从本地删除，先拉回来）", file=sys.stderr)
        return 1
    covered = sum(p[2] - p[1] for p in parts)
    if covered < (b - a) - 1:
        print(f"注意：录像只覆盖了 {covered:.0f}/{b - a:.0f} 秒（可能断流或分段缺失），预览会比素材范围短，时间会对不上")
    ass = None
    tr = load_transcript(sess / "transcript.json")
    if tr:
        try:
            sub_cfg = load_config()
            _, style = get_style(None, sub_cfg)
            streamer = load_cfg(resolve_config(args.config, sess)) if resolve_config(args.config, sess).exists() else {}
            events = build_events(tr["sentences"], a, b, style, sub_cfg.get("corrections") or {})
            if events:
                w, h = probe_size(parts[0][0])
                ass = dst.with_suffix(".ass")
                ass.write_text(to_ass(events, style, w, h, lore_keywords(streamer), sub_cfg.get("corrections") or {}),
                               encoding="utf-8")
        except StyleError as e:
            print(f"字幕样式有误，预览不加字幕：{e}")
    tmp = dst.with_suffix(".part.mp4")
    t0 = time.time()
    cut_one(parts, tmp, False, args.height, ass)
    tmp.replace(dst)
    for old in out_dir.glob(f"{args.rank:02d}_*"):          # 同一序号只留最新的预览
        if old.stem != dst.stem and old.suffix in (".mp4", ".ass"):
            old.unlink(missing_ok=True)
    print(f"预览已生成：{dst.name}（{time.time() - t0:.0f} 秒）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
