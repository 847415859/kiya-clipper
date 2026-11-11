"""精排 v4：证据 / 风险 / 置信度 / 失败原因 / 交接字段、同场二次比较、AI 判错的例子优先、pick 更严。"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from clipper.llm import fill, load_prompt  # noqa: E402
from clipper.memory import DEFAULT_WEIGHTS, context_text, final_score, is_hard, pick_examples  # noqa: E402
from rerank import ListOut, RerankOut, is_pick, list_item_text, listwise_pool, note_of, run_listwise  # noqa: E402

KEYS = dict(profile="p", memory="m", window="60", peak="30", context_before="120", context_after="240",
            before_speech="前", speech="中", after_speech="后", danmaku="d", gifts="g", frames_note="f")


def test_v4_prompt_has_no_peak_anchor_and_fills():
    v, system, user = load_prompt("rerank_v4")
    assert v == "rerank-v4"
    assert "{{peak}}" not in user and "高峰大约" not in user         # 不再告诉模型“粗筛认为高峰在第 N 秒”
    text = fill(user, **KEYS)
    assert "{{" not in text and "最多多 240 秒" in text
    for k in ("confidence", "evidence", "risk_level", "fail_reason", "context_caption", "beats", "cover_time", "punch_line", "hook", "asr_doubt",
              "in_character", "pace", "highlight_danmaku", "types", "start_reason", "end_reason", "highlight_times", "edit_hints", "src"):
        assert k in text
    for k in ("主播", "观众", "审核", "剪辑师"):
        assert k in system
    assert "输出前自检" in system and "证据要求" in system
    assert "必须是数字" in system and "只用来帮你听懂" in system
    assert "断章取义" in system and "人设一致性" in system and "不得编造" in system and "前 3 秒" in system


def test_old_outputs_parse_with_defaults():
    o = RerankOut(funny=True, score=8, standalone=True)
    assert o.confidence == 1.0 and o.risk_level == "none" and o.tier == "" and o.cover_time == -1
    assert o.evidence == [] and o.beats == [] and o.fail_reason == ""


def test_v4_output_is_cleaned():
    o = RerankOut(funny=False, score=3, risk_level="weird", fail_reason="whatever", tier="随便",
                  evidence=[{"t": 12, "quote": "原话"}, "只有一句", 5, {"t": 1, "quote": "c"}, {"t": 2, "quote": "d"}],
                  risks=["低俗擦边", "", "  "], beats=["12s 起因：x"] * 9, context_caption="前" * 100, cover_time=33)
    assert o.risk_level == "low" and o.fail_reason == "other" and o.tier == ""
    assert len(o.evidence) == 3 and o.evidence[1].quote == "只有一句"
    assert o.risks == ["低俗擦边"] and len(o.beats) == 6 and len(o.context_caption) == 60 and o.cover_time == 33
    assert RerankOut(funny=True, score=8, fail_reason="weak_hook").fail_reason == "weak_hook"


def test_hook_pace_character_fields():
    o = RerankOut(funny=True, score=8, hook="弱", pace="渐强", highlight_danmaku=["哈哈哈", "", "？？？"] + ["x"] * 9, in_character=False)
    assert o.hook == "弱" and o.hook_ok is False and o.pace == "渐强" and o.in_character is False and len(o.highlight_danmaku) == 5
    assert RerankOut(funny=True, score=8, hook="强").hook_ok is True
    bad = RerankOut(funny=True, score=8, hook="很强", pace="随便")
    assert bad.hook == "" and bad.pace == "" and bad.hook_ok is True            # 不认识的取值当没填；旧结果没有这些字段也一样
    assert RerankOut(funny=True, score=8, hook_ok=False).hook_ok is False        # 旧字段仍然有效


def test_reviewer_and_editor_fields():
    o = RerankOut(funny=True, score=8, types=["反差", "反差", "无", "随便", "破防", "口胡"], start_reason="  从起因切  " + "长" * 100,
                  highlight_times=[21, "30.5", "x", 40, 50], edit_hints=["21s 放大", "", "38s 花字"],
                  evidence=[{"t": 3, "src": "弹幕", "quote": "弹幕刷草"}, {"t": 5, "src": "乱写", "quote": "原话"}, "只有一句"])
    assert o.types == ["反差", "破防"] and o.start_reason.startswith("从起因切") and len(o.start_reason) == 80
    assert o.highlight_times == [21.0, 30.5, 40.0] and o.edit_hints == ["21s 放大", "38s 花字"]
    assert [e.src for e in o.evidence] == ["弹幕", "原话", "原话"]
    d = RerankOut(funny=True, score=8)
    assert d.types == [] and d.end_reason == "" and d.highlight_times == [] and d.edit_hints == []     # 旧结果照常读


def test_seconds_written_as_text_are_tolerated():
    o = RerankOut(funny=True, score=8, punch_time="约 21 秒", start_time="-15s", end_time="45.5", cover_time="12秒",
                  evidence=[{"t": "第3秒", "quote": "x"}])
    assert (o.punch_time, o.start_time, o.end_time, o.cover_time) == (21.0, -15.0, 45.5, 12.0) and o.evidence[0].t == 3.0
    assert RerankOut(funny=True, score=8, start_time=None).start_time is None
    import pytest
    with pytest.raises(Exception):
        RerankOut(funny=True, score=8, punch_time="看不出来")           # 取不出数字：仍然报错，交给重试


def test_is_pick_v4_rules():
    ok = dict(funny=True, score=8.0, standalone=True, complete=True, needs_context=False, confidence=0.9, risk_level="none")
    assert is_pick(ok, False, 8, 0.6)
    assert not is_pick(dict(ok, risk_level="high"), False, 8, 0.6)       # 审核 / 主播风险高
    assert not is_pick(dict(ok, in_character=False), False, 8, 0.6)      # 会被误读成真失态：留给人看
    assert is_pick(dict(ok, in_character=True), False, 8, 0.6)
    assert is_pick(dict(ok, risk_level="low"), False, 8, 0.6)
    assert not is_pick(dict(ok, confidence=0.4), False, 8, 0.6)           # 把握不足
    assert is_pick(dict(ok, confidence=0.4), False, 8, 0.0)               # 阈值可关
    assert not is_pick(dict(ok, tier="备选"), False, 8, 0.6)               # 二次比较没选它
    assert not is_pick(dict(ok, tier="淘汰"), False, 8, 0.6)
    assert is_pick(dict(ok, tier="推荐"), False, 8, 0.6)
    assert is_pick(dict(ok, tier=""), False, 8, 0.6)                      # 没跑二次比较：按单条结论


def _rec(rank, score, **kw):
    llm = dict(funny=True, score=score, type="口胡", standalone=True, complete=True, needs_context=False,
               what_happened=f"事{rank}", reason="因", punch_line=f"话{rank}", confidence=0.9, risk_level="none",
               risks=[], evidence=[{"t": 3, "quote": f"证{rank}"}], hook_ok=True)
    llm.update(kw)
    return {"rank": rank, "llm": llm, "refined": {"start": 0, "end": 60}, "taboo": []}


class StubClient:
    def __init__(self, items=None, fail=False):
        self.items, self.fail, self.calls = items or [], fail, []

    def chat_json(self, msgs, schema, tag=""):
        self.calls.append((msgs, tag))
        if self.fail:
            return SimpleNamespace(data=None, error="boom")
        return SimpleNamespace(data=ListOut.model_validate({"items": self.items}), error=None)


RC = dict(listwise_prompt="rerank_list_v1", listwise_min_score=7, listwise_top_n=10, listwise_max_pick=2)


def test_listwise_pool_filters_and_orders():
    recs = [_rec(1, 6), _rec(2, 9), _rec(3, 8, funny=False), _rec(4, 8), dict(_rec(5, 9), taboo=["x"]), {"rank": 6, "llm": None}]
    assert [r["rank"] for r in listwise_pool(recs, 7, 10)] == [2, 4]
    assert [r["rank"] for r in listwise_pool(recs, 7, 1)] == [2]


def test_run_listwise_sets_tiers_and_caps_picks():
    recs = [_rec(1, 9), _rec(2, 8), _rec(3, 8), _rec(4, 5)]
    cl = StubClient([{"rank": 1, "tier": "推荐", "reason": "最好"}, {"rank": 2, "tier": "推荐"}, {"rank": 3, "tier": "推荐", "dup_of": 2}])
    msg = run_listwise(cl, recs, {"profile": {}}, RC)
    assert [r["llm"].get("tier", "") for r in recs] == ["推荐", "推荐", "备选", ""]       # 推荐最多 2 条，超出的降为备选；分数不够的不参与
    assert "超过本场推荐上限" in recs[2]["listwise"]["reason"] and "推荐 2" in msg
    text = cl.calls[0][0][1]["content"]
    assert "#1" in text and "证1" in text and "话2" in text and "#4" not in text
    assert "不超过 2 条" in cl.calls[0][0][0]["content"]


def test_run_listwise_missing_item_is_alternate_and_failure_is_harmless():
    recs = [_rec(1, 9), _rec(2, 8)]
    run_listwise(StubClient([{"rank": 1, "tier": "推荐"}]), recs, {"profile": {}}, RC)
    assert recs[1]["llm"]["tier"] == "备选"
    recs = [_rec(1, 9), _rec(2, 8)]
    msg = run_listwise(StubClient(fail=True), recs, {"profile": {}}, RC)
    assert "失败" in msg and all(r["llm"].get("tier", "") == "" for r in recs)
    solo = [_rec(1, 9)]
    cl = StubClient()
    assert "跳过" in run_listwise(cl, solo, {"profile": {}}, RC) and not cl.calls


def test_list_item_text_flags_problems():
    t = list_item_text(_rec(7, 8, asr_doubt=True, hook_ok=False, hook_note="从第二句切", risk_level="high", risks=["引战"], needs_context=True))
    t2 = list_item_text(_rec(8, 8, hook_ok=False, hook="无", in_character=False, in_character_note="像真的在骂人"))
    assert "开头无" in t2 and "不符合人设" in t2 and "像真的在骂人" in t2
    assert "#7" in t and "听错" in t and "从第二句切" in t and "风险 high" in t and "引战" in t and "要前情" in t


def test_tier_multiplier_in_final_score():
    w = dict(DEFAULT_WEIGHTS)
    base = {"score": 8, "funny": True}
    f0 = final_score(base, 1, 10, w)
    assert final_score(dict(base, tier="推荐"), 1, 10, w) > f0 > final_score(dict(base, tier="淘汰"), 1, 10, w)
    assert final_score(dict(base, tier=""), 1, 10, w) == f0


def test_note_of():
    assert note_of({"llm": {"tier": "淘汰", "fail_reason": "not_funny", "asr_doubt": True, "risk_level": "high"}}) == "[淘汰·风险高·听写存疑·not_funny] "
    assert note_of({"pick": True, "llm": {"tier": "推荐", "fail_reason": ""}}) == "[推荐] "
    assert note_of({"llm": {}}) == ""


def _ex(sess, rank, mark, score, **kw):
    return dict({"id": f"{sess}#{rank}", "session": sess, "rank": rank, "mark": mark, "note": "", "reasons": [], "veto": False,
                 "what_happened": f"{sess}-{rank}", "type": "口胡", "llm_score": score, "speech": "", "excluded": False}, **kw)


def test_hard_examples_come_first():
    assert is_hard(_ex("a", 1, "N", 8)) and is_hard(_ex("a", 1, "Y", 5))
    assert not is_hard(_ex("a", 1, "N", 4)) and not is_hard(_ex("a", 1, "Y", 8)) and not is_hard(_ex("a", 1, "N", None))
    exs = [_ex("20261101", 1, "N", 3), _ex("20261102", 1, "N", 3), _ex("20261103", 1, "N", 9),        # 只有 11-03 是 AI 误报
           _ex("20261101", 2, "Y", 9), _ex("20261102", 2, "Y", 5)]                                  # 11-02#2 是 AI 漏报
    got = pick_examples(exs, "20261110", 1, 1)
    assert [e["id"] for e in got] == ["20261102#2", "20261103#1"]
    # 不是判错的例子，仍按“有备注 / 更新”排
    exs2 = [_ex("20261101", 1, "N", 3), _ex("20261102", 1, "N", 3)]
    assert pick_examples(exs2, "x", 0, 1)[0]["id"] == "20261102#1"


def test_context_text_marks_ai_mistakes():
    mem = {"version": "v", "examples": [_ex("20261103", 1, "N", 9, what_happened="她在念弹幕"), _ex("20261102", 2, "Y", 5, what_happened="漏掉的好笑")]}
    text, _ = context_text(mem, "20261110", {"memory": {}})
    assert "AI 当时给了 9 分，判断错了" in text and "AI 当时只给了 5 分，漏掉了" in text


# ---------------------------------------------------------------- 下游：edit_v5
def test_edit_v5_prompt_and_helpers():
    sys.path.insert(0, str(ROOT / "scripts"))
    from autoedit import shift_beats
    from clipper.edit import ensure_caption, validate_item
    v, system, user = load_prompt("edit_v5")
    assert v == "edit-v5"
    for k in ("caption", "punch_line", "hook_note", "beats", "pace", "highlight_danmaku", "types", "zoom_times", "edit_hints"):
        assert "{{" + k + "}}" in user
    assert "综艺版" in system and "{{max_items}}" in system and "{{max_memes}}" in system and "{{meme_gap}}" in system
    text = fill(user, profile="p", taste="t", variant="v", memes="m", window="60", trim_start="0", trim_end="60", what_happened="w", punch="30",
                setup="s", punchline="p", reaction="r", caption="她输了赌约", punch_line="我不玩了", hook_note="x", beats="- b", pace="渐强", highlight_danmaku="哈哈哈", types="反差", zoom_times="21", edit_hints="- h",
                speech="sp", danmaku="d", stickers="s", colors="#FFE14D")
    assert "{{" not in text
    assert shift_beats(["12s 起因：开始", "30.5s 笑点", "没有秒数"], -10) == "- [2s] 起因：开始\n- [20s] 笑点\n- 没有秒数"
    assert shift_beats([], 0) == ""
    trim = {"start": 5.0, "end": 60.0}
    items = [validate_item({"type": "text", "text": "破防了", "start": 30, "duration": 2}, 80)]
    out = ensure_caption(items, "她输了赌约", trim)
    assert out[0]["text"] == "她输了赌约" and out[0]["start"] == 5.2 and out[0]["preset"] == "fade" and len(out) == 2
    assert ensure_caption(items, "", trim) is items and ensure_caption(items, "（不需要）", trim) is items
    has = [validate_item({"type": "text", "text": "她输了赌约，要念黑历史", "start": 6, "duration": 2}, 80)]
    assert ensure_caption(has, "她输了赌约", trim) is has                    # 开头 6 秒内已经有了：不重复补


# ---------------------------------------------------------------- 输入构造：抽帧 / 弹幕 / SC
def test_frame_times_modes():
    from clipper.context import frame_times
    u = frame_times(0, 60, 8)
    assert len(u) == 8 and u[0] == 3.75 and u[1] - u[0] == 7.5                       # 均匀：和以前一样
    assert frame_times(0, 60, 8, peak=30, mode="uniform") == u
    p = frame_times(0, 60, 8, peak=30, mode="peak")
    assert len(p) == 8 and p == sorted(p) and all(0 <= t < 60 for t in p)
    near = [t for t in p if abs(t - 30) <= 8.01]
    assert len(near) >= 4                                                          # 至少一半集中在高峰前后
    assert sum(1 for t in p if abs(t - 30) > 10) >= 3                             # 另一半仍看到整体
    for x, y in zip(p, p[1:]):
        assert y - x > 0.5                                                          # 不会挤成一团
    assert frame_times(0, 60, 8, peak=None, mode="peak") == u                       # 没有高峰：退回均匀
    assert frame_times(0, 12, 8, peak=6, mode="peak") == frame_times(0, 12, 8)      # 窗口太短：退回均匀
    assert len(frame_times(100, 400, 8, peak=390, mode="peak")) == 8                # 高峰贴着边也是 8 帧


def _dm(t, text):
    from clipper.danmaku import Event
    return Event(t=t, kind="danmaku", text=text)


def test_pick_danmaku_keeps_key_lines():
    from clipper.context import pick_danmaku
    dm = [_dm(float(i), f"路人{i}") for i in range(200)]
    dm[137] = _dm(137.0, "绷不住了")                                                   # 笑点处那条
    uni = pick_danmaku(dm, 30)                                                       # 老办法：均匀抽样，会漏掉
    assert len(uni) == 30 and all(e.text != "绷不住了" for e in uni)
    hit = lambda t: "绷不住" in t
    sm = pick_danmaku(dm, 30, delay=0, peak=140, hit=hit)
    assert len(sm) == 30 and any(e.text == "绷不住了" for e in sm)
    assert [e.t for e in sm] == sorted(e.t for e in sm)                              # 按时间排
    assert sum(1 for e in sm if abs(e.t - 140) <= 10) >= 10 and sum(1 for e in sm if abs(e.t - 140) > 30) >= 5   # 高峰附近多，远处也有
    assert pick_danmaku(dm[:10], 30, 0, 140, hit) == dm[:10]                         # 没超量：原样


def test_danmaku_lines_gifts_prefer_sc():
    from clipper.context import danmaku_lines
    from clipper.danmaku import Event
    ev = [Event(t=float(i), kind="gift", text="小花", value=1) for i in range(40)] + \
         [Event(t=50.0, kind="sc", text="念一下你的黑历史", value=30), Event(t=51.0, kind="guard", text="舰长", value=198)]
    _, g = danmaku_lines(ev, 0, 100, 0, 0)
    lines = g.splitlines()
    assert len(lines) == 30 and any("念一下你的黑历史" in x for x in lines) and any("上舰" in x for x in lines)
    assert [int(x.split("]")[0][1:]) for x in lines] == sorted(int(x.split("]")[0][1:]) for x in lines)


def test_prompt_mentions_sc_as_cause():
    _, system, user = load_prompt("rerank_v4")
    assert "（SC）的文字" in system and "先读它" in user
