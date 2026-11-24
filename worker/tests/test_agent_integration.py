import os
from pathlib import Path
import sys

import pytest

WORKER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKER))
from clipper.agent_flow import digest, load, save
from clipper.agent_production import Production
from scripts.agent_produce import RecordedRoles
from scripts import ops_prepare


def test_live_review_sends_exact_mp4_and_reserves_before_network(tmp_path, monkeypatch):
    import base64
    from clipper.agent_flow import Pause
    from clipper.agent_live import LiveRoles
    from clipper.operations import DailyLedger

    ledger = tmp_path / 'daily.sqlite3'
    monkeypatch.setenv('KIYA_OPS_LEDGER', str(ledger))
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'test-credential')
    monkeypatch.setenv('QWEN_API_KEY', 'test-credential')
    monkeypatch.setenv('KIYA_OPS_LLM_MAX_FEN', '')
    media = tmp_path / 'actual.mp4'
    media.write_bytes(b'fixture MP4 payload')
    receipt, quality = tmp_path / 'receipt.json', tmp_path / 'quality.json'
    for path in (receipt, quality, media.with_suffix('.edit.json')):
        save(path, {})
    def no_network(*args, **kwargs):
        pytest.fail('network must not run without a verified ceiling')
    monkeypatch.setattr('urllib.request.urlopen', no_network)
    roles = LiveRoles(tmp_path / 'candidates.json', tmp_path / 'run', tmp_path / 'config.yaml', ledger)
    rendered = {'media': str(media), 'receipt': str(receipt), 'quality': str(quality),
                'files': {str(media): digest(media)}}
    with pytest.raises(Pause, match='KIYA_OPS_LLM_MAX_FEN'):
        roles.review({'evidence': [], 'pack': {'title': 'fixture'}}, 0, rendered)
    assert not ledger.exists()
    monkeypatch.setenv('KIYA_OPS_LLM_MAX_FEN', '10')

    class Response:
        def __enter__(self):
            return [b'data: {"choices":[{"delta":{"content":"{\\"verdict\\":\\"pass\\",\\"reason\\":\\"fixture\\"}"},"finish_reason":"stop"}]}\n']

        def __exit__(self, *args):
            return False

    def transport(request, **kwargs):
        import json
        from datetime import datetime, timezone
        from clipper.operations import beijing_day

        assert DailyLedger(ledger).snapshot('', beijing_day(datetime.now(timezone.utc)))['booked_fen'] == 10
        url = json.loads(request.data)['messages'][0]['content'][0]['video_url']['url']
        assert base64.b64decode(url.split(',', 1)[1]) == media.read_bytes()
        return Response()

    monkeypatch.setattr('urllib.request.urlopen', transport)
    decision = roles.review({'evidence': [], 'pack': {'title': 'fixture'}}, 0, rendered)
    assert decision['verdict'] == 'pass' and decision['media_sha256'] == digest(media)


def test_proposal_rechecks_version_and_artifacts(tmp_path, monkeypatch):
    media = tmp_path / '720.mp4'
    media.write_bytes(b'probe stub')
    from clipper import agent_flow
    monkeypatch.setattr(agent_flow, 'probe', lambda _: {'height': 720, 'audio': True, 'duration': 6})
    draft, pack, inv = (tmp_path / f'{name}.json' for name in ('draft', 'pack', 'inventory'))
    save(draft, {'id': 'v1'})
    save(pack, {'title': 'test'})
    save(tmp_path / 'state.json', {'status': 'inventoried', 'current_version': 'v1'})
    row = {'rank': 1, 'version': 'v1', 'inventory_path': str(inv)}
    save(inv, {'simulation': False, 'rank': 1, 'version': 'v1', 'draft': str(draft), 'draft_sha256': digest(draft),
               'pack': str(pack), 'pack_sha256': digest(pack),
               'review': {'verdict': 'pass', 'basis': 'actual_mp4_model', 'media_sha256': digest(media)},
               'render': {'media': str(media), 'quality': 'quality.json', 'files': {str(media): digest(media)}}})
    assert ops_prepare.proposal_item(row)['decision'] == 'qualified'
    assert ops_prepare.proposal_item(dict(row, version='stale')) is None
    media.write_bytes(b'changed')
    assert ops_prepare.proposal_item(row) is None


def test_isolated_queue_retry_and_proposal(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    import time

    sys.path.insert(0, str(WORKER.parent / 'deploy' / 'monitor'))
    import kiya_monitor as monitor

    root = tmp_path / 'ops'
    now = time.time()
    settings = {'KIYA_OPS_DIR': str(root)}
    for _ in range(2):
        monitor.enqueue_operations(settings, tmp_path, '123', now - 900, now - 300)
    jobs = list((root / 'queue').glob('*.json'))
    assert len(jobs) == 1
    job = jobs[0]
    session = root / 'sessions' / job.stem / 'kiya' / 'fixed'
    status = root / 'status' / job.name
    save(session / 'candidates.json', {'candidates': [{'rank': 1}, {'rank': 2}]})
    save(session / 'rerank.json', {'candidates': [{'rank': i, 'llm': {'type': '温馨'}} for i in (1, 2)]})
    save(status, {'sources': [], 'session': 'fixed', 'screen_complete': True})
    monkeypatch.setenv('KIYA_OPS_AGENT_V2', '1')
    monkeypatch.setenv('KIYA_OPS_LEDGER', str(root / 'daily.sqlite3'))
    monkeypatch.delenv('KIYA_OPS_HISTORY_DIR', raising=False)
    executions = []

    def step(script, args, timeout, log):
        assert script == 'agent_produce.py' and '--live' in args
        executions.append(script)
        output = args[args.index('--out') + 1]
        save(output / 'summary.json', {'candidates': [
            {'rank': 1, 'status': 'inventoried', 'current_version': 'fixed',
             'topic': {'event_id': 'fixed-event', 'topic': '温馨'}},
            {'rank': 2, 'status': 'paused' if len(executions) == 1 else 'needs_review'}]})

    monkeypatch.setattr(ops_prepare, 'run_step', step)
    monkeypatch.setattr(ops_prepare, 'proposal_item', lambda row: {
        **row, 'decision': 'qualified', 'media': '720.mp4', 'pack': 'pack.json', 'quality': 'quality.json'})
    instant = datetime.fromtimestamp(now, timezone.utc)
    ops_prepare.drain_one(root, now)
    assert load(status)['unresolved_ranks'] == [2]
    ops_prepare.build_daily_plan(root, instant)
    plan_path = next((root / 'plans').glob('*.json'))
    first_plan = load(plan_path)
    assert len(first_plan['streamers']['kiya']['plan']['keep_in_inventory']) == 1
    paused_status = load(status)
    ops_prepare.drain_one(root, now + 901)
    ops_prepare.drain_one(root, now + 1802)
    assert load(status)['status'] == 'prepared' and len(executions) == 2
    for _ in range(2):
        ops_prepare.build_daily_plan(root, instant)
        plan = load(next((root / 'plans').glob('*.json')))['streamers']['kiya']['plan']
        assert len(plan['keep_in_inventory']) == 1 and not plan['would_publish']
    if os.environ.get('KIYA_INTEGRATION_OUT'):
        save(Path(os.environ['KIYA_INTEGRATION_OUT']) / 'queue-rehearsal.json', {
            'queue': load(job), 'paused': paused_status, 'first_plan': first_plan,
            'resumed': load(status), 'final_plan': load(plan_path), 'simulated_producer_calls': executions})


@pytest.mark.skipif(not os.environ.get('KIYA_INTEGRATION_VIDEO'), reason='fixed material path required')
def test_real_caption_and_overlay_renders(tmp_path):
    video = Path(os.environ['KIYA_INTEGRATION_VIDEO']).resolve()
    root = Path(os.environ.get('KIYA_INTEGRATION_OUT', tmp_path)).resolve()
    source = root / 'input' / 'candidates.json'
    save(source, {'segments': [{'video': str(video), 'offset': 0, 'duration': 75}], 'candidates': [
        {'rank': 1, 't': 46, 'clip_start': 44, 'clip_end': 50, 'file_index': 0, 'local_start': 44},
        {'rank': 2, 't': 46, 'clip_start': 44, 'clip_end': 50, 'file_index': 0, 'local_start': 44}]})
    transcript = {'t0': 45, 't1': 45.75, 'text': '那个那个结果就是三'}
    save(source.with_name('transcript.json'), {'sentences': [transcript]})
    evidence = {'id': 'av', 'start': 44, 'end': 49, 'modality': 'av', 'content': '工程夹具区间'}
    draft = {'window': {'start': 44, 'end': 50}, 'trim': {'start': 0, 'end': 6}, 'items': [],
             'required_evidence': [evidence], 'subtitles': {'enabled': True}}
    row = {'topic': {'verdict': 'worth', 'reason': '工程夹具', 'evidence_ids': ['av']}, 'evidence': [evidence],
           'pack': {'title': '工程夹具'}, 'fixture_expectations': {'0': 6, '1': 6}}
    passed = {'verdict': 'pass', 'reason': '工程夹具预期'}
    overlay = {'id': 'cover', 'type': 'text', 'text': '遮挡测试', 'x': 0.5, 'y': 0.5, 'size': 300,
               'start': 2, 'duration': 2}
    roles = root / 'roles.json'
    save(roles, {'simulation': True, 'ranks': {
        '1': dict(row, draft=draft, reviews=[{'verdict': 'repair', 'reason': '压缩测试字幕',
            'action': 'shorten_subtitle', 'output_range': [1, 2],
            'subtitle': {'t0': 45, 't1': 45.75, 'original': transcript['text'], 'text': '结果是三'}}, passed]),
        '2': dict(row, draft=dict(draft, items=[overlay], subtitles={'enabled': False}), reviews=[
            {'verdict': 'repair', 'reason': '移除中心覆盖字', 'action': 'remove_overlay',
             'item_id': 'cover', 'output_range': [2, 4]}, passed])}})
    runner = Production(source, root / 'run', RecordedRoles(roles), WORKER.parent / 'config/kiya.yaml')
    results = runner.run()
    assert all(r['status'] == 'inventoried' for r in results), [r['events'][-1] for r in results]
    artifacts = []
    for row in results:
        before, after = (row['steps'][f'render-{i}']['result'] for i in range(2))
        assert before['files'][before['media']] != after['files'][after['media']]
        from subprocess import check_output

        stamp = '1.4' if row['rank'] == 1 else '3'
        for label, rendered in [('before', before), ('after', after)]:
            frame = check_output(['ffmpeg', '-v', 'error', '-ss', stamp, '-i', rendered['media'],
                                  '-frames:v', '1', '-f', 'image2pipe', '-vcodec', 'png', '-'])
            (root / f"{row['rank']:02d}-{label}.png").write_bytes(frame)
        if row['rank'] == 1:
            def fast(rendered):
                return [f for f in load(Path(rendered['receipt']))['arrangement']['findings']
                        if f['code'] == 'caption_reading_review']

            assert fast(before) and not fast(after)
        artifacts.append({'rank': row['rank'], 'before': before, 'after': after,
                          'inventory': str(root / 'run' / f"{row['rank']:02d}" / 'inventory.json')})
    save(root / 'acceptance.json', artifacts)
    again = runner.run()
    assert [r['current_version'] for r in again] == [r['current_version'] for r in results]
    assert not list(source.parent.glob('edits/*.json'))
