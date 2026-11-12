#!/usr/bin/env python3
"""校验剪辑脚本（后台保存时调用）：通过则在标准输出打印 {"edit": 规范化后的脚本, "warnings": [提醒]}，
失败则退出码 1、错误写到标准错误。提醒包括效果太密、贴图不在贴图库（不阻止保存）。

用法：python scripts/edit_check.py <edit.json> --clip-len 60
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os  # noqa: E402

from clipper import callbacks as CB  # noqa: E402
from clipper import music as M  # noqa: E402
from clipper.beats import SNAP_MIN_CONFIDENCE  # noqa: E402
from clipper.edit import EditError, density_problems, missing_assets, snap_to_beats, validate_edit  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def music_warnings(edit: dict, clip_len: float) -> list[str]:
    """背景音乐：检查曲库里有没有这首、勾了“吸附鼓点”就把效果挪到鼓点上（edit 原地修改）。没有曲库时给提示，不阻止保存。"""
    m = edit.get("music")
    if not m:
        return []
    t = M.get_track(m["track"])
    if not t or not M.track_file(t).is_file():
        return [f"曲库里没有 {m['track']}，渲染会失败（取消背景音乐，或把曲子放进 assets/music 并登记）"]
    if not m["snap"]:
        return []
    b = M.load_beats(m["track"])
    if not b or not b.get("beats"):
        return ["这首曲子还没做鼓点检测（python scripts/music.py scan），没有吸附"]
    if not b.get("snap_ok"):
        return [f"这首曲子节拍不清晰（置信度 {b.get('confidence')} < {SNAP_MIN_CONFIDENCE}），没有吸附"]
    w = edit.get("window")
    length = (w["end"] - w["start"]) if w else clip_len
    edit["items"], _ = snap_to_beats(edit["items"], b["beats"], m["start"], length)
    return []


def callback_warnings(edit: dict, sess_dir: Path) -> list[str]:
    """跨场次呼应：mode=clip 却找不到旧录像时提醒（渲染会自动改成文字卡，不会悄悄省掉）。"""
    cb = edit.get("callback")
    if not cb or not cb.get("enabled", True) or cb.get("mode") != "clip":
        return []
    if not CB.footage_available(sess_dir.parent, cb["session"], cb["start"], cb["end"]):
        return [f"呼应的旧录像（{cb.get('date') or cb['session']}）已经不在本地，渲染时会改成开头的文字卡"]
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--clip-len", type=float, required=True)
    args = ap.parse_args()
    try:
        raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
        edit = validate_edit(raw, args.clip_len)
        warnings = music_warnings(edit, args.clip_len)
        warnings += callback_warnings(edit, Path(args.file).resolve().parent.parent)
        warnings += density_problems(edit)
        stickers = os.environ.get("KIYA_STICKERS") or REPO / "assets" / "stickers"
        warnings += [f"贴图库里没有 {a}，渲染会失败" for a in missing_assets(edit, stickers)]
        print(json.dumps({"edit": edit, "warnings": warnings}, ensure_ascii=False))
        return 0
    except (EditError, ValueError) as e:
        print(f"剪辑脚本有误：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
