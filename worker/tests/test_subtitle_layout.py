"""Evidence-bound sentence layout and timed subdivision reach actual ASS output."""
import copy

import pytest

from clipper.speech_edit import apply_overrides, validate_overrides
from clipper.subtitles import build_events, to_ass, to_srt, validate_style


SENTENCE = {"t0": 100, "t1": 104, "text": "原文句子", "words": [[100, 104, "原文句子"]]}
ROW = {"t0": 100, "t1": 104, "original": "原文句子", "text": "原文句子"}
PARTS = [{"text": "原文", "t0": 100, "t1": 100.3}, {"text": "句子", "t0": 101, "t1": 104}]


def render(row, **style):
    st = validate_style({"strip_punct": False, **style})
    events = build_events([SENTENCE], 99, 106, st, {"原文": "替换"}, [row])
    return events, to_ass(events, st, width=1080, height=1920)


def test_layout_and_parts_survive_validation_application_and_ass_box_layers():
    row = dict(ROW, display_x=0.25, display_y=0.2, parts=PARTS)
    before = copy.deepcopy(SENTENCE)
    validated = validate_overrides([row])
    updated, unmatched = apply_overrides([SENTENCE], validated)
    assert not unmatched and updated[0]["parts"] == PARTS
    assert updated[0]["words"] == [] and SENTENCE == before
    events, ass = render(row, box=True, karaoke=True, min_duration=3)
    assert [e["t0"] for e in events] == pytest.approx([1, 2])
    assert [e["t1"] for e in events] == pytest.approx([1.3, 5])
    assert [e["text"] for e in events] == ["原文", "句子"]
    assert all(e["words"] == [] and e["manual"] for e in events)
    # Portrait PlayResX = 608; every box and text layer gets the same anchor.
    assert ass.count("{\\pos(152,216)}") == 4
    assert "1,0:00:01.00,0:00:01.30,Default" in ass
    assert "1,0:00:02.00,0:00:05.00,Default" in ass
    assert "\\kf" not in ass and "替换" not in ass
    srt = to_srt(events)
    assert "00:00:01,000 --> 00:00:01,300\n原文" in srt
    assert "pos(" not in srt


@pytest.mark.parametrize("axis,value", [("display_x", True), ("display_x", 0.01),
    ("display_y", 0.96), ("display_y", float("nan")), ("display_x", "0.5")])
def test_invalid_coordinates(axis, value):
    with pytest.raises(ValueError):
        validate_overrides([dict(ROW, **{axis: value})])


@pytest.mark.parametrize("parts", [[], [PARTS[0]],
    [{"text": "原文", "t0": 100, "t1": 100.2}, PARTS[1]],
    [{"text": "原文", "t0": 99.9, "t1": 100.3}, PARTS[1]],
    [PARTS[0], {"text": "句子", "t0": 100.2, "t1": 104}],
    [PARTS[0], {"text": "新词", "t0": 101, "t1": 104}],
    [PARTS[0], {"text": "句子", "t0": 101, "t1": 104.1}],
    [PARTS[0], {"text": "句子", "t0": True, "t1": 104}],
    [PARTS[0], {"text": "句子", "t0": 101, "t1": float("inf")}],
    [PARTS[0], {"text": "", "t0": 101, "t1": 104}]])
def test_invalid_parts(parts):
    with pytest.raises(ValueError):
        validate_overrides([dict(ROW, parts=parts)])


def test_parts_are_bound_to_explicit_display_interval_and_current_text():
    parts = [{"text": "修订", "t0": 99.8, "t1": 100.1}, {"text": "文字", "t0": 103, "t1": 104.2}]
    row = dict(ROW, text="修订文字", display_t0=99.8, display_t1=104.2, parts=parts)
    assert validate_overrides([row])[0]["parts"] == parts
    events, ass = render(row)
    assert events[0]["t0"] == pytest.approx(.8)
    assert events[-1]["t1"] == pytest.approx(5.2)
    assert "修订" in ass and "文字" in ass
    with pytest.raises(ValueError):
        validate_overrides([dict(row, display_t0=100)])


def test_position_only_uses_style_anchor_for_missing_axis_and_old_rows_stay_compatible():
    _, ass = render(dict(ROW, display_x=.5), position="top", margin_v=80)
    assert "{\\pos(304,80)}" in ass
    _, ass = render(dict(ROW, display_y=.5))
    assert "{\\pos(304,540)}" in ass
    events, ass = render(ROW)
    assert "\\pos" not in ass and events[0]["text"] == ROW["text"]
    assert validate_overrides([ROW]) == [ROW]


def test_clip_intersection_clamps_parts_and_stale_binding_has_no_layout():
    row = dict(ROW, parts=PARTS, display_y=.25)
    st = validate_style({})
    events = build_events([SENTENCE], 100.1, 102, st, sentence_overrides=[row])
    assert events[0]["t0"] == 0
    assert events[0]["t1"] == pytest.approx(.2)
    assert events[1]["t1"] == pytest.approx(1.9)
    stale = dict(row, original="stale")
    events = build_events([SENTENCE], 99, 106, st, sentence_overrides=[stale])
    assert len(events) == 1 and "display_y" not in events[0]
