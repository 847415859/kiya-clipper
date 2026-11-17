#!/usr/bin/env python3
"""采集到期的 24h / 7d 快照，写 SQLite；可由 systemd 每 15 分钟执行。

python scripts/collect_value_metrics.py --streamer kiya
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clipper.bili import Bili, memory_cookie_file
from clipper.env import load_env
from clipper.kcfg import load_cfg
from clipper.streamer import output_root, streamer_from_args, resolve_config, decisions_path
from clipper.value_metrics import collect_due, snapshot_store, upload_cache_store


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--streamer")
    ap.add_argument("--budget", type=int, default=30)
    args = ap.parse_args()
    load_env()
    sid = streamer_from_args(args.streamer)
    output = output_root(sid)
    path = output / "_memory" / "memory.json"
    memory = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    dec_path = decisions_path(sid)
    dec = json.loads(dec_path.read_text(encoding="utf-8")) if dec_path.exists() else {}
    published = dec.get("published") or {}
    store = output / "_memory" / "value_metrics.sqlite3"
    cache = upload_cache_store(store)
    rows = []
    for bv, link in published.items():
        if not isinstance(link, dict) or not link.get("session") or not link.get("rank"):
            continue
        row = next((r for r in memory.get("my_uploads", []) if r.get("bvid") == bv), {})
        rows.append({**row, **cache.get(bv, {}), "bvid": bv, "session": link["session"], "rank": link["rank"],
                     "owner_mid": dec.get("my_uid") or row.get("owner_mid"),
                     "event_id": link.get("event_id") or f"{link['session']}:{link['rank']}"})
    history = snapshot_store(store, memory.get("value_snapshots"))
    cfg = load_cfg(resolve_config(None, streamer=sid), decisions=dec)
    client = Bili(cookie_file=memory_cookie_file(cfg))
    fetched = {}
    def fetch(bv):
        stat = client.stat(bv)
        fetched[bv] = stat
        return stat
    updated, errors, calls = collect_due(rows, history, fetch, time.time(), args.budget, clock=time.time)
    upload_cache_store(store, fetched)
    saved = snapshot_store(store, updated)
    print(f"查询 {calls} 条；累计快照 {sum(len(v) for v in saved.values())} 条")
    for error in errors:
        print(error)
    # Refresh derived views locally. No second network pass or LLM invocation.
    if updated != history:
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "learn.py"), "--streamer", sid, "--no-fetch"]).returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
