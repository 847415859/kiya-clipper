#!/usr/bin/env python3
"""导出随机顺序的价值盲审表，或用填写后的表生成离线权重建议（不会自动应用）。

python scripts/value_review.py export output/kiya/场次/rerank.json
python scripts/value_review.py calibrate reviews/a.csv reviews/b.csv reviews/c.csv --keys reviews/a.keys.json reviews/b.keys.json reviews/c.keys.json --out proposal.json
python scripts/value_review.py experiment --out experiment.json --hypothesis '事实语录标题更易吸引路人' --budget 10 --stop-rule '发现事实或形象风险即停'
"""
import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clipper.value_review import calibrate, review_batch


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export")
    exp.add_argument("rerank")
    cal = sub.add_parser("calibrate")
    cal.add_argument("reviews", nargs="+")
    cal.add_argument("--keys", nargs="+", required=True)
    cal.add_argument("--out", required=True)
    plan = sub.add_parser("experiment")
    plan.add_argument("--out", required=True)
    plan.add_argument("--hypothesis", required=True)
    plan.add_argument("--budget", type=int, required=True)
    plan.add_argument("--stop-rule", required=True)
    args = ap.parse_args()
    if args.command == "export":
        path = Path(args.rerank)
        data = json.loads(path.read_text(encoding="utf-8"))
        rows, keys = review_batch(data["candidates"], data["session"])
        if not rows:
            ap.error("没有可导出的候选")
        target = path.with_name(path.stem + ".blind.csv")
        key_path = target.with_suffix(".keys.json")
        if target.exists() or key_path.exists():
            ap.error("盲审表或答案映射已存在，不覆盖人工标注")
        with target.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        key_path.write_text(json.dumps(keys, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{target}；评审时只看表与对应素材，不看旁边的 keys 文件")
        return 0
    if args.command == "calibrate":
        reviews, keys = [], {}
        for path in args.reviews:
            with Path(path).open(encoding="utf-8-sig", newline="") as f:
                reviews.extend(csv.DictReader(f))
        for path in args.keys:
            keys.update(json.loads(Path(path).read_text(encoding="utf-8")))
        result = calibrate(reviews, keys)
    else:
        if not 1 <= args.budget <= 50:
            ap.error("实验样本预算限 1–50 条")
        result = {"schema_version": 1, "hypothesis": args.hypothesis, "budget": args.budget,
                  "stop_rule": args.stop_rule, "control": "legacy", "treatment": "value-v1",
                  "observation_windows": ["24h", "7d"], "change_one_factor": True,
                  "target": "clip_account_follows", "auto_publish": False, "status": "draft"}
    path = Path(args.out)
    if path.exists():
        ap.error("输出已存在，不覆盖已有实验或建议")
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
