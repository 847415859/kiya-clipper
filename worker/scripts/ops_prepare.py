#!/usr/bin/env python3
"""Prepare one queued broadcast for editorial review and keep the work resumable."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, time as wall_time, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper.danmaku import _parse_time
from clipper.operations import BEIJING, DailyLedger, beijing_day, schedule_inventory
from clipper.session import find_sessions
from clipper.streamer import config_path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def recordings(job):
    paths = []
    for path in Path(job["rec_dir"]).rglob("*.xml"):
        try:
            with path.open(encoding="utf-8", errors="replace") as source:
                header = source.read(16384)
        except FileNotFoundError:
            continue
        info = re.search(r"<BililiveRecorderRecordInfo\b([^>]+)>", header)
        if not info:
            continue
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', info[1]))
        start = _parse_time(attrs.get("start_time"))
        if attrs.get("roomid") != job["room"] or not start:
            continue
        if job.get("earliest", job["since"] - 300) <= start.timestamp() <= job["ended"]:
            paths.append(path)
    return sorted(paths)


def run_step(script, args, timeout, log_path):
    with log_path.open("a", encoding="utf-8") as log:
        print(f"\n[{datetime.now(timezone.utc).isoformat()}] {script}", file=log, flush=True)
        subprocess.run([sys.executable, "-u", str(ROOT / "scripts" / script), *map(str, args)],
                       cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=timeout)


def prepare(root, job_path, job, state, now):
    def stage(name):
        state['stage'] = name
        save(root / 'status' / job_path.name, state)

    target = root / "sessions" / job_path.stem / job["streamer"]
    if "sources" not in state:
        stage('recording')
        paths = recordings(job)
        if not paths or any(not p.with_suffix(".flv").is_file() for p in paths):
            raise ValueError("未找到完整的同房间录播 XML 和 FLV")
        quiet_until = max(p.stat().st_mtime for xml in paths for p in (xml, xml.with_suffix(".flv"))) + 90
        if quiet_until > now:
            state.update(status="waiting_for_recording", retry_at=quiet_until)
            return
        sessions = find_sessions(paths)
        if len(sessions) != 1:
            raise ValueError("匹配到多个录播场次，需核对原始录播")
        state.update(sources=[str(p) for p in paths], session=sessions[0][0])
        save(root / "status" / job_path.name, state)

    session = target / state["session"]
    session.mkdir(parents=True, exist_ok=True)
    log = session / "operations.log"
    config = config_path(job["streamer"])
    inputs = [*state["sources"], "--config", config, "--out", target]
    candidates = session / "candidates.json"
    if not state.get("screen_complete"):
        if os.environ.get("KIYA_ASR") == "1" and not (session / "transcript.json").is_file():
            stage('transcribe')
            run_step("transcribe.py", inputs, int(os.environ.get("KIYA_ASR_TIMEOUT", "3600")), log)
        stage('screen')
        screen_args = inputs + (["--no-video"] if os.environ.get("KIYA_ANALYZE_NO_VIDEO") == "1" else [])
        run_step("screen.py", screen_args, int(os.environ.get("KIYA_ANALYZE_TIMEOUT", "1800")), log)
        state["screen_complete"] = True
        save(root / "status" / job_path.name, state)

    ranks = [row["rank"] for row in read(candidates)["candidates"]]
    state["directory"] = str(session)
    if not ranks:
        state.update(status="no_candidates", inventory=[])
        return

    isolated = state.get('production_mode') == 'isolated'
    if isolated and 'production_ranks' not in state:
        limit = int(os.environ.get('KIYA_OPS_AGENT_MAX_CANDIDATES', '3'))
        if limit < 1:
            raise ValueError('KIYA_OPS_AGENT_MAX_CANDIDATES must be positive')
        selected = sorted(set(ranks))[:limit]
        state.update(production_limit=limit, production_ranks=selected,
                     deferred_ranks=sorted(set(ranks) - set(selected)))
        save(root / 'status' / job_path.name, state)
    if isolated:
        ranks = state['production_ranks']

    rerank_path = session / "rerank.json"
    stage('rerank')
    if rerank_path.is_file():
        # Partial model failures are recorded as rows. Keep successful rows and retry only gaps.
        rows = read(rerank_path)["candidates"]
        completed = {row["rank"] for row in rows if row.get("llm")}
        missing = [rank for rank in ranks if rank not in completed]
        if missing:
            run_step("rerank.py", [candidates, "--config", config, "--ranks", *missing],
                     int(os.environ.get("KIYA_RERANK_TIMEOUT", "1800")), log)
    else:
        args = [candidates, '--config', config]
        if isolated:
            args.extend(['--ranks', *ranks])
        run_step('rerank.py', args, int(os.environ.get('KIYA_RERANK_TIMEOUT', '1800')), log)

    rows = read(rerank_path)['candidates']
    if not any(row.get('llm') and row['rank'] in ranks for row in rows):
        raise RuntimeError("本场未得到有效精排结论，见 operations.log")
    state["unresolved_ranks"] = sorted(set(ranks) - {row["rank"] for row in rows if row.get("llm")})
    if isolated:
        output = root / 'production' / job_path.stem
        stage('produce')
        run_step('agent_produce.py', ['--candidates', candidates, '--live', '--out', output,
                                      '--config', config, '--ledger', root / 'daily.sqlite3', '--ranks', *ranks], None, log)
        summary = read(output / 'summary-selected.json')
        results = {item['rank']: item['status'] for item in summary['candidates']}
        if set(results) != set(ranks) or len(results) != len(summary['candidates']):
            raise ValueError('production summary does not match selected ranks')
        pending = {rank for rank, status in results.items() if status in {'paused', 'busy'}}
        state['unresolved_ranks'] = sorted(pending | set(state['unresolved_ranks']))
        state.update(status='partial' if state['unresolved_ranks'] else 'prepared',
                     production=str(output), inventory=production_inventory(output, summary))
        return
    stage('produce')
    run_step("agent_run.py", [candidates, "--config", config, "--height", "720", "--timeout", "1800"],
             None, log)
    agent = read(session / "agent_run.json")
    inventory = []
    for rank in agent["ranks"]:
        quality_path = session / "quality" / f"{rank:02d}.json"
        quality = read(quality_path)
        row = next(r for r in rows if r["rank"] == rank)
        event = row.get("event") or {}
        item = {"rank": rank, "quality": str(quality_path), "score": row.get("final"),
                "event": event.get("event_id"), "summary": event.get("summary"),
                "topic": (row.get("llm") or {}).get("type"), "warnings": quality["warnings"],
                "decision": "needs_review"}
        if quality["ok"] and not row.get("taboo"):
            pack = session / "publish" / f"{rank:02d}.json"
            if not pack.is_file():
                run_step("publish_pack.py", [candidates, "--config", config, "--rank", rank,
                                             "--cover-mode", "programmatic"], 300, log)
            item["pack"] = str(pack)
            package = read(pack)
            evidence = row.get("video_evidence") or {}
            if (event.get("status") == "ready" and evidence.get("observations")
                    and not evidence.get("needs_review") and not quality["warnings"]
                    and not package.get("taboo_hits")):
                item["decision"] = "qualified"
        inventory.append(item)
    state.update(status="partial" if state["unresolved_ranks"] else "prepared", inventory=inventory)


def production_inventory(output, summary):
    rows = []
    for candidate in summary['candidates']:
        if candidate['status'] != 'inventoried':
            continue
        path = output / f"{candidate['rank']:02d}" / 'inventory.json'
        topic = candidate.get('topic') or {}
        row = {'rank': candidate['rank'], 'inventory_path': str(path), 'version': candidate['current_version'],
               'score': topic.get('score') or 0, 'event': topic.get('event_id'), 'topic': topic.get('topic')}
        verified = proposal_item(row)
        if verified:
            rows.append(verified)
    return rows


def proposal_item(row):
    from clipper.agent_flow import probe
    from clipper.agent_production import artifacts_valid, fingerprint

    try:
        item = read(Path(row['inventory_path']))
        rendered = item['render']
        media = Path(rendered['media'])
        review = item['review']
        candidate = read(Path(row['inventory_path']).with_name('state.json'))
        if (item['simulation'] or candidate.get('status') != 'inventoried'
                or candidate.get('current_version') != item['version']
                or item['version'] != row['version'] or item['rank'] != row['rank']
                or review['verdict'] != 'pass' or review.get('basis') != 'actual_mp4_model'
                or review.get('media_sha256') != rendered['files'][str(media)]
                or not artifacts_valid(rendered['files'])
                or fingerprint(Path(item['draft'])) != item['draft_sha256']
                or read(Path(item['draft']))['id'] != item['version']
                or fingerprint(Path(item['pack'])) != item['pack_sha256']):
            return None
        info = probe(media)
        if info['height'] != 720 or not info['audio'] or info['duration'] <= 0:
            return None
        return {**row, 'decision': 'qualified', 'pack': item['pack'], 'quality': rendered['quality'],
                'media': str(media)}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return None


def build_daily_plan(root, now):
    day = beijing_day(now)
    ledger = DailyLedger(root / "daily.sqlite3")
    rows_by_streamer = {}
    for job_path in sorted((root / "queue").glob("*.json")):
        streamer = read(job_path)["streamer"]
        rows = rows_by_streamer.setdefault(streamer, [])
        status_path = root / "status" / job_path.name
        if not status_path.is_file():
            continue
        state = read(status_path)
        isolated = state.get('production_mode') == 'isolated'
        if state.get('status') != 'prepared' and not isolated:
            continue
        for item in state.get('inventory', []):
            if isolated:
                item = proposal_item(item)
                if not item:
                    continue
            rows.append({**item, "id": f"{job_path.stem}:{item['rank']}",
                         "event": f"{job_path.stem}:{item['event']}" if item["event"] else None,
                         "score": item["score"] or 0,
                         "traits": [item["topic"]] if item["topic"] else []})
    account_statuses = {}
    history_root = os.environ.get("KIYA_OPS_HISTORY_DIR")
    for streamer, rows in rows_by_streamer.items():
        account = ""
        history = {}
        history_path = Path(history_root) / f"{streamer}.json" if history_root else None
        if history_path and history_path.is_file() and rows:
            from clipper.publish_account import status

            history = read(history_path)
            uid = history.get("account_uid")
            ids = history.get("occupied_ids")
            try:
                stamp = datetime.fromisoformat(history.get("verified_at", ""))
                age = (now - stamp).total_seconds()
            except (ValueError, TypeError):
                age = -1
            if (history.get("day") == day and history.get("complete") is True and 0 <= age <= 300
                    and isinstance(uid, str) and uid.isdigit() and isinstance(ids, list)
                    and all(isinstance(x, str) and x for x in ids)):
                login = status({}, streamer)
                if (login["state"] == "logged_in" and login["source"] == "website"
                        and login["account"]["uid"] == uid):
                    account = uid
                    ledger.reconcile(account, day, ids, complete=True)
        snapshot = ledger.snapshot(account, day)
        unique_rows = {row['id']: row for row in rows}
        plan = schedule_inventory(unique_rows.values(), snapshot, now, history.get("recent_events") or (),
                                  history.get("recent_topics") or ())
        for outcomes in plan.values():
            for outcome in outcomes:
                source = unique_rows[outcome['id']]
                if 'inventory_path' in source:
                    outcome.update({key: source[key] for key in ('inventory_path', 'version', 'media', 'pack', 'quality')})
        account_statuses[streamer] = {"budget": snapshot, "account_uid": account or None, "plan": plan}
    save(root / "plans" / f"{day}.json", {"mode": "proposal", "streamers": account_statuses})


def wait_for_budget(state, now, marker):
    if state["status"] not in {"retry", "partial", "blocked"}:
        return
    if not marker.is_file():
        return
    blocked_at = datetime.fromtimestamp(marker.stat().st_mtime, BEIJING)
    next_day = blocked_at.date() + timedelta(days=1)
    state.update(status="waiting_for_budget", attempts=state["attempts"] - 1,
                 retry_at=max(now.timestamp(), datetime.combine(next_day, wall_time.min, BEIJING).timestamp()))


def drain_one(root, now):
    for job_path in sorted((root / "queue").glob("*.json")):
        job = read(job_path)
        status_path = root / "status" / job_path.name
        state = read(status_path) if status_path.is_file() else {}
        if state.get("status") in {"prepared", "no_candidates"}:
            continue
        resume_marker = status_path.with_suffix(status_path.suffix + '.resume')
        manually_resumed = (resume_marker.is_file() and state.get('production_mode') == 'isolated'
                            and state.get('status') in {'partial', 'retry', 'blocked'})
        if state.get('status') == 'blocked' and not manually_resumed:
            continue
        if now < job['ended'] + 120 or (not manually_resumed and now < state.get('retry_at', 0)):
            continue
        attempts = state.get("attempts", 0) + 1
        state.setdefault('production_mode', 'isolated' if os.environ.get('KIYA_OPS_AGENT_V2') == '1' else 'legacy')
        state.update(status="running", attempts=attempts, retry_at=now + 900)
        save(status_path, state)
        resume_marker = status_path.with_suffix(status_path.suffix + '.resume')
        resume_marker.unlink(missing_ok=True)
        budget_marker = status_path.with_suffix(".budget-blocked")
        budget_marker.unlink(missing_ok=True)
        os.environ["KIYA_OPS_BUDGET_BLOCKED"] = str(budget_marker)
        try:
            prepare(root, job_path, job, state, now)
            state.pop("error", None)
            if state["status"] == "waiting_for_recording":
                state["attempts"] -= 1
            elif state["status"] == "partial" and attempts >= 3:
                state["status"] = "blocked"
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
            state.update(status="blocked" if attempts >= 3 else "retry", error=str(exc))
        finally:
            os.environ.pop("KIYA_OPS_BUDGET_BLOCKED", None)
        ledger = DailyLedger(os.environ["KIYA_OPS_LEDGER"])
        finished_at = datetime.now(timezone.utc)
        state["budget"] = ledger.snapshot("", beijing_day(finished_at))
        wait_for_budget(state, finished_at, budget_marker)
        save(status_path, state)
        return


def main():
    import fcntl

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    root = parser.parse_args().root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["KIYA_OPS_LEDGER"] = str(root / "daily.sqlite3")
    with (root / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        drain_one(root, time.time())
        build_daily_plan(root, datetime.now(timezone.utc))


if __name__ == "__main__":
    main()
