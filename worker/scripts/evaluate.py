#!/usr/bin/env python3
"""评测：候选片段和人工标注对比，算命中率和召回率。

标准答案来源（两者合并）：
  1. eval/annotations.csv：人工标注的有趣片段（session,start,end,...）
  2. eval/reviews/<场次>.csv：阶段 0 填好的 peaks.csv，“是否有趣”为 Y 的行算有趣片段

一个候选和任一有趣片段时间有重叠，就算命中。
  Top10 命中率 = 前 10 个候选里命中的比例
  Top20 命中率 = 前 20 个候选里命中的比例
  召回率@20   = 有趣片段里，被前 20 个候选覆盖到的比例

用法：
  python scripts/evaluate.py                          # 评测 output/ 下所有有标注的场次
  python scripts/evaluate.py --log "情绪词加了草和绷"   # 同时把结果追加到 eval/results.csv
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


def to_sec(s: str) -> float:
    parts = [float(x) for x in s.strip().split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def load_truth(annotations: Path, reviews: Path) -> dict[str, list[tuple[float, float, str]]]:
    truth: dict[str, list[tuple[float, float, str]]] = defaultdict(list)
    if annotations.exists():
        with open(annotations, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("session") and r.get("start") and r.get("end") and "示例行" not in (r.get("note") or ""):
                    truth[r["session"].strip()].append((to_sec(r["start"]), to_sec(r["end"]), "annotation"))
    if reviews.exists():
        for p in sorted(reviews.glob("*.csv")):
            with open(p, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    mark = (r.get("是否有趣(Y/N)") or "").strip().upper()
                    if mark == "Y":
                        truth[p.stem].append((to_sec(r["片段起"]), to_sec(r["片段止"]), "review"))
    return truth


def overlap(a0, a1, b0, b1) -> bool:
    return min(a1, b1) - max(a0, b0) > 0


def evaluate_session(cands: list[dict], truth: list[tuple[float, float, str]]) -> dict:
    def hits(k):
        top = cands[:k]
        return sum(any(overlap(c["clip_start"], c["clip_end"], a, b) for a, b, _ in truth) for c in top), len(top)

    h10, n10 = hits(10)
    h20, n20 = hits(20)
    covered = sum(any(overlap(c["clip_start"], c["clip_end"], a, b) for c in cands[:20]) for a, b, _ in truth)
    return {"top10": (h10, n10), "top20": (h20, n20), "recall": (covered, len(truth))}


def pct(x: tuple[int, int]) -> str:
    return f"{x[0]}/{x[1]} = {x[0] / x[1]:.0%}" if x[1] else "—"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "output"), help="screen.py 的输出目录")
    ap.add_argument("--annotations", default=str(REPO / "eval" / "annotations.csv"))
    ap.add_argument("--reviews", default=str(REPO / "eval" / "reviews"))
    ap.add_argument("--config", default=str(REPO / "config" / "kiya.yaml"))
    ap.add_argument("--log", metavar="改了什么", help="把结果追加到 eval/results.csv")
    args = ap.parse_args()

    truth = load_truth(Path(args.annotations), Path(args.reviews))
    if not truth:
        print("还没有标注数据：填写 eval/annotations.csv，或把标注过的 peaks.csv 复制到 eval/reviews/<场次>.csv")
        return 1
    tot = {"top10": [0, 0], "top20": [0, 0], "recall": [0, 0]}
    signals = set()
    n_sessions = 0
    print(f"{'场次':<16}{'Top10 命中率':<18}{'Top20 命中率':<18}{'召回率@20':<18}信号")
    for sess, t in sorted(truth.items()):
        cpath = Path(args.out) / sess / "candidates.json"
        if not cpath.exists():
            print(f"{sess:<16}（有标注 {len(t)} 条，但没有 candidates.json，先运行 screen.py）")
            continue
        data = json.loads(cpath.read_text(encoding="utf-8"))
        r = evaluate_session(data["candidates"], t)
        n_sessions += 1
        signals.add(data.get("signals_used", "?"))
        for k in tot:
            tot[k][0] += r[k][0]
            tot[k][1] += r[k][1]
        print(f"{sess:<16}{pct(r['top10']):<18}{pct(r['top20']):<18}{pct(r['recall']):<18}{data.get('signals_used', '')}")
    if not n_sessions:
        return 1
    print("-" * 80)
    print(f"{'合计':<16}{pct(tuple(tot['top10'])):<18}{pct(tuple(tot['top20'])):<18}{pct(tuple(tot['recall'])):<18}")
    print("阶段 1 验收：Top10 命中率 ≥ 50%（3 场以上）")

    if args.log:
        cfg_hash = hashlib.sha1(Path(args.config).read_bytes()).hexdigest()[:8]
        res = REPO / "eval" / "results.csv"
        new = not res.exists() or res.stat().st_size == 0
        with open(res, "a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["date", "change", "config_version", "sessions", "top10_hit_rate", "recall", "note"])
            r10 = tot["top10"][0] / tot["top10"][1] if tot["top10"][1] else 0
            rc = tot["recall"][0] / tot["recall"][1] if tot["recall"][1] else 0
            w.writerow([time.strftime("%F %H:%M"), args.log, cfg_hash, n_sessions, f"{r10:.2f}", f"{rc:.2f}",
                        " / ".join(sorted(signals))])
        print(f"已记录到 {res}（配置版本 {cfg_hash}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
