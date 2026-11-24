import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clipper.protection import coverage, protect_automatic, required, resolve
from clipper.edit import validate_edit
from clipper.timeline import edit_cuts


def test_automatic_business_preserves_response_but_manual_cut_is_reported(monkeypatch):
    monkeypatch.setattr('clipper.timeline.business_cuts', lambda *_: [{'start': 12, 'end': 18}])
    edit = {'required_evidence': [{'id': 'AV1', 'start': 14, 'end': 17}], 'cuts': []}
    assert edit_cuts(edit, [], 0, 0, 30) == []
    edit['cuts'] = [{'start': 15, 'end': 16}]
    removed = edit_cuts(edit, [], 0, 0, 30)
    assert removed == [{'start': 15, 'end': 16}]
    report = coverage(required(edit), [(0, 15), (16, 30)])
    assert report['checks'][0]['missing_source_ranges'] == [[15, 16]]
    assert report['checks'][0]['output_ranges'][1]['output_start'] == 15


def test_old_edit_and_legacy_reaction_compatibility():
    base = {'trim': {'start': 0, 'end': 20}, 'items': [], 'subtitles': {'enabled': False}}
    assert required(validate_edit(base, 20)) == []
    base.update(window={'start': 100, 'end': 120}, required_reaction={'start': 110, 'end': 115})
    edit = validate_edit(base, 20)
    assert required(edit)[0]['start'] == 110
    base['required_evidence'] = [{'id': 'AV2', 'start': 118, 'end': 125}]
    assert validate_edit(base, 20)['required_evidence'][0]['end'] == 125


def test_resolve_keeps_revision_and_unknown_ids():
    rows, trace = resolve({'video_evidence': {'must_keep_ids': ['AV1', 'AV2'], 'observations': [{'id': 'AV1', 'start': 1, 'end': 3}]}}, {'rank': 2, 'clip_start': 0}, 's')
    assert trace['issues'] == [{'id': 'AV2', 'status': 'unknown_id'}]
    assert rows[0]['evidence_revision'] == trace['evidence_revision']
    assert rows[0]['candidate']['session'] == 's'


def test_actual_source_gap_is_not_flattened(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    import cut
    a, b = tmp_path/'a.mp4', tmp_path/'b.mp4'
    a.touch(); b.touch()
    monkeypatch.setattr(cut, 'probe_duration', lambda _: 5)
    with pytest.raises(ValueError, match='5.000–10.000'):
        cut.pieces([{'video':str(a),'offset':0},{'video':str(b),'offset':10}], 0, 15)


def test_stale_candidate_evidence_not_relabelled():
    rows, trace = resolve({'clip_start':10,'clip_end':20,'video_evidence':{'must_keep_ids':['AV1']}}, {'rank':1,'clip_start':30,'clip_end':40}, 's')
    assert rows == [] and trace['issues'][0]['status'] == 'stale_candidate_source'


def test_optional_feedback_validated_and_preserved():
    edit = {'trim':{'start':0,'end':10},'items':[], 'review_feedback':{'reasons':['response'],'note':'留到OK'}, 'adopted_ai_draft':'a'*32}
    assert validate_edit(edit,10)['review_feedback']['reasons'] == ['response']
    edit['review_feedback']['reasons'] = ['invented']
    from clipper.edit import EditError
    with pytest.raises(EditError):
        validate_edit(edit,10)


def test_new_evidence_reaches_old_edit_without_overwriting_saved_rows(tmp_path):
    import json
    from clipper.evidence_edit import attach
    candidate = {'rank':1, 'clip_start':10, 'clip_end':30}
    (tmp_path/'rerank.json').write_text(json.dumps({'candidates':[dict(candidate, video_evidence={'must_keep_ids':['AV1'],'observations':[{'id':'AV1','start':12,'end':15}]})]}),encoding='utf-8')
    result = attach({'required_evidence':[{'id':'MANUAL','start':20,'end':22}]},candidate,tmp_path)
    assert [r['id'] for r in result['required_evidence']] == ['MANUAL','AV1']
    assert attach(result,candidate,tmp_path)['required_evidence'] == result['required_evidence']


def test_quality_handles_bound_receipt_with_actual_audio(tmp_path, monkeypatch):
    import json
    from clipper import quality
    media = tmp_path/'test.mp4'
    media.write_bytes(b'fixture')
    receipt = coverage([{'id':'A','start':0,'end':3,'modality':'audio'}],[(0,3)])
    receipt.update(media_size=media.stat().st_size,media_mtime_ns=media.stat().st_mtime_ns)
    media.with_suffix('.evidence.json').write_text(json.dumps(receipt),encoding='utf-8')
    monkeypatch.setattr(quality,'_media_checks',lambda *a,**k:{'duration':3,'audio':[]})
    monkeypatch.setattr(quality,'_black_frames',lambda *a,**k:None)
    candidate = {'rank':1,'clip_start':0,'clip_end':3}
    edit = {'trim':{'start':0,'end':3},'subtitles':{'enabled':False},'items':[]}
    result=quality.quality_report(tmp_path,1,final=media,candidate=candidate,edit=edit)
    assert any(e.code=='required_audio_missing' for e in result.errors)


def test_invalid_required_does_not_pass_validation():
    with pytest.raises(ValueError):
        required({'required_evidence': [{'id': 'x', 'start': float('nan'), 'end': 2}]})
