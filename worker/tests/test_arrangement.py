import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clipper.arrangement import inspect_plan, narrative_handoff, speech_events
from clipper.protection import coverage, required
from clipper.timeline import edit_cuts


def test_emotional_pause_overrides_automatic_business_cut():
    pause = {'id': 'pause', 'start': 12.5, 'end': 13, 'role': 'reaction_pause'}
    edit = {'required_evidence': [pause], 'remove_business': True}
    sentences = [{'t0': 12, 't1': 14, 'text': '谢谢大家的礼物'}]
    assert edit_cuts(edit, sentences, 10, 10, 16) == []
    assert coverage(required(edit), [(10, 16)])['status'] == 'time_covered'


def test_original_change_precedes_explanation_after_a_cut():
    edit = {'narrative_plan': narrative_handoff({'punch_time': 4, 'story': {'punch': '原话'}}, 10)}
    kept = [(10, 12), (13, 17)]
    overlay = {'type': 'text', 'purpose': 'reveal', 'start': 2.5, 'duration': 1}
    result, = inspect_plan(edit, [overlay], {}, kept, 30)
    assert result['code'] == 'early_reveal'
    assert result['source_ranges'] == [[13.5, 14.5]]
    assert inspect_plan(edit, [dict(overlay, start=3.1)], {}, kept, 30) == []


def test_quiet_response_ducks_without_subtitles():
    edit = {'subtitles': {'enabled': False}, 'required_evidence': [
        {'id': 'response', 'start': 13.5, 'end': 14, 'modality': '原声'},
        {'id': 'result', 'start': 14, 'end': 15, 'modality': '画面'},
    ]}
    assert speech_events(edit, [], [(10, 12), (13, 17)]) == [{'t0': 2.5, 't1': 3}]


def test_reading_time_and_overlap_are_independent_review_hints():
    caption = {'text': '最终结果还需要仔细确认', 't0': 1, 't1': 1.6}
    subtitles = {'enabled': True, 'events': [caption]}
    overlay = {'type': 'text', 'y': 0.88, 'start': 1, 'duration': 1}
    findings = inspect_plan({}, [overlay], subtitles, [(20, 25)], 30)
    assert {row['code'] for row in findings} == {'caption_reading_review', 'subtitle_overlay_review'}
    assert all(row['status'] == 'review_hint' for row in findings)
    subtitles['events'] = [dict(caption, t1=3)]
    assert inspect_plan({}, [dict(overlay, y=0.15)], subtitles, [(20, 25)], 30) == []
