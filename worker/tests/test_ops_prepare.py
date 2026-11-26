from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clipper.operations import DailyLedger, beijing_day, reserve_external_cost
from scripts import ops_prepare


def test_preparation_resumes_and_plans_without_posting(tmp_path, monkeypatch):
    root = tmp_path / "operations"
    ledger_path = root / "daily.sqlite3"
    monkeypatch.setenv("KIYA_OPS_LEDGER", str(ledger_path))
    monkeypatch.delenv("KIYA_ASR", raising=False)
    monkeypatch.delenv("KIYA_OPS_HISTORY_DIR", raising=False)
    rec = tmp_path / "rec"
    rec.mkdir()
    start = datetime(2026, 11, 24, 10, tzinfo=timezone.utc)
    for name, room, stamp in (("right", "123", start.isoformat()),
                              ("other-room", "456", start.isoformat()),
                              ("old", "123", "2026-11-23T10:00:00+00:00")):
        xml = rec / (name + ".xml")
        xml.write_text(f'<i><BililiveRecorderRecordInfo roomid="{room}" start_time="{stamp}"/></i>',
                       encoding="utf-8")
        xml.with_suffix(".flv").write_bytes(b"test source")

    job_path = root / "queue" / "kiya-test.json"
    ops_prepare.save(job_path, {"streamer": "kiya", "room": "123", "since": start.timestamp(),
                                "ended": start.timestamp() + 600, "rec_dir": str(rec)})
    called = []

    def fake_step(script, args, timeout, log):
        called.append(script)
        session = log.parent
        if script == "screen.py":
            assert args[0] == str(rec / "right.xml")
            ops_prepare.save(session / "candidates.json", {"candidates": [{"rank": 1}, {"rank": 2}]})
        elif script == "rerank.py":
            selected = {"rank": 1, "final": .95, "llm": {"type": "反差"},
                        "event": {"event_id": "test", "status": "ready"},
                        "video_evidence": {"observations": [{"id": "audio-visual"}]}}
            if "--ranks" in args:
                assert args[-2:] == ["--ranks", 2]
                missing = {"rank": 2, "llm": {"type": "温馨"}}
            else:
                missing = {"rank": 2, "llm": None}
            ops_prepare.save(session / "rerank.json", {"candidates": [selected, missing]})
        elif script == "agent_run.py":
            ops_prepare.save(session / "agent_run.json", {"ranks": [1]})
            ops_prepare.save(session / "quality" / "01.json", {"ok": True, "warnings": []})
        elif script == "publish_pack.py":
            ops_prepare.save(session / "publish" / "01.json", {"title": "标题", "taboo_hits": []})

    monkeypatch.setattr(ops_prepare, "run_step", fake_step)
    now = max(start.timestamp() + 900, (rec / "right.xml").stat().st_mtime + 100)
    ops_prepare.drain_one(root, now)
    status_path = root / "status" / job_path.name
    assert ops_prepare.read(status_path)["status"] == "partial"
    ops_prepare.drain_one(root, now + 901)
    ops_prepare.drain_one(root, now + 1802)
    status = ops_prepare.read(status_path)
    assert status["status"] == "prepared"
    assert status["inventory"][0]["decision"] == "qualified"
    assert called == ["screen.py", "rerank.py", "agent_run.py", "publish_pack.py", "rerank.py", "agent_run.py"]

    ops_prepare.build_daily_plan(root, start)
    daily = ops_prepare.read(root / "plans" / "2026-11-24.json")["streamers"]["kiya"]
    assert daily["account_uid"] is None
    assert daily["plan"]["would_publish"] == []
    assert len(daily["plan"]["keep_in_inventory"]) == 1

    history_root = tmp_path / "account_history"
    monkeypatch.setenv("KIYA_OPS_HISTORY_DIR", str(history_root))
    monkeypatch.setattr("clipper.publish_account.status",
                        lambda *args: {"source": "website", "state": "logged_in", "account": {"uid": "456"}})
    history = {"account_uid": "456", "day": "2026-11-24", "complete": True,
               "occupied_ids": ["manual-1", "manual-2"], "verified_at": start.isoformat()}
    ops_prepare.save(history_root / "kiya.json", history)
    ops_prepare.build_daily_plan(root, start)
    daily = ops_prepare.read(root / "plans" / "2026-11-24.json")["streamers"]["kiya"]
    assert daily["budget"]["publication_slots_available"] == 1
    assert daily["plan"]["would_publish"][0]["planned_at"] == "2026-11-24T18:30:00+08:00"


def test_manual_resume_reuses_original_checkpoints(tmp_path, monkeypatch):
    import json
    import os
    import time

    now = time.time()
    root = tmp_path / 'ops'
    job = root / 'queue' / 'kiya-123456.json'
    status = root / 'status' / job.name
    session = root / 'sessions' / job.stem / 'kiya' / 'fixture'
    output = root / 'production' / job.stem
    inventory = output / '01' / 'inventory.json'
    rerank = session / 'rerank.json'
    ops_prepare.save(job, {'streamer': 'kiya', 'room': '123', 'since': now - 1200, 'ended': now - 300})
    ops_prepare.save(status, {'sources': [], 'session': 'fixture', 'screen_complete': True,
                             'production_mode': 'isolated', 'production_ranks': [1, 2],
                             'status': 'blocked', 'retry_at': now + 900, 'attempts': 3})
    ops_prepare.save(session / 'candidates.json', {'candidates': [{'rank': 1}, {'rank': 2}]})
    ops_prepare.save(rerank, {'candidates': [{'rank': i, 'llm': {'score': 8}} for i in (1, 2)]})
    ops_prepare.save(inventory, {'version': 'kept'})
    calls = []

    def step(script, args, timeout, log):
        assert script == 'agent_produce.py'
        calls.append(script)
        ops_prepare.save(output / 'summary-selected.json', {'candidates': [
            {'rank': 1, 'status': 'inventoried'}, {'rank': 2, 'status': 'needs_review'}]})

    monkeypatch.setenv('KIYA_OPS_LEDGER', str(root / 'daily.sqlite3'))
    monkeypatch.setattr(ops_prepare, 'run_step', step)
    monkeypatch.setattr(ops_prepare, 'production_inventory', lambda *args: [{'rank': 1, 'version': 'kept'}])
    ops_prepare.drain_one(root, now)
    assert calls == []
    before = ops_prepare.read(status)
    marker = status.with_suffix('.json.resume')
    marker.touch()
    versions = {p.name: p.read_bytes() for p in (rerank, inventory)}
    ops_prepare.drain_one(root, now)
    ops_prepare.drain_one(root, now + 1)
    after = ops_prepare.read(status)
    assert calls == ['agent_produce.py'] and after['status'] == 'prepared'
    assert not marker.exists()
    assert versions == {p.name: p.read_bytes() for p in (rerank, inventory)}
    if os.environ.get('KIYA_SESSION_ACCEPTANCE_OUT'):
        ops_prepare.save(Path(os.environ['KIYA_SESSION_ACCEPTANCE_OUT']) / 'resume.json',
                         {'mode': 'mock-production', 'jobId': job.name, 'before': before, 'after': after,
                          'executed': calls, 'unchanged': sorted(versions), 'markerConsumed': True})


def test_paid_attempts_fail_closed_and_keep_unknown_cost(tmp_path, monkeypatch):
    path = tmp_path / "ledger.sqlite3"
    monkeypatch.setenv("KIYA_OPS_LEDGER", str(path))
    monkeypatch.delenv("KIYA_OPS_LLM_MAX_FEN", raising=False)
    with pytest.raises(RuntimeError, match="KIYA_OPS_LLM_MAX_FEN"):
        reserve_external_cost("LLM")
    monkeypatch.setenv("KIYA_OPS_LLM_MAX_FEN", "4000")
    reserve_external_cost("LLM")
    marker = tmp_path / "blocked"
    monkeypatch.setenv("KIYA_OPS_BUDGET_BLOCKED", str(marker))
    with pytest.raises(RuntimeError, match="额度不足"):
        reserve_external_cost("LLM")
    now = datetime.now(timezone.utc)
    assert DailyLedger(path).snapshot("", beijing_day(now))["booked_fen"] == 4000
    state = {"status": "retry", "attempts": 1}
    ops_prepare.wait_for_budget(state, now, marker)
    assert state["status"] == "waiting_for_budget" and state["attempts"] == 0
    assert beijing_day(datetime.fromtimestamp(state["retry_at"], timezone.utc)) > beijing_day(now)
