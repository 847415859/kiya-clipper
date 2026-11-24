#!/usr/bin/env python3
"""Build a local operations brief from candidate and accounting snapshots."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clipper.operations import DAILY_COST_FEN, DAILY_PUBLISH_LIMIT, DailyLedger, plan_inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--day", required=True, help="北京时间日期 YYYY-MM-DD")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inputs = json.loads(args.inventory.read_text(encoding="utf-8-sig"))
    snapshot = DailyLedger(args.ledger).snapshot(args.account, args.day)
    plan = plan_inventory(inputs["candidates"], snapshot, inputs.get("recent_events", []),
                          inputs.get("recent_topics", []))
    cost_complete = inputs.get("cost_records_complete") is True
    result = {"mode": "rehearsal", "inventory_source": str(args.inventory), "account": args.account,
              "paid_limit_fen": DAILY_COST_FEN, "publish_limit": DAILY_PUBLISH_LIMIT, **snapshot, **plan,
              "cost_records_complete": cost_complete}
    if not cost_complete:
        result["available_fen"] = None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
