"""精排 v3：更严的校准、后续原话、推荐发布（pick）、不能单独看懂降权。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from clipper.llm import fill, load_prompt  # noqa: E402
from clipper.memory import DEFAULT_WEIGHTS, final_score  # noqa: E402
from rerank import RerankOut, is_pick  # noqa: E402


def test_v3_prompt_fills_all_variables():
    v, system, user = load_prompt("rerank_v3")
    assert v == "rerank-v3"
    keys = dict(profile="p", memory="m", window="60", peak="30", context_before="60", context_after="45",
                before_speech="前", speech="中", after_speech="后", danmaku="d", gifts="g", frames_note="f")
    text = fill(user, **keys)
    assert "{{" not in text and "后" in text
    assert "8 分以上通常不超过 2–3 个" in system and "needs_context" in text and "complete" in text
    assert "最多多 45 秒" in text


def test_v2_prompt_unchanged_has_no_after_variable():
    _, _, user = load_prompt("rerank_v2")
    assert "{{after_speech}}" not in user                 # v2 仍可切回，不需要新变量


def test_old_results_still_parse():
    o = RerankOut(funny=True, score=8, standalone=True)   # v2 的输出没有 needs_context / complete
    assert o.complete is True and o.needs_context is False


def test_is_pick():
    ok = dict(funny=True, score=8.0, standalone=True, complete=True, needs_context=False)
    assert is_pick(ok, False)
    assert not is_pick(ok, True)                                    # 命中禁区
    assert not is_pick(dict(ok, score=7.0), False)                  # 分数不够
    assert not is_pick(dict(ok, standalone=False), False)
    assert not is_pick(dict(ok, complete=False), False)
    assert not is_pick(dict(ok, needs_context=True), False)
    assert not is_pick(dict(ok, funny=False), False)
    assert not is_pick(None, False)
    assert is_pick(dict(ok, score=7.0), False, pick_score=7)        # 阈值可配


def test_not_standalone_penalty_is_opt_in():
    base = dict(DEFAULT_WEIGHTS)
    llm = {"score": 8, "funny": True, "standalone": False}
    same = final_score(llm, 1, 10, base)                            # 没配 not_standalone：不惩罚，旧行为不变
    assert same == final_score(dict(llm, standalone=True), 1, 10, base)
    w = dict(base, not_standalone=0.7)
    assert abs(final_score(llm, 1, 10, w) - same * 0.7) < 1e-9
    assert final_score(dict(llm, standalone=True), 1, 10, w) == same   # 能单独看懂的不受影响
    assert final_score({"score": 8, "funny": True}, 1, 10, w) == same  # 旧结果没有 standalone 字段：不惩罚
