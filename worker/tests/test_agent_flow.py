import sys
from contextlib import closing
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clipper.agent_flow import RunState, Pause, session_lock
from clipper.operations import DailyLedger
from clipper.agent_evidence import subtract, topic_contract
from clipper.agent_flow import Review


def test_evidence_gap_and_missing_value_are_distinct():
    assert subtract([10, 20], [(8, 12), (14, 16), (18, 25)]) == [[12, 14], [16, 18]]
    recommendation = {'verdict': 'worth', 'reason': 'turn', 'evidence_ids': ['e'], 'topic': '反差'}
    assert topic_contract(recommendation, [{'id': 'e'}])['priority'] == 3
    with pytest.raises(Review, match='references'):
        topic_contract(recommendation, [])


def test_step_resume_and_input_change(tmp_path):
    path = tmp_path / 'state.json'
    calls = []
    def perform():
        calls.append(1)
        return {'result': len(calls)}
    assert RunState(path, None).step('topic', [1], perform) == {'result': 1}
    assert RunState(path, None).step('topic', [1], perform) == {'result': 1}
    assert RunState(path, None).step('topic', [2], perform) == {'result': 2}


def test_unknown_cost_stays_booked_and_blocks_retry(tmp_path):
    state = RunState(tmp_path / 'state.json', DailyLedger(tmp_path / 'daily.sqlite3'))
    def timeout(_):
        raise TimeoutError('no usage')
    with pytest.raises(TimeoutError):
        state.call('evidence', 4999, timeout)
    with pytest.raises(Pause, match='exhausted'):
        state.call('retry', 2, timeout)
    row = state.data['attempts'][0]
    assert row['status'] == 'unknown'
    assert state.ledger.snapshot('kiya', row['day'])['booked_fen'] == 4999


def test_lock_released_on_close(tmp_path):
    import sqlite3
    target = tmp_path / 'lock.sqlite3'
    with closing(session_lock(target)):
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            session_lock(target)
    with closing(session_lock(target)):
        pass


@pytest.mark.parametrize('height, matching, covered, reason', [
    (480, True, True, '720p'), (720, False, True, 'revision'),
    (720, True, False, 'required evidence'),
])
def test_inventory_gate_rejects_bad_media(tmp_path, monkeypatch, height, matching, covered, reason):
    from clipper import agent_production as production
    from clipper.agent_flow import save
    media = tmp_path / 'final' / 'named_720p.mp4'
    media.parent.mkdir()
    media.write_bytes(b'probe stub')
    edit = {'required_evidence': [{'id': 'a', 'start': 1, 'end': 2}]}
    save(media.with_suffix('.edit.json'), edit)
    save(media.with_suffix('.evidence.json'), {
        'partial': False, 'media_size': media.stat().st_size, 'media_mtime_ns': media.stat().st_mtime_ns,
        'edit_revision': production.revision(edit) if matching else 'stale',
        'status': 'time_covered' if covered else 'missing',
    })
    save(tmp_path / 'quality' / '01.json', {'ok': True, 'media': str(media)})
    monkeypatch.setattr(production, 'probe', lambda _: {'height': height, 'audio': True, 'duration': 2})
    with pytest.raises(Review, match=reason):
        production.verify_render(tmp_path, edit)


def test_disk_pause_and_recovery(tmp_path, monkeypatch):
    from clipper import agent_flow
    from types import SimpleNamespace
    monkeypatch.setattr(agent_flow.shutil, 'disk_usage', lambda _: SimpleNamespace(free=0))
    with pytest.raises(Pause, match='low disk'):
        agent_flow.disk_guard([tmp_path], 1)
    state = RunState(tmp_path / 'state.json', None)
    state.data['attempts'].append({'id': 'lost', 'status': 'in_flight'})
    state.commit()
    recovered = RunState(state.path, None)
    recovered.recover()
    assert recovered.data['attempts'][0]['status'] == 'unknown'


def test_paid_result_persists_before_step_checkpoint(tmp_path):
    ledger = DailyLedger(tmp_path / 'daily.sqlite3')
    path = tmp_path / 'state.json'
    result = {'id': 'audio-evidence'}
    assert RunState(path, ledger).call('answer', 10, lambda _: (result, {}, 2), request_key='same') == result
    def duplicate(_):
        pytest.fail('replayed completed request')
    state = RunState(path, ledger)
    assert state.call('answer', 10, duplicate, request_key='same') == result
    assert len(state.data['attempts']) == 1


def test_two_processes_compete_for_last_reservation(tmp_path):
    import subprocess

    path = tmp_path / 'daily.sqlite3'
    ledger = DailyLedger(path)
    assert ledger.reserve_cost('kiya', '2026-11-24', 'existing', 4900) == 'reserved'
    script = ('import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); '
              'from clipper.operations import DailyLedger; '
              'print(DailyLedger(Path(sys.argv[2])).reserve_cost("kiya", "2026-11-24", sys.argv[3], 100))')
    worker = str(Path(__file__).resolve().parents[1])
    processes = [subprocess.Popen([sys.executable, '-c', script, worker, str(path), str(i)],
                                  stdout=subprocess.PIPE, text=True) for i in range(2)]
    assert sorted(p.communicate(timeout=20)[0].strip() for p in processes) == ['daily_limit', 'reserved']
    assert all(p.returncode == 0 for p in processes)
    assert ledger.snapshot('kiya', '2026-11-24')['booked_fen'] == 5000


def test_same_checkpoint_resumes_after_beijing_midnight(tmp_path, monkeypatch):
    from datetime import datetime
    from clipper import agent_flow

    class Clock(datetime):
        current = '2026-11-24T15:59:59+00:00'

        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(cls.current)

    monkeypatch.setattr(agent_flow, 'datetime', Clock)
    ledger = DailyLedger(tmp_path / 'daily.sqlite3')
    assert ledger.reserve_cost('kiya', '2026-11-24', 'existing', 5000) == 'reserved'
    path = tmp_path / 'state.json'

    def answer():
        return RunState(path, ledger).call('answer', 10, lambda _: ('ok', {}, 2), request_key='answer')

    with pytest.raises(Pause, match='exhausted'):
        answer()
    Clock.current = '2026-11-24T16:00:00+00:00'
    assert answer() == 'ok'
    assert [a['status'] for a in RunState(path, ledger).data['attempts']] == ['blocked', 'completed']
    assert ledger.snapshot('kiya', '2026-11-24')['booked_fen'] == 5000
    assert ledger.snapshot('kiya', '2026-11-25')['booked_fen'] == 2


def test_local_caption_and_overlay_repairs():
    from clipper.agent_production import repair
    edit = {'window': {'start': 10, 'end': 14}, 'items': [{'id': 'overlay', 'start': 1, 'duration': 1}]}
    mapping = [{'source_start': 10, 'source_end': 14, 'output_start': 0, 'output_end': 4}]
    issue = {'output_range': [1, 2], 'reason': 'fixture review'}
    revised = repair(edit, dict(issue, action='remove_overlay', item_id='overlay'), [], mapping)
    assert revised['items'] == [] and len(edit['items']) == 1
    with pytest.raises(Review, match='output_range.*fixture review'):
        repair(edit, {'action': 'remove_overlay', 'item_id': 'overlay', 'reason': 'fixture review'}, [], mapping)
    with pytest.raises(Review, match='location'):
        repair(edit, dict(issue, action='remove_overlay', item_id='overlay', output_range=[3, 4]), [], mapping)
    transcript = {'sentences': [{'t0': 11, 't1': 12, 'text': '那个结果是三'}]}
    subtitle = {'t0': 11, 't1': 12, 'original': '那个结果是三', 'text': '结果是三'}
    decision = dict(issue, action='shorten_subtitle', subtitle=subtitle)
    assert repair(edit, decision, [], mapping, transcript)['subtitles']['sentence_overrides'] == [subtitle]
    with pytest.raises(Review, match='shortening'):
        repair(edit, dict(decision, subtitle=dict(subtitle, text='结果是四')), [], mapping, transcript)


def test_rank5_opening_overlay_repair_preserves_model_review():
    from clipper.agent_production import rank5_opening_overlay_decision, repair

    edit = {'window': {'start': 10, 'end': 14}, 'items': [
        {'id': '4334c3f5', 'type': 'text', 'text': '开头原稿文案', 'start': 0.2, 'duration': 3},
        {'id': 'keep', 'type': 'text', 'text': '另一个效果', 'start': 2, 'duration': 1},
    ]}
    review = {'verdict': 'repair', 'reason': '刚做完奶粉雪花酥与原生标题重叠遮挡', 'media_sha256': 'verified'}
    rendered = {'media': 'preview.mp4', 'files': {'preview.mp4': 'verified'}}
    mapping = [{'source_start': 10, 'source_end': 14, 'output_start': 0, 'output_end': 4}]
    decision = rank5_opening_overlay_decision(edit, review, rendered)
    assert [item['id'] for item in repair(edit, decision, [], mapping)['items']] == ['keep']
    assert 'action' not in review and len(edit['items']) == 2
    with pytest.raises(Review, match='不匹配'):
        rank5_opening_overlay_decision(edit, {**review, 'media_sha256': 'stale'}, rendered)
    with pytest.raises(Review, match='唯一'):
        rank5_opening_overlay_decision({**edit, 'items': edit['items'] * 2}, review, rendered)
