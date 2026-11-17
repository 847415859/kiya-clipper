import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clipper.edit import EditError, validate_edit
from clipper.timeline import compact_events, edit_cuts
from clipper.render_plan import plan
from clipper.subtitles import validate_style
from clipper.quality import _expected_window


def test_manual_and_automatic_cuts_form_union_and_share_quality_duration(tmp_path):
    edit = validate_edit({"window": {"start": 100, "end": 120}, "trim": {"start": 2, "end": 18},
                          "cuts": [{"start": 5, "end": 8}, {"start": 7, "end": 10}],
                          "items": [{"id": "before", "type": "text", "text": "范围外", "start": 0, "duration": 1},
                                    {"id": "after", "type": "zoom", "start": 11, "duration": 2}]}, 20)
    speech = [{"t0": 108, "t1": 112, "text": "谢谢大家的礼物"}]
    assert edit["cuts"] == [{"start": 5, "end": 10}]
    assert edit_cuts(edit, speech, 100, 102, 118) == [{"start": 105, "end": 112}]
    p = plan(edit, speech, 100, validate_style({}), {}, [])
    assert p["duration"] == 9
    assert [(q["id"], q["start"], q["duration"]) for q in p["items"]] == [("after", 3, 1)]
    (tmp_path / "transcript.json").write_text(json.dumps({"sentences": speech}), encoding="utf-8")
    assert _expected_window(tmp_path, 1, None, edit) == (102, 118, 9)
    edit["remove_business"] = False
    assert plan(edit, speech, 100, validate_style({}), {}, [])["duration"] == 11


@pytest.mark.parametrize("cuts", [[{"start": 0, "end": 20}], [{"start": 4, "end": 3}],
                                     [{"start": -1, "end": 2}], [{"start": 1, "end": math.nan}],
                                     [{"start": True, "end": 2}], "wrong", [None]])
def test_invalid_or_emptying_cuts_rejected(cuts):
    with pytest.raises(EditError):
        validate_edit({"cuts": cuts}, 20)


def test_crossing_caption_keeps_words_and_manual_caption_without_words():
    removed = [{"start": 102, "end": 104}]
    events = [{"t0": 1, "t1": 5, "text": "甲乙丙", "words": [[1, 2, "甲"], [2, 4, "乙"], [4, 5, "丙"]]},
              {"t0": 6, "t1": 8, "text": "手动修订", "manual": True, "words": []},
              {"t0": 1, "t1": 5, "text": "跨接缝的修订", "manual": True, "words": []},
              {"t0": 2, "t1": 4, "text": "整句删除", "words": []}]
    result = compact_events(events, 100, removed)
    assert result[0]["text"] == "甲丙"
    assert result[0]["words"] == [[1, 2, "甲"], [2, 3, "丙"]]
    assert result[1] == {"t0": 4, "t1": 6, "text": "手动修订", "manual": True, "words": []}
    assert result[2]["text"] == "跨接缝的修订" and result[2]["cut_adjusted"]
    assert len(result) == 3


def test_preview_plan_cli_applies_current_overrides_and_source_times(tmp_path):
    cand = tmp_path / "candidates.json"
    cand.write_text(json.dumps({"candidates": [{"rank": 1, "clip_start": 100, "clip_end": 120}]}), encoding="utf-8")
    (tmp_path / "transcript.json").write_text(json.dumps({"sentences": [{"t0": 108, "t1": 110, "text": "原文"}]}), encoding="utf-8")
    edit = tmp_path / "edit.json"
    edit.write_text(json.dumps({"cuts": [{"start": 3, "end": 5}], "subtitles": {"enabled": True,
        "sentence_overrides": [{"t0": 108, "t1": 110, "original": "原文", "text": "校对字幕"}]}}), encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "scripts/edit_plan.py"), str(cand), "--rank", "1", "--edit", str(edit)], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    p = json.loads(r.stdout.splitlines()[-1])
    assert p["duration"] == 18
    assert p["subtitles"]["events"][0]["text"] == "校对字幕"
    assert p["subtitles"]["events"][0]["t0"] == 6
