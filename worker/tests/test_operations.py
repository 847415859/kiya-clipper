from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clipper.operations import DailyLedger, beijing_day, plan_inventory


DAY = "2026-11-24"


def test_unknown_publication_holds_slot_and_requires_history(tmp_path):
    ledger = DailyLedger(tmp_path / "ops.sqlite")
    assert ledger.reserve_upload("acct", DAY, "one") == "history_unverified"
    ledger.reconcile("acct", DAY, ["manual-1", "manual-2", "manual-1"], complete=True)
    assert ledger.reserve_upload("acct", DAY, "one") == "reserved"
    ledger.record_upload("acct", "one", "unknown")
    assert ledger.reserve_upload("acct", DAY, "one") == "existing_unknown"
    assert ledger.reserve_upload("acct", DAY, "two") == "daily_limit"
    ledger.reconcile("acct", DAY, ["manual-1", "manual-2", "BV-one"], complete=True)
    ledger.record_upload("acct", "one", "confirmed", receipt="BV-one")
    assert ledger.snapshot("acct", DAY)["publication_slots_used"] == 3
    assert ledger.reserve_upload("acct", "2026-11-25", "one") == "existing_released"


def test_budget_reservations_are_shared_across_threads_and_failures(tmp_path):
    path = tmp_path / "ops.sqlite"
    ledger = DailyLedger(path)
    assert ledger.reserve_cost("kiya", DAY, "first", 4600) == "reserved"
    assert ledger.reserve_cost("kiya", DAY, "first", 4600) == "already_reserved"
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda i: DailyLedger(path).reserve_cost("kiya", DAY, str(i), 400), range(6)))
    assert results.count("reserved") == 1
    assert results.count("daily_limit") == 5
    assert ledger.snapshot("acct", DAY)["available_fen"] == 0
    ledger.settle_cost("kiya", "first", 4500)
    assert ledger.snapshot("acct", DAY)["available_fen"] == 100
    assert ledger.reserve_cost("kiya", "2026-11-25", "next", 5000) == "reserved"
    assert beijing_day(datetime(2026, 11, 23, 16, tzinfo=timezone.utc)) == DAY


def test_combination_prefers_desired_traits_but_keeps_diversity(tmp_path):
    ledger = DailyLedger(tmp_path / "ops.sqlite")
    ledger.reconcile("acct", DAY, ["manual"], complete=True)
    slots = ledger.snapshot("acct", DAY)

    def row(id_, topic, traits, score, decision="qualified"):
        return {"id": id_, "event": id_, "topic": topic, "traits": traits, "score": score, "decision": decision}

    result = plan_inventory([row("a", "扫码", ["反差"], 9), row("b", "扫码", ["出丑"], 8),
                             row("c", "问候", ["温馨"], 7), row("d", "下播", [], 2, "reject")], slots)
    assert [row["id"] for row in result["would_publish"]] == ["a", "c"]
    assert [row["id"] for row in result["keep_in_inventory"]] == ["b"]
    assert [row["id"] for row in result["skip"]] == ["d"]
    assert ledger.snapshot("acct", DAY) == slots
