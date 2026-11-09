#!/usr/bin/env python3
"""评测：候选片段和人工标注对比，算命中率和召回率。

标准答案来源（两者合并）：
  1. eval/annotations.csv：人工标注的有趣片段（session,start,end,...）
  2. eval/reviews/<场次>.csv：阶段 0 填好的 peaks.csv，“是否有趣”为 Y 的行算有趣片段

一个候选和任一有趣片段时间有重叠，就算命中。
  送审数   = 过了分数线、要剪出来给人看的候选个数
  精确率   = 送审的候选里有趣的比例（越高，审核越不浪费时间）
  召回率   = 有趣片段里，被送审候选覆盖到的比例（越高，漏得越少）
  前10覆盖 = 有趣片段里，被前 10 个候选覆盖到的比例
一场只有三四个有趣片段时，“前 10 个里有几个有趣”天然到不了 50%，所以不用它做验收。

用法：
  python scripts/evaluate.py                          # 评测 output/ 下所有有标注的场次
  python scripts/evaluate.py --log "情绪词加了草和绷"   # 同时把结果追加到 eval/results.csv
  python scripts/evaluate.py --sweep                  # 按标注试不同分数线：每条线送审几个、漏掉几个
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


def covered(cands: list[dict], a: float, b: float) -> bool:
    return any(overlap(c["clip_start"], c["clip_end"], a, b) for c in cands)


def evaluate_session(cands: list[dict], truth: list[tuple[float, float, str]]) -> dict:
    hit = sum(any(overlap(c["clip_start"], c["clip_end"], a, b) for a, b, _ in truth) for c in cands)
    return {"n": len(cands),
            "precision": (hit, len(cands)),
            "recall": (sum(covered(cands, a, b) for a, b, _ in truth), len(truth)),
            "top10": (sum(covered(cands[:10], a, b) for a, b, _ in truth), len(truth))}


def pct(x: tuple[int, int]) -> str:
    return f"{x[0]}/{x[1]} = {x[0] / x[1]:.0%}" if x[1] else "—"


def load_review_scores(reviews: Path) -> list[tuple[str, float, str]]:
    """标注过的候选：(场次, 分数, Y/N)。"""
    rows = []
    for p in sorted(reviews.glob("*.csv")) if reviews.exists() else []:
        with open(p, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                m = (r.get("是否有趣(Y/N)") or "").strip().upper()
                try:
                    if m in ("Y", "N"):
                        rows.append((p.stem, float(r["分数"]), m))
                except (KeyError, ValueError):
                    pass
    return rows


def sweep(reviews: Path, truth: dict, out: Path) -> int:
    rows = load_review_scores(reviews)
    if not rows:
        print("还没有标注过的候选（eval/reviews/*.csv 里“是否有趣”填 Y / N）")
        return 1
    ys = sorted(s for _, s, m in rows if m == "Y")
    n_y, n_all = len(ys), len(rows)
    sessions = sorted({r[0] for r in rows})
    # 粗筛本身漏掉的（annotations.csv 里有、但不在任何候选里）：调分数线救不回来
    missed = 0
    for sess, t in truth.items():
        cpath = out / sess / "candidates.json"
        if cpath.exists():
            d = json.loads(cpath.read_text(encoding="utf-8"))
            allc = d["candidates"] + d.get("below_threshold", [])
            missed += sum(not covered(allc, a, b) for a, b, src in t if src == "annotation")
    print(f"已标注 {n_all} 个候选（{len(sessions)} 场），有趣 {n_y} 个")
    print(f"{'分数线':>8}{'送审':>8}{'有趣':>8}{'精确率':>10}{'有趣保留':>10}{'少看':>8}")
    for th in sorted({round(s, 2) for _, s, _ in rows}, reverse=True):
        kept = [m for _, s, m in rows if s >= th]
        y = kept.count("Y")
        print(f"{th:>8.2f}{len(kept):>8}{y:>8}{y / len(kept):>10.0%}{(y / n_y if n_y else 0):>10.0%}"
              f"{n_all - len(kept):>8}")
    if ys:
        rec = max(0.0, int((ys[0] - 0.05) * 10) / 10)     # 最低的有趣分数再往下留一点余量，取一位小数
        kept = sum(1 for _, s, _ in rows if s >= rec)
        print(f"\n建议分数线：{rec}（保住全部 {n_y} 个有趣片段，送审 {kept}/{n_all} 个，少看 {n_all - kept} 个）")
        print(f"  写进 config/kiya.yaml：analysis.min_score: {rec}")
        if len(sessions) < 3:
            print("  只有 1–2 场数据，先偏宽松；攒到 3 场以上再用这个命令重新定")
    if missed:
        print(f"另有 {missed} 个有趣片段根本不在候选里（粗筛漏掉的），调分数线救不回来，需要改信号 / 权重")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "output"), help="screen.py 的输出目录")
    ap.add_argument("--annotations", default=str(REPO / "eval" / "annotations.csv"))
    ap.add_argument("--reviews", default=str(REPO / "eval" / "reviews"))
    ap.add_argument("--config", default=str(REPO / "config" / "kiya.yaml"))
    ap.add_argument("--log", metavar="改了什么", help="把结果追加到 eval/results.csv")
    ap.add_argument("--sweep", action="store_true", help="按标注试不同分数线")
    args = ap.parse_args()

    truth = load_truth(Path(args.annotations), Path(args.reviews))
    if args.sweep:
        return sweep(Path(args.reviews), truth, Path(args.out))
    if not truth:
        print("还没有标注数据：填写 eval/annotations.csv，或把标注过的 peaks.csv 复制到 eval/reviews/<场次>.csv")
        return 1
    tot = {"n": 0, "precision": [0, 0], "recall": [0, 0], "top10": [0, 0]}
    signals = set()
    n_sessions = 0
    print(f"{'场次':<16}{'送审':>6}  {'精确率':<14}{'召回率':<14}{'前10覆盖':<14}{'分数线':<8}信号")
    for sess, t in sorted(truth.items()):
        cpath = Path(args.out) / sess / "candidates.json"
        if not cpath.exists():
            print(f"{sess:<16}（有标注 {len(t)} 条，但没有 candidates.json，先运行 screen.py）")
            continue
        data = json.loads(cpath.read_text(encoding="utf-8"))
        r = evaluate_session(data["candidates"], t)
        n_sessions += 1
        signals.add(data.get("signals_used", "?"))
        tot["n"] += r["n"]
        for k in ("precision", "recall", "top10"):
            tot[k][0] += r[k][0]
            tot[k][1] += r[k][1]
        ms = data.get("min_score")
        print(f"{sess:<16}{r['n']:>6}  {pct(r['precision']):<14}{pct(r['recall']):<14}{pct(r['top10']):<14}"
              f"{'—' if ms is None else ms:<8}{data.get('signals_used', '')}")
    if not n_sessions:
        return 1
    print("-" * 90)
    print(f"{'合计':<16}{tot['n']:>6}  {pct(tuple(tot['precision'])):<14}{pct(tuple(tot['recall'])):<14}"
          f"{pct(tuple(tot['top10'])):<14}")
    print("阶段 1 验收（3 场以上）：召回率 ≥ 90%，精确率 ≥ 40%")
    if not Path(args.annotations).exists() or Path(args.annotations).stat().st_size < 80:
        print("提示：召回率只算了你审过的候选；候选之外漏掉的有趣片段记到 eval/annotations.csv 才算得准"
              "（可以扫一遍 output/<场次>/transcript.txt 找）")

    if args.log:
        cfg_hash = hashlib.sha1(Path(args.config).read_bytes()).hexdigest()[:8]
        res = REPO / "eval" / "results.csv"
        header = ["date", "change", "config_version", "sessions", "top10_hit_rate", "recall", "note",
                  "precision", "candidates", "top10_coverage"]
        old = []
        if res.exists() and res.stat().st_size:
            with open(res, encoding="utf-8-sig") as f:
                old = list(csv.DictReader(f))
        prec = tot["precision"][0] / tot["precision"][1] if tot["precision"][1] else 0
        rc = tot["recall"][0] / tot["recall"][1] if tot["recall"][1] else 0
        t10 = tot["top10"][0] / tot["top10"][1] if tot["top10"][1] else 0
        old.append({"date": time.strftime("%F %H:%M"), "change": args.log, "config_version": cfg_hash,
                    "sessions": n_sessions, "top10_hit_rate": "", "recall": f"{rc:.2f}",
                    "note": " / ".join(sorted(signals)), "precision": f"{prec:.2f}", "candidates": tot["n"],
                    "top10_coverage": f"{t10:.2f}"})
        with open(res, "w", newline="", encoding="utf-8-sig") as f:      # 旧文件缺新列时顺便补上表头
            w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
            w.writeheader()
            w.writerows(old)
        print(f"已记录到 {res}（配置版本 {cfg_hash}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
