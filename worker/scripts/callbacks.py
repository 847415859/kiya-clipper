#!/usr/bin/env python3
"""看一个候选能找到什么跨场次呼应（只看、不写任何文件）。AI 剪辑初稿（autoedit.py）里会自动做同样的事，这个脚本用来调试检索质量。

用法：
  python scripts/callbacks.py output/kiya/20261112-2000/candidates.json --rank 3 --no-llm   # 只看检索到的历史段落（不花钱）
  python scripts/callbacks.py output/kiya/20261112-2000/candidates.json --rank 3            # 再让大模型判断 + 程序校验

输出：用了哪些查询句、检索方式（MySQL / 直接读 transcript.json）、命中的历史段落、大模型的判断和校验结果。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper import callbacks as cb  # noqa: E402
from clipper.asr import load_transcript  # noqa: E402
from clipper.context import speech_lines  # noqa: E402
from clipper.edit import suggest_window  # noqa: E402
from clipper.kcfg import load_cfg  # noqa: E402
from clipper.llm import LLMClient, load_prompt  # noqa: E402
from clipper.streamer import resolve_config, streamer_from_session_dir  # noqa: E402
from clipper.transcript_index import open_index  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("candidates")
    ap.add_argument("--rank", type=int, required=True)
    ap.add_argument("--no-llm", action="store_true", help="只看检索结果，不调大模型")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    cpath = Path(args.candidates)
    sess = cpath.parent
    cfg = load_cfg(resolve_config(args.config, sess))
    cbc = cb.settings(cfg)
    data = json.loads(cpath.read_text(encoding="utf-8"))
    c = next((x for x in data["candidates"] if x["rank"] == args.rank), None)
    if not c:
        print(f"没有序号 {args.rank} 的候选", file=sys.stderr)
        return 1
    tr = load_transcript(sess / "transcript.json")
    sents = tr["sentences"] if tr else []
    if not sents:
        print("这场还没有转写，没法找呼应", file=sys.stderr)
        return 1
    rr = None
    if (sess / "rerank.json").exists():
        rr = next((r for r in json.loads((sess / "rerank.json").read_text(encoding="utf-8"))["candidates"] if r["rank"] == args.rank), None)
    llm = (rr or {}).get("llm") or {}
    w = suggest_window(c, (rr or {}).get("refined"))
    a, b = w["start"], w["end"]
    punch = c["clip_start"] + float(llm["punch_time"]) if llm.get("punch_time", -1) >= 0 else None
    sid = streamer_from_session_dir(sess)
    index, how = open_index(sid, sess.parent, cbc["index"])
    print(f"检索方式：{how}；片段 {a:.0f}–{b:.0f} 秒，笑点 {'%.0f 秒' % punch if punch else '未知'}")
    queries = cb.build_queries(sents, a, b, punch, llm.get("what_happened") or "")
    print("查询句：")
    for q in queries:
        print("  - " + q)
    hits = cb.retrieve(index, sid, sess.name, queries, cbc)
    passages = cb.build_passages(index, sid, hits, cbc)
    print(f"\n命中 {len(hits)} 句，合并成 {len(passages)} 个历史段落：\n{cb.passages_text(passages)}")
    if args.no_llm or not passages:
        return 0
    client = LLMClient(cache_dir=sess.parent / "_llm_cache")
    out, err = cb.judge(client, load_prompt("callback_v1"), speech_lines(sents, a, b, a), llm.get("what_happened") or "", passages)
    if out is None:
        print(f"\n大模型判定失败：{err}", file=sys.stderr)
        return 1
    clip_text = " ".join(s["text"] for s in sents if s["t1"] > a and s["t0"] < b)
    ok, why = cb.verify(out.callbacks, passages, clip_text, cbc)
    print(f"\n大模型给了 {len(out.callbacks)} 条，校验通过 {len(ok)} 条：")
    for x in ok:
        footage = cb.footage_available(sess.parent, x["session"], x["start"], x["end"])
        print(f"  ✓ {cb.RELATION_NAME[x['relation']]}  {x['label']}  置信度 {x['confidence']}  旧画面 {x['start']:.1f}–{x['end']:.1f}s"
              f"（{'录像在' if footage else '录像不在本地，会用文字卡'}）\n    本片：{x['current_quote']}\n    理由：{x['note']}")
    for x in why:
        print("  ✗ " + x)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
