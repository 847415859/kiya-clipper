#!/usr/bin/env python3
"""转写全文索引：把整场转写按句入库（MySQL ngram 全文索引），给“跨场次呼应”检索用。

用法：
  python scripts/transcript_index.py index output/kiya/20261109-2014        # 一场入库（重复运行会覆盖这一场）
  python scripts/transcript_index.py index output/kiya/20261109-2014 --soft # MySQL 用不了时只提示、不报错（转写后自动入库用这个）
  python scripts/transcript_index.py index --all [--streamer kiya]          # 历史场次全部补入库
  python scripts/transcript_index.py search "再也不吃辣了" [--before 20261110-2000] [--flag]
  python scripts/transcript_index.py status

需要 MySQL（后台同一个库，表 transcript_segment 会自动建）和 pymysql：pip install PyMySQL；
手动运行时先让环境变量带上库密码：set -a; source /etc/kiya-admin.env; set +a
没有 MySQL 也不影响呼应功能：检索会退回直接读各场的 transcript.json（慢一点，几十场内够用）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper.env import load_env  # noqa: E402
from clipper.streamer import output_root, streamer_from_args, streamer_from_session_dir  # noqa: E402
from clipper.transcript_index import (IndexUnavailable, MemoryIndex, MySQLIndex, date_label, open_index)  # noqa: E402


def sentences_of(session_dir: Path) -> list[dict] | None:
    try:
        return json.loads((session_dir / "transcript.json").read_text(encoding="utf-8")).get("sentences") or []
    except (OSError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ix = sub.add_parser("index")
    ix.add_argument("session_dir", nargs="?")
    ix.add_argument("--all", action="store_true")
    ix.add_argument("--streamer")
    ix.add_argument("--soft", action="store_true", help="MySQL 用不了时不报错")
    se = sub.add_parser("search")
    se.add_argument("text")
    se.add_argument("--streamer")
    se.add_argument("--before", help="只看这个场次之前的（场次名）")
    se.add_argument("--flag", action="store_true", help="只看立 flag 的句子")
    se.add_argument("--limit", type=int, default=10)
    st = sub.add_parser("status")
    st.add_argument("--streamer")
    args = ap.parse_args()
    load_env()

    if args.cmd == "index":
        if args.all:
            sid = streamer_from_args(args.streamer)
            dirs = [d for d in sorted(output_root(sid).iterdir()) if d.is_dir() and not d.name.startswith("_")] if output_root(sid).is_dir() else []
        elif args.session_dir:
            d = Path(args.session_dir)
            sid = streamer_from_args(args.streamer) if args.streamer else streamer_from_session_dir(d)
            dirs = [d]
        else:
            print("要么给场次目录，要么用 --all", file=sys.stderr)
            return 2
        try:
            idx = MySQLIndex.from_env()
        except IndexUnavailable as e:
            msg = f"没有写入全文索引：{e}。呼应检索会改为直接读 transcript.json，功能不受影响。"
            print(msg, file=sys.stderr if not args.soft else sys.stdout)
            return 0 if args.soft else 1
        n_sess = n_rows = 0
        for d in dirs:
            sents = sentences_of(d)
            if sents is None:
                print(f"  {d.name}：没有转写，跳过")
                continue
            n = idx.replace_session(sid, d.name, sents)
            n_sess += 1
            n_rows += n
            print(f"  {d.name}：{n} 句入库")
        print(f"完成：{n_sess} 场，{n_rows} 句（{sid}）")
        return 0

    sid = streamer_from_args(args.streamer)
    if args.cmd == "status":
        try:
            idx = MySQLIndex.from_env()
            names = idx.sessions(sid)
            print(f"MySQL 索引里有 {len(names)} 场：{', '.join(names[-8:])}{' …' if len(names) > 8 else ''}")
        except IndexUnavailable as e:
            idx = MemoryIndex.from_output(output_root(sid), sid)
            print(f"MySQL 不可用（{e}）；文件里有 {len(idx.sessions(sid))} 场转写可检索")
        return 0

    idx, how = open_index(sid, output_root(sid))
    print(f"检索方式：{how}")
    hits = idx.search(sid, args.text, before=args.before, limit=args.limit, flag_only=args.flag)
    if not hits:
        print("没有命中")
    for h in hits:
        print(f"{date_label(h.session)} {h.session} #{h.idx} [{h.t0:.0f}s] {'⚑' if h.flag else ' '} {h.text}  (相关度 {h.score:.2f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
