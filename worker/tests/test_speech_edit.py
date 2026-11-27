import copy
import json
import sys
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from clipper.edit import EditError, validate_edit
from clipper.editdiff import diff_edit
from clipper.speech_edit import apply_overrides, validate_overrides
from clipper.subtitles import build_events, to_ass, to_srt, validate_style
from render import subtitle_events

SENTENCE = {"t0": 100, "t1": 104, "text": "ASR句子", "words": [[100, 102, "原识别"], [102, 104, "文字"]]}
ROW = {"t0": 100, "t1": 104, "original": "原识别文字", "text": "修订后的句子"}


def test_sentence_override_roundtrip_is_clip_local_and_keeps_audio_timing():
    edit = validate_edit({"subtitles": {"enabled": True, "sentence_overrides": [ROW]}, "trim": {"start": 0, "end": 20}}, 20)
    assert edit["subtitles"]["sentence_overrides"] == [ROW]
    before = copy.deepcopy(SENTENCE)
    corrected, stale = apply_overrides([SENTENCE], [ROW])
    assert not stale and SENTENCE == before
    assert corrected[0]["t0"] == 100 and corrected[0]["t1"] == 104 and corrected[0]["words"] == []
    assert diff_edit({"trim": {"start": 0, "end": 20}}, edit)["subtitle_changed"]


@pytest.mark.parametrize("change", [{"original": "另一句"}, {"t0": 100.1}, {"t1": 105}])
def test_changed_transcript_does_not_receive_stale_corrections(change):
    row = dict(ROW, **change)
    corrected, stale = apply_overrides([SENTENCE], [row])
    assert corrected == [SENTENCE] and stale == [row]


@pytest.mark.parametrize("rows", [[dict(ROW, text="")], [dict(ROW, original="")], [dict(ROW, t0=True)],
    [dict(ROW, t1=float('nan'))], [dict(ROW, t1=99)], [ROW, ROW], {}, [dict(ROW, text="长" * 1001)]])
def test_invalid_overrides_are_rejected(rows):
    with pytest.raises(EditError):
        validate_edit({"subtitles": {"sentence_overrides": rows}}, 20)


def test_render_exports_manual_words_without_global_replacement_or_fake_karaoke():
    style = validate_style({"karaoke": True})
    ev = subtitle_events([SENTENCE], 99, 106, style, {"修订": "不该覆盖"}, [ROW])
    assert ev[0]["text"] == ROW["text"] and ev[0]["words"] == [] and ev[0]["manual"]
    assert ev[0]["t0"] == 1 and ev[0]["t1"] == 5
    ass = to_ass(build_events([SENTENCE], 99, 106, style, {"修订": "不该覆盖"}, [ROW]), style, corrections={"修订": "不该覆盖"})
    assert ROW["text"] in ass and "\\kf" not in ass
    assert ROW["text"] in to_srt(ev)


def test_same_words_at_other_times_and_other_clips_are_unaffected():
    other = dict(SENTENCE, t0=110, t1=114, words=[[110, 114, "原识别文字"]])
    ev = subtitle_events([SENTENCE, other], 99, 116, validate_style({}), {}, [ROW])
    assert [e["text"] for e in ev] == [ROW["text"], "原识别文字"]
    assert subtitle_events([SENTENCE], 99, 106, validate_style({}), {})[0]["text"] == "原识别文字"


def test_save_validation_preserves_overrides_and_reports_stale_transcript(tmp_path):
    edits = tmp_path / "edits"
    edits.mkdir()
    file = edits / "01.json"
    file.write_text(json.dumps({"subtitles": {"sentence_overrides": [ROW]}}), encoding="utf-8")
    transcript = tmp_path / "transcript.json"
    transcript.write_text(json.dumps({"sentences": [SENTENCE]}), encoding="utf-8")
    def check():
        result = subprocess.run([sys.executable, str(ROOT / "scripts/edit_check.py"), str(file), "--clip-len", "20"],
                                capture_output=True, encoding="utf-8")
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    saved = check()
    assert saved["edit"]["subtitles"]["sentence_overrides"] == [ROW] and not saved["warnings"]
    transcript.write_text('{"sentences": []}', encoding="utf-8")
    assert any("无法匹配" in warning for warning in check()["warnings"])



def test_display_timing_keeps_original_binding_and_changes_render_event():
    row = dict(ROW, display_t0=99.8, display_t1=104.2)
    validated = validate_overrides([row])
    assert validated == [row]
    corrected, stale = apply_overrides([SENTENCE], validated)
    assert not stale and corrected[0]['t0'] == 99.8 and corrected[0]['t1'] == 104.2
    assert SENTENCE['t0'] == 100
    events = subtitle_events([SENTENCE], 99, 106, validate_style({}), {}, validated)
    assert events[0]['t0'] == pytest.approx(.8)
    assert events[0]['t1'] == pytest.approx(5.2)
    changed = dict(SENTENCE, t0=100.001)
    result, stale = apply_overrides([changed], validated)
    assert result == [changed] and stale == validated


@pytest.mark.parametrize('changes', [{'display_t0': 100}, {'display_t0': True, 'display_t1': 105}, {'display_t0': 105, 'display_t1': 100}])
def test_invalid_display_timing_rejected(changes):
    with pytest.raises(ValueError):
        validate_overrides([dict(ROW, **changes)])
