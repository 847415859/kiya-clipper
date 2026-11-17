import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clipper.edit import density_problems, ensure_caption, finalize_caption, validate_edit, validate_item
from clipper.quality import QualityReport, _subtitle_check


def text(content, start, duration=3):
    return validate_item({"type": "text", "text": content, "start": start, "duration": duration}, 30)


@pytest.mark.parametrize("existing", [text("她输了赌约", 0), text("她输了别的东西", 5), text("她输了赌约", 9), text("她输了赌约", 5, 0.2)])
def test_hidden_partial_late_or_flashing_context_does_not_satisfy_opening(existing):
    items = ensure_caption([existing], "她输了赌约", {"start": 5, "end": 25})
    assert len(items) == 2 and items[0]["start"] == 5.2


def test_required_context_survives_busy_opening_and_item_limit():
    edit = {"trim": {"start": 5, "end": 25}, "items": [
        text("热闹花字", 5), validate_item({"type": "zoom", "start": 5, "duration": 4}, 30), text("另一条", 10)]}
    result, dropped = finalize_caption(edit, "她输了赌约", 2)
    assert any(i.get("text") == "她输了赌约" for i in result["items"])
    assert len(result["items"]) <= 2 and dropped and not density_problems(result)
    assert validate_edit(result, 30)["items"] == result["items"]
    again, _ = finalize_caption(result, "她输了赌约", 2)
    assert again == result


def test_short_trim_caption_stays_within_export():
    result, _ = finalize_caption({"trim": {"start": 5, "end": 6}, "items": []}, "补前情", 1)
    cap = result["items"][0]
    assert cap["start"] + cap["duration"] == pytest.approx(6)


def subtitle_report(tmp_path, events, font_size=64):
    job = tmp_path / "render" / "job_01"
    job.mkdir(parents=True, exist_ok=True)
    (job / "props.json").write_text(json.dumps({"width": 1280, "height": 720,
        "subtitles": {"enabled": True, "events": events, "style": {"font_size": font_size}}}), encoding="utf-8")
    report = QualityReport(rank=1)
    _subtitle_check(report, tmp_path, 1, {"subtitles": {"enabled": True}}, 30)
    return report


def test_empty_render_subtitles_are_reported(tmp_path):
    report = subtitle_report(tmp_path, [{"t0": 1, "t1": 3, "text": "  "}])
    assert "subtitle_empty" in [x.code for x in report.warnings]


def test_fast_subtitles_include_seekable_examples(tmp_path):
    report = subtitle_report(tmp_path, [{"t0": 3, "t1": 3.4, "text": "这一句根本来不及读完"}])
    issue = next(x for x in report.warnings if x.code == "subtitle_reading_speed")
    assert issue.details["examples"][0]["t0"] == 3
    assert report.checks["subtitle_readability"]["heuristic"] is True


def test_phone_font_and_comfortable_subtitle(tmp_path):
    events = [{"t0": 1, "t1": 4, "text": "这句话可以看清"}]
    assert "subtitle_phone_size" in [x.code for x in subtitle_report(tmp_path, events, 40).warnings]
    assert subtitle_report(tmp_path, events, 72).checks["subtitle_readability"]["status"] == "pass"


def test_optional_viewer_prompt_keeps_runtime_template_contract():
    import re
    from clipper.llm import fill, load_prompt
    version, system, user = load_prompt("edit_v7")
    _, old_system, old_user = load_prompt("edit_v6")
    variables = lambda s: set(re.findall(r"\{\{(\w+)\}\}", s))
    assert version == "edit-v7"
    assert variables(system + user) == variables(old_system + old_user)
    assert "{{" not in fill(system + user, **{k: "test" for k in variables(system + user)})
