import pytest

from clipper.agent_flow import Review
from clipper.agent_production import repair


def base_edit():
    return {'window': {'start': 0, 'end': 20}, 'trim': {'start': 0, 'end': 20},
            'items': [{'id': 'ov1', 'type': 'text', 'start': 2, 'duration': 3, 'text': 'title'},
                      {'id': 'z1', 'type': 'zoom', 'start': 4, 'duration': 2, 'scale': 1.5, 'x': .5, 'y': .5}],
            'cuts': []}


MAPPING = [{'source_start': 0, 'source_end': 20, 'output_start': 0, 'output_end': 20}]


def decision(action, **fields):
    return {'action': action, 'output_range': [1, 7], 'reason': 'fixture finding', **fields}


def test_overlay_zoom_boundary_and_rhythm_repairs_are_targeted():
    edit = base_edit()
    overlay = repair(edit, decision('adjust_overlay', item_id='ov1', changes={'y': .7}), [], MAPPING)
    zoom = repair(edit, decision('adjust_zoom', item_id='z1', changes={'scale': 1.2}), [], MAPPING)
    boundary = repair(edit, decision('adjust_boundary', output_range=[0, 20], trim={'start': 1, 'end': 19}), [], MAPPING, {'sentences': []})
    rhythm = repair(edit, decision('adjust_rhythm', output_range=[8, 9], cuts=[{'start': 8, 'end': 9}]), [], MAPPING, {'sentences': []})
    assert overlay['items'][0]['y'] == .7
    assert zoom['items'][1]['scale'] == 1.2
    assert boundary['trim'] == {'start': 1, 'end': 19}
    assert rhythm['cuts'] == [{'start': 8, 'end': 9}]
    assert 'y' not in edit['items'][0] and edit['items'][1]['scale'] == 1.5


@pytest.mark.parametrize('action, fields', [
    ('adjust_overlay', {'item_id': 'missing', 'changes': {'y': .8}}),
    ('adjust_zoom', {'item_id': 'ov1', 'changes': {'scale': 1.2}}),
    ('adjust_boundary', {'trim': {'start': -1, 'end': 19}}),
    ('adjust_rhythm', {'cuts': [{'start': 1, 'end': 5}]}),
])
def test_repairs_reject_invalid_or_unbound_targets(action, fields):
    with pytest.raises(Review):
        repair(base_edit(), decision(action, **fields), [], MAPPING)


def test_repair_rejects_missing_output_location():
    with pytest.raises(Review, match='output_range'):
        repair(base_edit(), {'action': 'adjust_zoom', 'item_id': 'z1', 'changes': {'scale': 1.2},
                             'reason': 'fixture finding'}, [], MAPPING)


@pytest.mark.parametrize('value', [True, float('nan'), float('inf'), '1'])
def test_effect_numeric_types_are_strict(value):
    with pytest.raises(Review):
        repair(base_edit(), decision('adjust_zoom', item_id='z1', changes={'scale': value}), [], MAPPING)


def test_all_required_evidence_uses_absolute_source_time():
    edit = base_edit()
    edit['window'] = {'start': 100, 'end': 120}
    edit['required_evidence'] = [{'id': 'one', 'start': 102, 'end': 103}, {'id': 'two', 'start': 118, 'end': 120}]
    mapping = [dict(MAPPING[0], source_start=100, source_end=120)]
    with pytest.raises(Review, match='required evidence'):
        repair(edit, decision('adjust_boundary', output_range=[0, 20], trim={'start': 1, 'end': 19}), [], mapping, {'sentences': []})
    with pytest.raises(Review, match='required evidence'):
        repair(edit, decision('adjust_rhythm', output_range=[18, 20], cuts=[{'start': 18, 'end': 19}]), [], mapping, {'sentences': []})


def test_timeline_requires_transcript_and_exact_reviewed_location():
    with pytest.raises(Review, match='location'):
        repair(base_edit(), decision('adjust_rhythm', cuts=[{'start': 8, 'end': 9}]), [], MAPPING, {'sentences': []})
    with pytest.raises(Review, match='boundaries unavailable'):
        repair(base_edit(), decision('adjust_rhythm', cuts=[{'start': 3, 'end': 4}]), [], MAPPING)
    with pytest.raises(Review, match='speech'):
        repair(base_edit(), decision('adjust_rhythm', cuts=[{'start': 3, 'end': 4}]), [], MAPPING, {'sentences': [{'t0': 2, 't1': 5}]})
    with pytest.raises(Review, match='sentence'):
        repair(base_edit(), decision('adjust_boundary', output_range=[0, 20], trim={'start': 1, 'end': 19}), [], MAPPING, {'sentences': [{'t0': 0, 't1': 2}]})


def test_multi_action_is_atomic_and_conflicts_do_not_mutate_original():
    edit = base_edit()
    actions = [{'action': 'adjust_overlay', 'item_id': 'ov1', 'changes': {'y': .7}}, {'action': 'remove_zoom', 'item_id': 'z1'}]
    result = repair(edit, decision(None, actions=actions), [], MAPPING)
    assert len(result['items']) == 1 and result['items'][0]['y'] == .7
    assert len(edit['items']) == 2 and 'y' not in edit['items'][0]
    with pytest.raises(Review, match='same object'):
        repair(edit, decision(None, actions=[actions[0], dict(actions[0], changes={'y': .8})]), [], MAPPING)
    with pytest.raises(Review):
        repair(edit, decision(None, actions=[actions[0], dict(actions[1], item_id='missing')]), [], MAPPING)
    assert 'y' not in edit['items'][0]


def test_subtitle_correction_and_timing_remain_original_bound():
    edit = base_edit()
    transcript = {'sentences': [{'t0': 3, 't1': 5, 'text': 'wrong'}]}
    sub = {'t0': 3, 't1': 5, 'original': 'wrong', 'text': 'right'}
    corrected = repair(edit, decision('correct_subtitle', subtitle=sub), [], MAPPING, transcript)
    timed = dict(sub, display_t0=2.8, display_t1=5.2)
    result = repair(corrected, decision('adjust_subtitle_timing', subtitle=timed), [], MAPPING, transcript)
    assert result['subtitles']['sentence_overrides'][0]['t0'] == 3
    assert result['subtitles']['sentence_overrides'][0]['display_t0'] == 2.8
    with pytest.raises(Review, match='original transcript'):
        repair(edit, decision('correct_subtitle', subtitle=dict(sub, t0=3.001)), [], MAPPING, transcript)


def test_zoom_remove_does_not_remove_overlay():
    with pytest.raises(Review, match='type'):
        repair(base_edit(), decision('remove_zoom', item_id='ov1'), [], MAPPING)
    result = repair(base_edit(), decision('remove_zoom', item_id='z1'), [], MAPPING)
    assert [r['id'] for r in result['items']] == ['ov1']



def production_fixture(tmp_path, monkeypatch):
    from clipper import agent_production as production
    from clipper.agent_flow import save, digest
    from scripts.agent_produce import RecordedRoles
    source = tmp_path / 'input' / 'candidates.json'
    video = tmp_path / 'video.mp4'
    video.write_bytes(b'original')
    config = tmp_path / 'config.yaml'
    config.write_text('fixture')
    save(source, {'segments': [{'video': str(video), 'offset': 0, 'duration': 20}], 'candidates': [{'rank': 1, 'clip_start': 0, 'clip_end': 20}]})
    roles_path = tmp_path / 'roles.json'
    row = {'topic': {'verdict': 'worth', 'reason': 'fixture', 'evidence_ids': ['e']}, 'evidence': [{'id': 'e', 'start': 0, 'end': 20, 'modality': 'av', 'content': 'fixture'}], 'draft': dict(base_edit(), required_evidence=[{'id': 'e', 'start': 0, 'end': 20}], subtitles={'enabled': False}), 'pack': {'title': 'fixture'}, 'reviews': [{'verdict': 'pass', 'reason': 'fixture'}]}
    save(roles_path, {'simulation': True, 'ranks': {'1': row}})
    def fake_render(target, data, candidate, edit, transcript, config, minimum):
        media = target / 'final' / '01.mp4'
        media.parent.mkdir(parents=True)
        media.write_bytes(str(edit).encode())
        normalized = media.with_suffix('.edit.json')
        save(normalized, edit)
        receipt = media.with_suffix('.evidence.json')
        save(receipt, {'callback_offset': 0, 'source_to_output': MAPPING})
        sha = digest(media)
        save(media.with_suffix('.audio.json'), {'after_recovery': {'status': 'diagnosed', 'earliest_stage': 'none', 'sources': [{'status': 'available', 'gaps': []}], 'stages': {'cut': {'status': 'available', 'gaps': []}, 'final': {'status': 'available', 'gaps': [], 'sha256': sha}}}})
        return {'media': str(media), 'receipt': str(receipt), 'files': {str(p): digest(p) for p in (media, normalized, receipt)}, 'probe': {'duration': 20}}
    monkeypatch.setattr('clipper.agent_evidence.probe', lambda _: {'height': 720, 'duration': 20})
    monkeypatch.setattr(production, 'render', fake_render)
    return production.Production(source, tmp_path / 'run', RecordedRoles(roles_path), config, minimum=0), row, roles_path


def test_failed_repair_preserves_qualified_inventory_and_working_version(tmp_path, monkeypatch):
    from clipper.agent_flow import load, save
    from scripts.agent_produce import RecordedRoles
    runner, row, roles_path = production_fixture(tmp_path, monkeypatch)
    first = runner.run()[0]
    assert first['status'] == 'inventoried'
    inventory = runner.output / '01' / 'inventory.json'
    accepted = inventory.read_bytes()
    row['draft']['items'][0]['text'] = 'new working draft'
    row['reviews'] = [{'verdict': 'repair', 'reason': 'uncertain', 'action': 'remove_overlay', 'output_range': [15, 16], 'item_id': 'ov1'}]
    save(roles_path, {'simulation': True, 'ranks': {'1': row}})
    runner.roles = RecordedRoles(roles_path)
    second = runner.run()[0]
    assert second['status'] == 'needs_review'
    assert second['working_version'] != second['qualified_version']
    assert second['qualified_version'] == first['qualified_version']
    assert inventory.read_bytes() == accepted


def test_unbound_nonfixture_decision_never_passes(tmp_path, monkeypatch):
    runner, row, _ = production_fixture(tmp_path, monkeypatch)
    class Unbound:
        name = 'live-test'
        def for_rank(self, rank):
            return row
        def review(self, row, index, rendered):
            return {'verdict': 'pass', 'reason': 'missing hash and revision'}
    runner.roles = Unbound()
    result = runner.run()[0]
    assert result['status'] == 'needs_review'
    assert not (runner.output / '01' / 'inventory.json').exists()



@pytest.mark.parametrize('field', ['media_sha256', 'edit_revision'])
def test_stale_review_binding_never_passes(tmp_path, monkeypatch, field):
    from scripts.agent_produce import RecordedRoles
    runner, row, _ = production_fixture(tmp_path, monkeypatch)
    original = runner.roles.review
    def review(row, index, rendered):
        decision = original(row, index, rendered)
        decision[field] = 'stale'
        return decision
    monkeypatch.setattr(runner.roles, 'review', review)
    assert runner.run()[0]['status'] == 'needs_review'
    assert not (runner.output / '01' / 'inventory.json').exists()


def test_audio_sidecar_hash_must_bind_exact_render(tmp_path, monkeypatch):
    from clipper import agent_production
    from clipper.agent_flow import load, save
    runner, row, _ = production_fixture(tmp_path, monkeypatch)
    original = agent_production.render
    def render(*args):
        result = original(*args)
        path = __import__('pathlib').Path(result['media']).with_suffix('.audio.json')
        audio = load(path)
        audio['after_recovery']['stages']['final']['sha256'] = 'wrong media'
        save(path, audio)
        return result
    monkeypatch.setattr(agent_production, 'render', render)
    result = runner.run()[0]
    assert result['status'] == 'needs_review'
    assert not (runner.output / '01' / 'inventory.json').exists()



def test_subtitle_layout_and_split_preserve_original_binding_and_current_text():
    edit = base_edit()
    transcript = {'sentences': [{'t0': 3, 't1': 5, 'text': 'hello world'}]}
    sub = {'t0': 3, 't1': 5, 'original': 'hello world', 'text': 'hello world', 'display_y': .6}
    moved = repair(edit, decision('adjust_subtitle_layout', subtitle=sub), [], MAPPING, transcript)
    assert moved['subtitles']['sentence_overrides'][0]['display_y'] == .6
    parts = [{'text': 'hello ', 't0': 3, 't1': 4}, {'text': 'world', 't0': 4, 't1': 5}]
    split = repair(moved, decision('split_subtitle', subtitle=dict(sub, parts=parts)), [], MAPPING, transcript)
    assert split['subtitles']['sentence_overrides'][0]['parts'] == parts
    with pytest.raises(Review):
        repair(edit, decision('split_subtitle', subtitle=dict(sub, parts=[dict(parts[0], text='new '), parts[1]])), [], MAPPING, transcript)
    with pytest.raises(Review):
        repair(edit, decision('adjust_subtitle_layout', subtitle=dict(sub, display_y=1)), [], MAPPING, transcript)
    with pytest.raises(Review):
        repair(edit, decision('split_subtitle', subtitle=dict(sub, parts=[parts[0], dict(parts[1], t0=3.9)])), [], MAPPING, transcript)
