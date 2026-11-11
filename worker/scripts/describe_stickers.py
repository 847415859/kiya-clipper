#!/usr/bin/env python3
"""给贴图库里的贴图写含义说明（assets/stickers/stickers.json），让 AI 编排知道每个贴图是什么、适合什么时候用。

AI 编排只看得到文件名（下载来的表情包常常叫 1743….png），不写说明它就不会用这个贴图。
用法：
  python scripts/describe_stickers.py            # 给还没有说明的贴图让多模态模型看图写一句（标 auto）
  python scripts/describe_stickers.py --force    # 连自动写的也重写（手写 / 手改过的不动）
  python scripts/describe_stickers.py --list     # 只列出每个贴图的说明状态，不调模型
写完可以直接编辑 stickers.json 改得更准；想让某个贴图不被用，给它加 "disabled": true。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper.llm import LLMClient  # noqa: E402
from clipper.stickers import describe_missing, list_files, load_meta, sticker_dir  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="连自动写的说明也重写（手写的不动）")
    ap.add_argument("--list", action="store_true", help="只列出状态，不调模型")
    ap.add_argument("--model", default="", help="多模态模型；不填用 .env 的 DASHSCOPE_MODEL")
    args = ap.parse_args()
    d = sticker_dir()
    names = list_files(d)
    if not names:
        print(f"贴图库是空的：{d}（在后台“成片编辑”里上传贴图）")
        return 0
    meta = load_meta(d)
    if args.list:
        for n in names:
            m = meta.get(n) if isinstance(meta.get(n), dict) else {}
            state = "已禁用" if m.get("disabled") else ("自动" if m.get("auto") else "手写") if m.get("meaning") else "没有说明（AI 不会用）"
            print(f"{n:<40}{state}  {m.get('meaning', '')}")
        return 0
    client = LLMClient(model=args.model or None, cache_dir=ROOT / "output" / "_llm_cache")
    res = describe_missing(client, d, args.force)
    ok = sum(1 for v in res.values() if v)
    print(f"完成：{ok}/{len(res)} 个贴图写了说明（{d / 'stickers.json'}）" if res else "每个贴图都已经有说明，没有要补的")
    return 0 if all(res.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
