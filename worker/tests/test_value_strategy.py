from clipper.boundary import refine_bounds
from clipper.memory import value_final_score, value_score
from scripts.rerank import is_pick


def item(**values):
    return {"value_schema_version": 1, "standalone": True, "complete": True,
            "confidence": 1.0, "tier": "推荐",
            "value_evidence": [{"dimension": k, "src": "原话", "t": 0, "quote": "真实表达", "why": "对应行为"}
                               for k in ("cute", "contrast", "charm", "unique", "funny_strength")], **values}


def test_value_score_requires_complete_v1_dimensions():
    assert value_score(item(cute=.8, contrast=.7, charm=.6, unique=.5, funny_strength=.4)) is not None
    assert value_score(item(cute=.8, contrast=.7, charm=.6, unique=.5)) is None
    assert value_score({"funny": True, "score": 10}) is None


def test_value_path_does_not_require_funny_boolean():
    result = item(cute=.8, contrast=.8, charm=.8, unique=.8, funny_strength=.1)
    assert is_pick(result, False, value_pick_score=.65)
    assert value_final_score(result, 1, 1) >= .65


def test_explicit_short_bounds_are_not_forced_to_minimum_length():
    sentences = [{"t0": 10.0, "t1": 10.8}, {"t0": 10.8, "t1": 11.3}]
    refined = refine_bounds(sentences, peak=10.5, start=10.0, end=11.3, min_len=5.0, max_len=20.0)
    assert refined["end"] - refined["start"] < 5.0
    assert "补足最短时长" not in refined["note"]


def test_missing_start_still_uses_default_lead():
    sentences = [{"t0": 8.0, "t1": 10.0}, {"t0": 10.0, "t1": 12.0}]
    refined = refine_bounds(sentences, peak=10.5, start=None, end=11.0, min_len=5.0, max_len=20.0)
    assert refined["start"] < 10.0
    assert "按默认前留" in refined["note"]
