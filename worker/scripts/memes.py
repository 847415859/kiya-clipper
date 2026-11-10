#!/usr/bin/env python3
"""梗库：抓B站热门 → 更新梗库 → （可选）预热向量；也能直接试检索。

用法：
  python scripts/memes.py refresh               # 抓热门（热门 / 每周必看 / 热搜，只取标题）+ 重建梗库 + 预热向量
  python scripts/memes.py refresh --no-fetch    # 不联网抓热门，只用已有的快照重建（后台点“重建”时用）
  python scripts/memes.py refresh --no-embed    # 不预热文本向量
  python scripts/memes.py search "主播钓鱼翻车，弹幕刷屏"   # 看这个情境会检索到哪些梗

结果写到 output/_memory/memes.json（后台“梗库”页读它）。热门快照在 output/_memory/hot/，留 14 天。
低频、只抓文本：每天一次，不抓评论、不抓视频。新梗默认“待确认”，后台填好含义和尺度通过后，剪辑初稿才会用。
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from clipper import hot as hotmod  # noqa: E402
from clipper.bili import Bili, BiliError  # noqa: E402
from clipper.embed import Embedder, EmbedError  # noqa: E402
from clipper.env import load_env  # noqa: E402
from clipper.kcfg import load_cfg, read_decisions  # noqa: E402
from clipper.memes import (STATUS_LABEL, build_library, doc_text, load_library, meme_stats, retrieve,  # noqa: E402
                           settings, usable, write_library)
from clipper.weekly import read_json  # noqa: E402


def stream_recent(out_dir: Path, days: int = 14) -> dict[str, int]:
    """近 days 天各场（按场次名里的日期）弹幕 + 转写里各说法出现的次数。"""
    from clipper.memory import ngrams_of_session
    from clipper.weekly import session_start
    since = date.today() - timedelta(days=days)
    cnt: dict[str, int] = {}
    for d in sorted(p for p in out_dir.iterdir() if p.is_dir() and not p.name.startswith("_")) if out_dir.exists() else []:
        t = session_start(d.name)
        if not t or t.date() < since:
            continue
        g = ngrams_of_session(d) or {}
        for part in ("speech", "danmaku"):
            for w, n in (g.get(part) or {}).items():
                cnt[w.lower()] = cnt.get(w.lower(), 0) + int(n)
    return cnt


def rebuild(cfg: dict, out_dir: Path) -> list[dict]:
    mem_dir = out_dir / "_memory"
    hist = hotmod.load_history(mem_dir / "hot")
    lib = build_library(cfg, read_decisions(), read_json(mem_dir / "memory.json", {}) or {}, hist, date.today(),
                        stats=meme_stats(out_dir), stream_recent=stream_recent(out_dir),
                        previous=load_library(mem_dir / "memes.json"))
    write_library(mem_dir / "memes.json", lib, {"hot_days": len(hist)})
    return lib


def summary(lib: list[dict]) -> str:
    n = {s: sum(1 for e in lib if e["status"] == s) for s in STATUS_LABEL}
    cool = sum(1 for e in lib if e.get("cooling") and e["status"] == "approved")
    return (f"梗库共 {len(lib)} 条：已启用 {n['approved']}（其中降温 {cool}）、待确认 {n['pending']}、"
            f"已停用 {n['disabled']}、黑名单 {n['blacklisted']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(REPO / "config" / "kiya.yaml"))
    ap.add_argument("--out", default=str(ROOT / "output"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("refresh")
    r.add_argument("--no-fetch", action="store_true")
    r.add_argument("--no-embed", action="store_true")
    q = sub.add_parser("search")
    q.add_argument("situation")
    args = ap.parse_args()

    load_env()
    cfg = load_cfg(Path(args.config))
    s = settings(cfg)
    out_dir = Path(args.out)
    mem_dir = out_dir / "_memory"

    if args.cmd == "search":
        lib = load_library(mem_dir / "memes.json") or rebuild(cfg, out_dir)
        rows, method = retrieve(args.situation, lib, cfg, taboo=cfg.get("taboo") or [])
        print(f"检索方式：{ {'vector': '文本向量', 'lexical': '字面相似度', 'none': '无可用的梗'}[method] }；可用的梗 {len(usable(lib, cfg, cfg.get('taboo') or []))} 条")
        for x in rows:
            print(f"  {x['score']:.3f}（相似 {x['sim']:.3f} × 权重 {x['weight']}）《{x['text']}》 {x.get('meaning', '')}")
        return 0

    errors = []
    if not args.no_fetch:
        try:
            snap = hotmod.crawl(Bili(), int(s["hot_min_titles"]))
            hotmod.save_snapshot(mem_dir / "hot", snap)
            print(f"热门：{snap['titles']} 个标题，{len(snap['phrases'])} 个短语（来源 {snap['sources']}）")
            errors += snap["errors"]
        except BiliError as e:
            errors.append(str(e))
            print(f"热门抓取失败，用已有的快照重建：{e}", file=sys.stderr)
    lib = rebuild(cfg, out_dir)
    print(summary(lib))
    if not args.no_embed:
        pool = usable(lib, cfg, cfg.get("taboo") or [])
        emb = Embedder(model=s["embed_model"] or None)
        if pool and emb.available:
            try:
                emb.embed([doc_text(e) for e in pool])
                print(f"文本向量：{len(pool)} 条已启用的梗已就绪")
            except EmbedError as e:
                print(f"文本向量预热失败（检索时退回字面匹配）：{e}", file=sys.stderr)
    for e in errors:
        print(f"  注意：{e}", file=sys.stderr)
    return 1 if errors and not args.no_fetch and len(errors) >= 4 else 0


if __name__ == "__main__":
    sys.exit(main())
