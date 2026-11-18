import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper.event import generate_boundary_options, merge_candidates, normalize_event, stable_event_id


def test_merge_candidates_keeps_sources_and_stable_id():
    candidates = [
        {"clip_start": 10, "clip_end": 20, "source": "speech", "what_happened": "主播突然笑了"},
        {"clip_start": 18, "clip_end": 28, "source": "emotion", "what_happened": "主播突然笑了起来"},
        {"clip_start": 100, "clip_end": 110, "source": "speech", "what_happened": "主播解释原因"},
    ]
    merged = merge_candidates(candidates)
    assert len(merged) == 2
    assert merged[0]["source_ids"] == ["speech", "emotion"]
    assert merged[0]["event_id"] == stable_event_id(merged[0])


def test_different_core_is_not_merged_only_by_time():
    candidates = [
        {"clip_start": 10, "clip_end": 20, "source": "a", "what_happened": "主播突然笑了"},
        {"clip_start": 18, "clip_end": 22, "source": "b", "what_happened": "主播突然哭了"},
    ]
    assert len(merge_candidates(candidates)) == 2


def test_event_normalization_marks_missing_understanding():
    event = normalize_event({"rank": 1, "clip_start": 2, "clip_end": 8}, {})
    assert event["status"] == "needs_review"
    assert "缺少事件概述" in event["understanding_issues"]


def test_boundary_options_have_three_tradeoffs_and_preserve_core():
    candidate = {"clip_start": 10, "clip_end": 30, "t": 20, "what_happened": "主播突然笑了"}
    event = {"core": "主播突然笑了", "core_start": 18, "core_end": 22, "context_start": 10, "reaction_end": 28}
    options = generate_boundary_options(candidate, event, {"start": 14, "end": 25}, material_end=60)
    assert {item["key"] for item in options} == {"concise", "context", "afterglow"}
    assert all(item["start"] <= 18 and item["end"] >= 22 for item in options)


def test_boundary_options_filter_material_overflow():
    candidate = {"clip_start": 10, "clip_end": 30, "t": 20, "what_happened": "主播突然笑了"}
    event = {"core": "主播突然笑了", "core_start": 18, "core_end": 22, "reaction_end": 80}
    options = generate_boundary_options(candidate, event, {"start": 14, "end": 25}, material_end=30)
    assert all(item["end"] <= 30 for item in options)
