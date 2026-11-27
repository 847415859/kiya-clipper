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
            import json
            from clipper.protection import revision
            answer = {'verdict': 'pass', 'reason': 'fixture', 'media_sha256': digest(media), 'edit_revision': revision({})}
            return [('data: ' + json.dumps({'choices': [{'delta': {'content': json.dumps(answer)}, 'finish_reason': 'stop'}]}) + '\n').encode()]

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
    audits = [load(path) for path in (tmp_path / 'run' / 'model_requests').glob('*.json')]
    assert sorted(a['request_attempts'] for a in audits) == [0, 1]
    completed = next(a for a in audits if a['status'] == 'completed')
    assert completed['media'][0]['sha256'] == digest(media)
    assert completed['configured_max_fen'] == '10'


@pytest.mark.parametrize('supported', [True, False, None])
def test_rank5_context_single_attempt_and_target_only(tmp_path, monkeypatch, supported):
    import base64
    import json
    from datetime import datetime, timezone
    from clipper import agent_live
    from clipper.agent_flow import Review, RunState
    from clipper.operations import DailyLedger, beijing_day

    ledger = tmp_path / 'daily.sqlite3'
    monkeypatch.setenv('KIYA_OPS_LEDGER', str(ledger))
    monkeypatch.setenv('KIYA_OPS_LLM_MAX_FEN', '368')
    monkeypatch.setenv('QWEN_BASE_URL', agent_live.DEFAULT_BASE_URL)
    monkeypatch.setenv('QWEN_API_KEY', 'test-credential')
    monkeypatch.setattr(agent_live, 'probe', lambda _: {'height': 360, 'audio': True, 'duration': 8.0})
    roles = agent_live.LiveRoles(tmp_path / 'candidates.json', tmp_path / 'run', tmp_path / 'config.yaml', ledger)
    roles.contextual_gap = 'gap-1'
    roles.context_media = [tmp_path / name for name in ('00.mp4', '01-preview.mp4')]
    for i, path in enumerate(roles.context_media):
        path.write_bytes(f'clip{i}'.encode())
    question = {'id': 'gap-1', 'range': [2058.223, 2060.080]}
    context = roles.context_inputs(question)
    assert context['mapping'][0]['role'] == 'context_only'
    assert context['mapping'][1]['target_local_range'] == [0, 1.857]
    calls = []

    class Response:
        def __enter__(self):
            answer = 'not JSON' if supported is None else json.dumps({
                'supported': supported, 'quote': 'fixture quote' if supported else '',
                'explanation': 'fixture context', 'reason': 'unclear audio' if not supported else ''})
            event = {'choices': [{'delta': {'content': answer}, 'finish_reason': 'stop'}],
                     'usage': {'prompt_tokens': 20, 'completion_tokens': 10}}
            return [('data: ' + json.dumps(event)).encode()]

        def __exit__(self, *args):
            return False

    def transport(request, **kwargs):
        assert DailyLedger(ledger).snapshot('', beijing_day(datetime.now(timezone.utc)))['booked_fen'] == 368
        body = json.loads(request.data)
        assert body['model'] == 'qwen3.8-omni-flash'
        parts = body['messages'][0]['content']
        assert [base64.b64decode(row['video_url']['url'].split(',', 1)[1]) for row in parts[:2]] == [b'clip0', b'clip1']
        assert agent_live.CONTEXT_QUESTION in parts[2]['text']
        calls.append(body)
        return Response()

    monkeypatch.setattr('urllib.request.urlopen', transport)
    state = RunState(roles.output / '05/state.json', None)
    old = state.step('evidence-0-answer', ['original'], lambda: {'content': 'original'})
    run = lambda: state.step('evidence-1-answer-context', context, lambda: roles.answer_context(question, context))
    if supported is None:
        for _ in range(2):
            with pytest.raises(Review, match='输出不合法'):
                run()
    else:
        result = run()
        assert run() == result
        assert roles.answer_context(question, context) == result  # audit survives before checkpoint commit
        if supported:
            assert [result['answer'][k] for k in ('start', 'end')] == question['range']
            assert result['answer']['content'] == 'fixture quote'
        else:
            assert result['answer'] is None and result['reason'] == 'unclear audio'
    assert state.step('evidence-0-answer', ['original'], lambda: pytest.fail('recomputed'), reuse_only=True) == old
    with pytest.raises(Review, match='not rerunning'):
        state.step('evidence-0-answer', ['changed'], lambda: pytest.fail('recomputed'), reuse_only=True)
    assert len(calls) == 1
    audit = load(next((roles.output / 'model_requests').glob('*.json')))
    assert audit['request_attempts'] == 1 and audit['usage']['prompt_tokens'] == 20
    roles.context_media[1].write_bytes(b'changed')
    assert roles.context_inputs(question) != context


def test_manual_rank5_evidence_review_binds_media_and_keeps_model_audit(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from clipper.agent_live import LiveRoles, RANK5_TARGET
    from clipper.agent_flow import Review
    from clipper.protection import revision

    clips = [tmp_path / name for name in ('before.mp4', 'target-preview.mp4')]
    for index, path in enumerate(clips):
        path.write_bytes(f'clip-{index}'.encode())
    ledger = tmp_path / 'ledger.sqlite'
    roles = LiveRoles(tmp_path / 'candidates.json', tmp_path / 'run', tmp_path / 'config.yaml', ledger)
    roles.contextual_gap = 'gap-1'
    roles.context_media = clips
    monkeypatch.setattr('clipper.agent_live.probe', lambda _: {'height': 360, 'audio': True, 'duration': 8.0})
    question = {'id': 'gap-1', 'range': RANK5_TARGET, 'kind': 'response'}
    context = roles.context_inputs(question)
    record_path = tmp_path / 'manual_reviews' / 'gap-1-human.json'
    record_path.parent.mkdir()
    model_audit = tmp_path / 'omni-original.json'
    save(model_audit, {'status': 'completed', 'response': {'supported': False, 'reason': '只有背景音乐'},
                       'request_attempts': 1, 'usage': {'prompt_tokens': 30, 'completion_tokens': 8}})
    save(record_path, {'schema': 1, 'session': '20261124-2151', 'streamer': 'kiya', 'rank': 5,
         'question_id': 'gap-1', 'quote': '我有点嫌弃他', 'source_range': RANK5_TARGET,
         'vocal_range': [2058.223, 2059.423],
         'media': {'assets': context['assets'], 'mapping': context['mapping']}, 'reviewer': 'user-confirmed',
         'reviewed_at': datetime.now(timezone.utc).isoformat(), 'note': '用户复听确认；原始模型结论保留'})
    answer = roles.answer_context(question, context, record_path)
    assert answer['answer']['content'] == '我有点嫌弃他'
    assert answer['answer']['basis'] == 'human_review'
    assert answer['manual_review']['sha256'] == digest(record_path)
    assert load(model_audit)['response']['reason'] == '只有背景音乐'
    clips[1].write_bytes(b'changed')
    with pytest.raises(Review, match='media changed'):
        roles.manual_context_review(question, context, record_path)


def test_manual_confirmation_does_not_replace_original_audio_coverage():
    from clipper.protection import coverage
    required = [{'id': 'manual-gap-1', 'start': 2058.223, 'end': 2060.080, 'continuous': True}]
    result = coverage(required, [(2058.223, 2059.0)])
    assert result['status'] == 'missing'
    assert result['checks'][0]['missing_source_ranges'] == [[2059.0, 2060.08]]


def test_render_manual_evidence_uses_the_human_row_even_after_model_projection(tmp_path):
    from clipper.agent_flow import Review
    from clipper.agent_live import LiveRoles, RANK5_TARGET

    record = tmp_path / 'manual.json'
    save(record, {'quote': '我有点嫌弃他', 'media': {'assets': [{'sha256': 'before'}, {'sha256': 'after'}]}})
    audit = {'path': str(record), 'sha256': digest(record)}
    media = tmp_path / 'render.mp4'
    receipt = tmp_path / 'receipt.json'
    start, end = RANK5_TARGET
    save(receipt, {'source_to_output': [{'source_start': start, 'source_end': end}]})
    projection = {'id': 'manual-gap-1', 'start': start, 'end': end, 'basis': 'model_observation'}
    human = {**projection, 'basis': 'human_review', 'content': '我有点嫌弃他', 'media_sha256': ['before', 'after']}
    sidecar = media.with_suffix('.edit.json')
    save(sidecar, {'required_evidence': [projection, human]})
    roles = LiveRoles(tmp_path / 'candidates.json', tmp_path, tmp_path / 'config.yaml', tmp_path / 'ledger.sqlite')
    rendered = {'media': str(media), 'receipt': str(receipt)}
    roles.verify_manual_review_render(rendered, audit)
    save(sidecar, {'required_evidence': [projection, {**human, 'media_sha256': ['before', 'stale']}]})
    with pytest.raises(Review, match='hash-bound'):
        roles.verify_manual_review_render(rendered, audit)


def test_rank5_draft_receives_persisted_human_review(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from clipper.agent_flow import Pause
    from clipper.agent_live import LiveRoles

    ledger = tmp_path / 'ledger.sqlite'
    monkeypatch.setenv('KIYA_OPS_LEDGER', str(ledger))
    roles = LiveRoles(tmp_path / 'candidates.json', tmp_path / 'run', tmp_path / 'config.yaml', ledger)
    roles.contextual_gap = 'gap-1'
    review = tmp_path / 'gap-1-human.json'
    save(review, {'reviewer': 'user-confirmed'})
    state = SimpleNamespace(path=tmp_path / '05/state.json',
                            data={'manual_review': {'path': str(review), 'sha256': digest(review)}})
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr('clipper.agent_live.subprocess.run', run)
    with pytest.raises(Pause, match='autoedit pending'):
        roles.draft({'rerank': {'rank': 5}}, state, {}, {'rank': 5}, [], None)
    assert calls[0][-2:] == ['--manual-review-record', str(review)]


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


def test_screen_segments_keep_recording_gaps(tmp_path, monkeypatch):
    import subprocess
    from clipper.agent_evidence import collect, evidence_rows, normalize_segments
    from clipper.agent_flow import Review
    from clipper.danmaku import Segment
    from scripts import screen

    def media(name):
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                        'color=s=160x90:r=25', '-f', 'lavfi', '-i', 'anullsrc=r=44100',
                        '-t', '2', '-c:v', 'flv', '-c:a', 'libmp3lame', str(tmp_path / name)], check=True)

    media('first.flv')
    media('last.flv')
    (tmp_path / 'corrupt.flv').write_bytes(b'invalid recording')
    inputs = [Segment(Path(f'{name}.xml'), None, offset, 3) for name, offset in
              [('first', 0), ('absent', 4), ('corrupt', 7), ('last', 10)]]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(screen, 'plot', lambda *args: None)
    screen.write_outputs('fixed', inputs, [], {'delay': 0, 'delay_note': '', 'weights': {}},
                         tmp_path, {'value_recall': {'enabled': False}}, 'danmaku')
    path = tmp_path / 'candidates.json'
    raw = load(path)
    assert all(set(seg) == {'video', 'offset'} for seg in raw['segments'])
    segs = normalize_segments(raw['segments'], path.parent)
    assert [s['source_status'] for s in segs] == ['readable', 'missing_source', 'probe_failed', 'readable']
    assert segs[0]['duration'] == pytest.approx(2, abs=.1)
    assert segs[1]['duration'] is segs[2]['duration'] is None
    result = collect({'id': 'q', 'kind': 'setup', 'range': [1, 11], 'question': 'What happened?'},
                     [], segs, tmp_path / 'evidence')
    assert len(result['coverage']) == 2
    assert result['media_gaps'][0][0] == pytest.approx(2, abs=.1)
    assert result['media_gaps'][0][1] == 10
    assert [(f['range'], f['reason']) for f in result['failures']] == [
        ([4, 7], 'missing_source'), ([7, 10], 'probe_failed')]
    # Exercise the near-zero file-local seek that previously became 2.7e-05.
    near_boundary = collect({'id': 'boundary', 'kind': 'setup', 'range': [10.000027, 11.857],
                             'question': 'What happened?'}, [], segs, tmp_path / 'boundary')
    assert near_boundary['failures'] == []
    assert near_boundary['media_gaps'] == []
    assert len(near_boundary['coverage']) == 1
    with pytest.raises(Review, match='unavailable source'):
        evidence_rows([{'id': 'fake', 'start': 1, 'end': 11, 'content': 'test', 'modality': 'av'}], segs)
    assert load(path) == raw
    if os.environ.get('KIYA_INTEGRATION_OUT'):
        save(Path(os.environ['KIYA_INTEGRATION_OUT']).resolve() / 'screen-segment-regression.json',
             {'screen': raw, 'segments': segs, 'evidence': result})


def test_single_candidate_queue_reads_real_screen(tmp_path, monkeypatch):
    import subprocess
    from datetime import datetime, timezone

    sys.path.insert(0, str(WORKER.parent / 'deploy' / 'monitor'))
    import kiya_monitor as monitor

    artifact_root = Path(os.environ.get('KIYA_INTEGRATION_OUT') or tmp_path).resolve()
    now = datetime.now(timezone.utc).timestamp()
    root = artifact_root / 'single-candidate-queue' / str(int(now * 1000))
    rec = root / 'rec'
    rec.mkdir(parents=True)
    monkeypatch.setenv('PYTHONUTF8', '1')
    start = datetime.fromtimestamp(now - 900, timezone.utc).isoformat()
    xml = rec / 'fixture.xml'
    xml.write_text(f'<i><BililiveRecorderRecordInfo roomid="123" start_time="{start}"/>'
                   + ''.join(f'<d p="{t}">哈哈哈</d>' for t in [20] * 32 + [55] * 28 + [85]) + '</i>',
                   encoding='utf-8')
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'color=s=160x90:r=10',
                    '-f', 'lavfi', '-i', 'anullsrc=r=44100', '-t', '90', '-c:v', 'flv', '-c:a',
                    'libmp3lame', str(xml.with_suffix('.flv'))], check=True)
    # Job timestamps isolate each run even when preserving logs between runs.
    for _ in range(2):
        monitor.enqueue_operations({'KIYA_OPS_DIR': str(root)}, rec, '123', now - 900, now - 300)
    jobs = list((root / 'queue').glob('*.json'))
    assert len(jobs) == 1
    job = jobs[0]
    status = root / 'status' / job.name
    session = root / 'sessions' / job.stem / 'kiya' / ops_prepare.find_sessions([xml])[0][0]
    monkeypatch.setenv('KIYA_OPS_AGENT_V2', '1')
    monkeypatch.setenv('KIYA_OPS_AGENT_MAX_CANDIDATES', '1')
    monkeypatch.setenv('KIYA_OPS_LEDGER', str(root / 'daily.sqlite3'))
    monkeypatch.setenv('KIYA_ENV_FILE', str(tmp_path / 'nonexistent.env'))
    monkeypatch.delenv('KIYA_ASR', raising=False)
    real_step = ops_prepare.run_step
    calls = []

    def step(script, args, timeout, log):
        calls.append(script)
        if script == 'screen.py':
            return real_step(script, [*args, '--danmaku-only'], timeout, log)
        if script == 'rerank.py':
            assert args[-2:] == ['--ranks', 1]
            picked = load(session / 'candidates.json')['candidates'][0]
            t = picked['t']
            save(session / 'rerank.json', {'candidates': [{'rank': 1, 't': t,
                'llm': {'score': 8, 'type': '反差', 'what_happened': 'fixture'},
                'video_evidence': {'observations': [{'id': 'observed', 'start': t - 2, 'end': t - 1,
                    'description': 'fixture', 'modality': '音画'}], 'selected_gaps': [[t - 1, t]]}}]})
            raise RuntimeError('injected interruption after rerank checkpoint')
        assert script == 'agent_produce.py' and args[-2:] == ['--ranks', 1]
        return real_step(script, args, timeout, log)

    monkeypatch.setattr(ops_prepare, 'run_step', step)
    monkeypatch.setenv('QWEN_API_KEY', '')
    monkeypatch.setenv('DASHSCOPE_API_KEY', '')
    monkeypatch.setenv('KIYA_OPS_LLM_MAX_FEN', '')
    # Drain after the fixture's quiet period.
    ops_prepare.drain_one(root, now + 120)
    assert load(status)['status'] == 'retry'
    assert load(status)['production_ranks'] == [1]
    raw = load(session / 'candidates.json')
    assert raw['candidates'] and all(set(s) == {'video', 'offset'} for s in raw['segments'])
    ops_prepare.drain_one(root, now + 1021)
    state = load(status)
    assert state['status'] == 'partial' and state['unresolved_ranks'] == [1]
    assert not state['inventory'] and state['budget']['booked_fen'] == 0
    output = Path(state['production'])
    candidate = load(output / '01' / 'state.json')
    assert candidate['steps']['evidence-0']['result']['media_gaps'] == []
    assert 'evidence-0-answer' not in candidate['steps']
    assert [p.name for p in output.iterdir() if p.is_dir() and p.name.isdigit()] == ['01']
    assert calls == ['screen.py', 'rerank.py', 'agent_produce.py']


@pytest.mark.parametrize('limit', [1, 2])
def test_isolated_queue_retry_and_proposal(tmp_path, monkeypatch, limit):
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
    monkeypatch.setenv('KIYA_OPS_AGENT_MAX_CANDIDATES', str(limit))
    monkeypatch.setenv('KIYA_OPS_LEDGER', str(root / 'daily.sqlite3'))
    monkeypatch.delenv('KIYA_OPS_HISTORY_DIR', raising=False)
    executions = []

    def step(script, args, timeout, log):
        assert script == 'agent_produce.py' and '--live' in args
        assert args[args.index('--ranks') + 1:] == list(range(1, limit + 1))
        executions.append(script)
        output = args[args.index('--out') + 1]
        rank_one = {'rank': 1, 'status': 'inventoried', 'current_version': 'fixed',
                    'topic': {'event_id': 'fixed-event', 'topic': '温馨'}}
        if limit == 1:
            results = [{'rank': 1, 'status': 'paused'}] if len(executions) == 1 else [rank_one]
        else:
            results = [rank_one, {'rank': 2, 'status': 'paused' if len(executions) == 1 else 'needs_review'}]
        save(output / 'summary-selected.json', {'candidates': results})

    monkeypatch.setattr(ops_prepare, 'run_step', step)
    monkeypatch.setattr(ops_prepare, 'proposal_item', lambda row: {
        **row, 'decision': 'qualified', 'media': '720.mp4', 'pack': 'pack.json', 'quality': 'quality.json'})
    instant = datetime.fromtimestamp(now, timezone.utc)
    ops_prepare.drain_one(root, now)
    assert load(status)['unresolved_ranks'] == [limit]
    assert load(status)['deferred_ranks'] == ([2] if limit == 1 else [])
    ops_prepare.build_daily_plan(root, instant)
    plan_path = next((root / 'plans').glob('*.json'))
    first_plan = load(plan_path)
    assert len(first_plan['streamers']['kiya']['plan']['keep_in_inventory']) == limit - 1
    paused_status = load(status)
    monkeypatch.setenv('KIYA_OPS_AGENT_MAX_CANDIDATES', '3')
    ops_prepare.drain_one(root, now + 901)
    ops_prepare.drain_one(root, now + 1802)
    assert load(status)['status'] == 'prepared' and len(executions) == 2
    for _ in range(2):
        ops_prepare.build_daily_plan(root, instant)
        plan = load(next((root / 'plans').glob('*.json')))['streamers']['kiya']['plan']
        assert len(plan['keep_in_inventory']) == 1 and not plan['would_publish']
    if os.environ.get('KIYA_INTEGRATION_OUT'):
        save(Path(os.environ['KIYA_INTEGRATION_OUT']) / f'queue-rehearsal-limit-{limit}.json', {
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
