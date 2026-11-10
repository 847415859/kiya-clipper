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

from clipper.edit import EditError, density_problems, missing_assets, validate_edit  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--clip-len", type=float, required=True)
    args = ap.parse_args()
    try:
        raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
        edit = validate_edit(raw, args.clip_len)
        warnings = density_problems(edit)
        stickers = os.environ.get("KIYA_STICKERS") or REPO / "assets" / "stickers"
        warnings += [f"贴图库里没有 {a}，渲染会失败" for a in missing_assets(edit, stickers)]
        print(json.dumps({"edit": edit, "warnings": warnings}, ensure_ascii=False))
        return 0
    except (EditError, ValueError) as e:
        print(f"剪辑脚本有误：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
