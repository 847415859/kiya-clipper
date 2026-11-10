#!/usr/bin/env python3
"""校验剪辑脚本（后台保存时调用）：通过则在标准输出打印规范化后的 JSON，失败则退出码 1、错误写到标准错误。

用法：python scripts/edit_check.py <edit.json> --clip-len 60
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clipper.edit import EditError, validate_edit  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--clip-len", type=float, required=True)
    args = ap.parse_args()
    try:
        raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
        print(json.dumps(validate_edit(raw, args.clip_len), ensure_ascii=False))
        return 0
    except (EditError, ValueError) as e:
        print(f"剪辑脚本有误：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
