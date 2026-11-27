"""Focused isolated historical worker contracts; no paid services."""
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest
from clipper.agent_flow import digest, load, save
from scripts.historical_trial_worker import HistoricalWorker


def claim(path):
    from contextlib import closing
    from clipper.agent_flow import session_lock
    import sqlite3
    try:
        with closing(session_lock(Path(path))):
            import time
            time.sleep(0.3)
            return True
    except sqlite3.OperationalError:
        return False


@pytest.fixture
def job(tmp_path):
    original = tmp_path / 'original'
    original.mkdir()
    rec = tmp_path / 'recordings'
    rec.mkdir()
    video = rec / 'fixture.mp4'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=1280x720:rate=25', '-f', 'lavfi', '-i',
                    'sine=frequency=440:sample_rate=48000', '-t', '7', '-c:v', 'libx264',
                    '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-y', str(video)], check=True, timeout=30)
    source = original / 'candidates.json'
    save(source, {'session': 'same-production-session', 'streamer': 'kiya', 'segments': [
        {'video': str(video), 'offset': 0, 'duration': 7}], 'candidates': [
        {'rank': 1, 'clip_start': 0, 'clip_end': 6, 't': 1, 'file_index': 0, 'local_start': 0}]})
    save(original / 'transcript.json', {'sentences': [{'t0': 0, 't1': 1, 'text': 'fixture'}]})
    root = tmp_path / 'acceptance' / 'history'
    worker = HistoricalWorker(root, fixture=True, minimum=0)
    task = {'schema': 1, 'source': 'historical_trial', 'task_id': f'history-{uuid4()}',
            'session': 'same-production-session', 'streamer': 'kiya', 'rank': 1, 'candidates': str(source),
            'source_root': str(original), 'rec_root': str(rec), 'input_revision': digest(source),
            'input_files': {str(video): digest(video)}, 'mode': 'fixture', 'publish': False, 'max_cost_fen': 0}
    queue = root / 'queue' / f'{task["task_id"]}.json'
    save(queue, task)
    return worker, task, queue, original, video


def state(worker, task):
    return load(worker.root / 'status' / f'{task["task_id"]}.json')


def test_explicit_fixture_and_budget_gate(job):
    worker, task, queue, _, _ = job
    worker.fixture = False
    assert worker.once() == 1
    assert state(worker, task)['status'] == 'waiting_for_budget'
    assert worker.once() == 0
    assert not list((worker.root / 'production').rglob('*.mp4'))


def test_missing_source_failure_is_readable_and_terminal(job):
    worker, task, queue, _, video = job
    video.unlink()
    assert worker.once() == 1
    result = state(worker, task)
    assert result['status'] == 'failed'
    assert result['phase'] == 'snapshot'
    assert result['reason']
    assert worker.once() == 0


def test_hash_binding_and_containment(job):
    worker, task, queue, original, video = job
    task['input_files'][str(video)] = '0' * 64
    save(queue, task)
    worker.once()
    assert 'hash mismatch' in state(worker, task)['reason']
    task['task_id'] = f'history-{uuid4()}'
    task['candidates'] = str(original.parent / 'outside.json')
    save(Path(task['candidates']), {'segments': [], 'candidates': []})
    save(worker.root / 'queue' / f'{task["task_id"]}.json', task)
    worker.once()
    assert 'outside' in state(worker, task)['reason']


def test_restart_snapshot_integrity_and_no_source_edits(job):
    worker, task, queue, original, video = job
    before = {p: digest(p) for p in [*original.rglob('*'), video] if p.is_file()}
    directory = worker.root / 'production' / task['task_id']
    directory.mkdir()
    snapshot = worker.snapshot(task, directory)
    assert worker.snapshot(task, directory) == snapshot
    assert before == {p: digest(p) for p in before}
    snapshot.write_text('{}')
    with pytest.raises(ValueError, match='checkpoint hash'):
        worker.snapshot(task, directory)


def test_process_failure_and_redaction(job, monkeypatch):
    worker, task, queue, _, _ = job
    def fail(*args):
        raise RuntimeError('api_key=secret123 renderer fixture crashed')
    monkeypatch.setattr(worker, 'roles', fail)
    worker.once()
    result = state(worker, task)
    assert result['status'] == 'failed'
    assert result['phase'] == 'production'
    assert 'secret123' not in result['reason']


def consume(root):
    return HistoricalWorker(root, fixture=False, minimum=0).once()


def test_duplicate_workers_consume_once(job):
    worker, task, queue, original, video = job
    with ProcessPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(consume, [str(worker.root), str(worker.root)])) == 1
    assert state(worker, task)['status'] == 'waiting_for_budget'


def test_cross_process_lock_excludes_duplicate_claims(tmp_path):
    path = str(tmp_path / 'claim.sqlite3')
    from contextlib import closing
    from clipper.agent_flow import session_lock
    with closing(session_lock(Path(path))):
        with ProcessPoolExecutor(max_workers=2) as pool:
            assert list(pool.map(claim, [path, path])) == [False, False]
    assert claim(path)


def test_true_media_success_restart_idempotent_production_unchanged(job):
    worker, task, queue, original, video = job
    before = {p: digest(p) for p in [*original.rglob('*'), video] if p.is_file()}
    assert worker.once() == 1
    result = state(worker, task)
    assert result['status'] == 'has_clips', json.dumps(result, ensure_ascii=False)
    inventory = load(Path(result['inventory_path']))
    media = Path(inventory['render']['media'])
    assert media.is_file() and media.stat().st_size > 1000
    subprocess.run(['ffmpeg', '-v', 'error', '-ss', '0.5', '-i', str(media),
                    '-t', '0.25', '-f', 'null', '-'], check=True, timeout=30)
    first = digest(media)
    assert worker.once() == 0
    worker.status(task, 'running', 'production')
    assert worker.once() == 1
    assert state(worker, task)['status'] == 'has_clips'
    assert digest(media) == first
    assert before == {p: digest(p) for p in before}
    assert inventory['simulation'] is True
    assert load(worker.root / 'production' / task['task_id'] / 'simulation-ledger.json')['paid_requests'] == 0
