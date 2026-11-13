#!/usr/bin/env python3
"""生成候选成片的程序质检报告。

用法：
  python scripts/quality_check.py output/<场次>/candidates.json --rank 3
  python scripts/quality_check.py output/<场次>/candidates.json --all

报告写入 output/<场次>/quality/<序号>.json；--all 另外汇总 AI 初稿与人工终稿差异。
质检只报告问题，不会替渲染或发布做自动决策。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper.editdiff import collect_diffs, edit_taste  # noqa: E402
from clipper.quality import quality_report, write_report  # noqa: E402


def load_edit(session: Path, rank: int) -> dict | None:
    f = session / "edits" / f"{rank:02d}.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    except (OSError, ValueError):
        return None


def load_candidates(path: Path) -> list[dict]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return list(obj.get("candidates") or [])
    except (OSError, ValueError) as e:
        raise SystemExit(f"候选文件无法读取：{e}")


def one(candidates_path: Path, rank: int) -> dict:
    session = candidates_path.parent
    candidate = next((c for c in load_candidates(candidates_path) if int(c.get("rank", -1)) == rank), None)
    if candidate is None:
        raise SystemExit(f"没有序号 {rank} 的候选")
    report = quality_report(session, rank, candidate=candidate, edit=load_edit(session, rank))
    out = session / "quality" / f"{rank:02d}.json"
    write_report(report, out)
    state = "通过" if report.ok and not report.warnings else "有警告" if report.ok else "失败"
    print(f"#{rank} 质检{state}：错误 {len(report.errors)}，警告 {len(report.warnings)}，报告 {out}")
    return report.as_dict()


def all_reports(candidates_path: Path) -> int:
    session = candidates_path.parent
    rows = [one(candidates_path, int(c["rank"])) for c in load_candidates(candidates_path) if str(c.get("rank", "")).isdigit()]
    output = session.parent
    diffs = collect_diffs(output)
    taste = edit_taste(output)
    aggregate = {"session": session.name, "reports": rows, "edit_diffs": [r for r in diffs if r["session"] == session.name],
                 "edit_taste": taste}
    path = session / "quality" / "index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"汇总报告：{path}；AI/人工差异 {len(aggregate['edit_diffs'])} 条")
    return 0 if all(r["ok"] for r in rows) else 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("candidates", type=Path)
    ap.add_argument("--rank", type=int)
    ap.add_argument("--all", action="store_true", dest="all_reports")
    args = ap.parse_args()
    if args.all_reports:
        return all_reports(args.candidates)
    if args.rank is None:
        ap.error("需要 --rank 或 --all")
    result = one(args.candidates, args.rank)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
