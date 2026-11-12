#!/usr/bin/env python3
"""曲库管理 + 鼓点检测。曲子文件自己放进 assets/music，授权说明必填。

用法：
  python scripts/music.py add 曲子.mp3 --license "购买自 XX，商用授权，订单号 123" [--title 名字] [--source 链接] [--mood 轻快]
  python scripts/music.py scan            # 对还没检测的曲子跑鼓点检测（--force 全部重跑）
  python scripts/music.py list            # 看有哪些曲子、BPM、能不能用来吸附（--json 给后台用）
  python scripts/music.py beats 曲子id    # 输出这首的鼓点（后台时间轴用）
  python scripts/music.py remove 曲子id
注意：曲库为空时，剪辑器的背景音乐 / 吸附鼓点功能自动不可用，不影响其他功能。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clipper import music  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("file")
    a.add_argument("--license", required=True, dest="license_text")
    a.add_argument("--title", default="")
    a.add_argument("--source", default="")
    a.add_argument("--mood", default="")
    s = sub.add_parser("scan")
    s.add_argument("--force", action="store_true")
    ls = sub.add_parser("list")
    ls.add_argument("--json", action="store_true")
    bt = sub.add_parser("beats")
    bt.add_argument("id")
    r = sub.add_parser("remove")
    r.add_argument("id")
    args = ap.parse_args()

    try:
        if args.cmd == "add":
            t = music.register(args.file, args.title, args.license_text, args.source, args.mood)
            print(f"已登记：{t['id']}（{t['title']}）。接下来运行 scan 检测鼓点")
        elif args.cmd == "scan":
            n = 0
            for t in music.load_tracks():
                if not music.track_file(t).is_file():
                    print(f"跳过 {t['id']}：文件不存在", file=sys.stderr)
                    continue
                if music.beats_path(t).exists() and not args.force:
                    continue
                res = music.analyze(t)
                n += 1
                flag = "可吸附" if res["snap_ok"] else "节拍不清晰，不建议吸附"
                print(f"{t['id']}：{res['bpm']} BPM，{len(res['beats'])} 个鼓点，置信度 {res['confidence']}（{flag}）")
            print(f"检测了 {n} 首")
        elif args.cmd == "list":
            rows = music.summary()
            if args.json:
                print(json.dumps(rows, ensure_ascii=False))
            elif not rows:
                print("曲库是空的。把有授权的曲子放进 assets/music，再 add 登记")
            for t in [] if args.json else rows:
                print(f"{t['id']}  {t['title']}  BPM={t['bpm'] or '未检测'}  {'可吸附' if t['snap_ok'] else '-'}  授权：{t['license']}")
        elif args.cmd == "beats":                       # 给后台时间轴画鼓点刻度用：一行 JSON
            b = music.load_beats(args.id)
            if b is None:
                print("这首曲子还没有鼓点检测结果（python scripts/music.py scan）", file=sys.stderr)
                return 1
            print(json.dumps({"id": args.id, **b}, ensure_ascii=False))
        else:
            print("已删除" if music.remove(args.id) else "没有这首曲子")
        return 0
    except (music.MusicError, RuntimeError) as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
