"""审核一键原因 / 盲审与快审 / 边界反馈、AI 初稿 vs 人工终稿的剪辑口味、梗转正和禁区话题。"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from clipper.editdiff import diff_edit, edit_taste, taste_text  # noqa: E402
from clipper.kcfg import apply_decisions, find_taboo, load_cfg  # noqa: E402
from clipper.memory import build_examples, context_text, final_score, load_marks, review_stats  # noqa: E402

HEADER = ["序号", "高峰时间", "片段起", "片段止", "分数", "主要信号", "高频弹幕", "是否有趣(Y/N)", "备注", "原因", "模式", "耗时(秒)"]


def write_review(reviews: Path, sess: str, rows: list[list]):
    reviews.mkdir(exist_ok=True)
    with open(reviews / f"{sess}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for r in rows:
            w.writerow([r[0], "00:01:00", f"00:00:{30 + r[0]:02d}", "00:02:00", "2.0", "", "", *r[1:]])


def make_session(out: Path, name: str, n: int):
    d = out / name
    d.mkdir(parents=True)
    (d / "candidates.json").write_text(json.dumps({"candidates": [
        {"rank": i, "t": 60, "clip_start": 30 + i, "clip_end": 120} for i in range(1, n + 1)]}), encoding="utf-8")
    (d / "rerank.json").write_text(json.dumps({"candidates": [
        {"rank": i, "llm": {"what_happened": f"事件{i}", "type": "互动", "score": 5}} for i in range(1, n + 1)]}), encoding="utf-8")
    (d / "transcript.json").write_text(json.dumps({"sentences": [{"t0": 40, "t1": 50, "text": "说点什么"}]}), encoding="utf-8")


def test_reasons_modes_and_boundary(tmp_path):
    out, reviews = tmp_path / "out", tmp_path / "reviews"
    make_session(out, "20261101-2000", 6)
    write_review(reviews, "20261101-2000", [
        [1, "Y", "笑点在 0:35", "gem|start_early", "blind", 40],
        [2, "N", "", "read_sc", "fast", 12],
        [3, "Y", "", "", "confirm", 3],                      # 直接采纳 AI：不做例子、不进评测
        [4, "N", "", "taboo|not_funny", "", ""],             # 旧数据没有模式 = 盲审
        [5, "Y", "", "start_early", "blind", 30],
        [6, "", "", "", "", ""]])                            # 没标 Y/N 的不算
    marks = load_marks(reviews)["20261101-2000"]
    assert set(marks) == {1, 2, 3, 4, 5} and marks[1]["reasons"] == ["gem", "start_early"] and marks[1]["seconds"] == 40
    assert set(load_marks(reviews, trusted_only=True)["20261101-2000"]) == {1, 4, 5}      # 只留盲审 / 旧数据

    ex = build_examples(out, load_marks(reviews), set())
    assert {e["rank"] for e in ex} == {1, 2, 4, 5}                                            # confirm 的没有新信息
    assert next(e for e in ex if e["rank"] == 4)["veto"] and not next(e for e in ex if e["rank"] == 1)["veto"]
    text, _ = context_text({"examples": ex, "version": "m-x"}, "20261102-2000",
                           {"memory": {"examples_yes": 3, "examples_no": 3}})
    assert "原因：特别好笑" in text and "只是在念醒目留言" in text
    assert "【主播明确不想发的内容" in text and "事件4" in text.split("【主播明确不想发的内容")[1]
    own, _ = context_text({"examples": ex, "version": "m-x"}, "20261101-2000", {})
    assert "事件" not in own                                                                  # 自己这场的不拿来考自己

    st = review_stats(load_marks(reviews), {"min_boundary_votes": 8})
    assert st["marked"] == 5 and st["avg_seconds"] == 21 and st["modes"] == {"blind": 3, "fast": 1, "confirm": 1}
    assert st["boundary"]["votes"]["start_early"] == 2 and not st["boundary"]["suggestions"]   # 票数不够：不提建议

    big = {s: {r: {"mark": "Y", "reasons": ["start_early"] * (r < 9) + ["start_late"] * (r == 9), "mode": "blind", "seconds": 5}
               for r in range(1, 11)} for s in ("a", "b")}
    sug = review_stats(big, {"min_boundary_votes": 8}, 30, 30)["boundary"]["suggestions"]
    assert len(sug) == 1 and "开头多数反馈偏长" in sug[0] and "30 调到 25" in sug[0]       # 一边倒才建议，结尾没票不提


def test_evaluate_skips_non_blind(tmp_path):
    import evaluate
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    with open(reviews / "s1.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["序号", "分数", "是否有趣(Y/N)", "模式"])
        w.writerows([[1, 2.0, "Y", "blind"], [2, 1.0, "N", ""], [3, 3.0, "Y", "fast"], [4, 3.0, "Y", "confirm"]])
    assert [(r[1], r[2]) for r in evaluate.load_review_scores(reviews)] == [(2.0, "Y"), (1.0, "N")]


def edit(items, trim=(0, 60), ws=100.0):
    return {"window": {"start": ws, "end": ws + 60}, "trim": {"start": trim[0], "end": trim[1]}, "items": items}


def test_diff_edit():
    ai = edit([{"type": "zoom", "start": 20, "duration": 2}, {"type": "text", "start": 20.2, "duration": 2, "text": "发舰礼？！"},
               {"type": "text", "start": 40, "duration": 2, "text": "笑死"}, {"type": "sticker", "start": 45, "duration": 1, "asset": "a.png"}],
              trim=(5, 50))
    human = edit([{"type": "zoom", "start": 20.1, "duration": 2}, {"type": "text", "start": 20.2, "duration": 2, "text": "舰礼寄来了内衣"},
                  {"type": "sticker", "start": 48, "duration": 1, "asset": "b.png"}, {"type": "text", "start": 55, "duration": 1, "text": "新加的"}],
                 trim=(8, 50))
    d = diff_edit(ai, human)
    assert d["kept"] == 2 + 0 and d["text_changed"] == [{"from": "发舰礼？！", "to": "舰礼寄来了内衣"}]
    assert [(x["type"], x["text"]) for x in d["removed"]] == [("text", "笑死"), ("sticker", "a.png")]   # 贴图时间差超过 2 秒 → 算删掉
    assert [(x["type"], x["text"]) for x in d["added"]] == [("sticker", "b.png"), ("text", "新加的")]
    assert d["trim_shift"] == {"start": 3.0, "end": 0.0} and d["changed"]
    same = diff_edit(ai, ai)
    assert not same["changed"] and same["kept"] == 4
    shifted = diff_edit(ai, edit(ai["items"], trim=(5, 50), ws=110.0))        # 窗口起点不同，换成整场秒数比较
    assert len(shifted["removed"]) == 4 and len(shifted["added"]) == 4         # 整体晚了 10 秒：一个都对不上


def test_edit_taste_gate(tmp_path):
    out = tmp_path / "out"
    for n, s in enumerate(["20261101-2000", "20261102-2000", "20261103-2000"]):
        (out / s / "edits").mkdir(parents=True)
        for rank in (1, 2):
            ai = edit([{"type": "text", "start": 10, "duration": 2, "text": f"原花字{n}{rank}"},
                       {"type": "zoom", "start": 11, "duration": 2}, {"type": "sticker", "start": 30, "duration": 1, "asset": "a.png"}])
            human = edit([{"type": "zoom", "start": 11, "duration": 2}], trim=(3, 60))
            human["human_saved_at"] = f"2026-11-0{n + 1}T10:0{rank}:00"
            (out / s / "edits" / f"{rank:02d}.ai.json").write_text(json.dumps({"edit": ai}), encoding="utf-8")
            (out / s / "edits" / f"{rank:02d}.json").write_text(json.dumps(human), encoding="utf-8")
    (out / "20261103-2000" / "edits" / "03.ai.json").write_text(json.dumps({"edit": edit([])}), encoding="utf-8")   # 没有人工终稿：不算
    t = edit_taste(out, min_edits=5, min_sessions=2)
    assert t["n"] == 6 and t["sessions"] == 3 and t["active"]
    assert t["by_type"]["text"] == {"ai": 6, "kept": 0, "removed": 6, "added": 0}
    assert t["by_type"]["zoom"]["removed"] == 0 and t["trim_shift"] == {"start": 3.0, "end": 0.0}
    txt = taste_text(t)
    assert "花字：AI 排的有 100% 被剪辑师删掉" in txt and "贴图" in txt and "开头平均往后挪 3 秒" in txt and "原花字" in txt
    assert "推镜：基本都被保留" in txt
    few = edit_taste(out, min_edits=10, min_sessions=2)
    assert not few["active"] and "还差 4 条人工修改" in few["status"] and taste_text(few) == ""
    one = edit_taste(out, min_edits=1, min_sessions=4)
    assert not one["active"] and "不同场次" in one["status"]


def test_promote_and_taboo(tmp_path):
    base = {"lore": {"catchphrases": ["喵"], "recurring_topics": ["做局"]}, "taboo": ["静态禁词"]}
    dec = {"promoted_lore": [{"text": "律师函警告", "kind": "meme"}, {"text": "泥嚎", "kind": "catchphrase"},
                              {"text": "喵", "kind": "catchphrase"}, {"text": "钓鱼", "kind": "topic"}],
           "taboo": [{"word": "某某事件", "note": ""}, "静态禁词"]}
    cfg = apply_decisions(base, dec)
    assert cfg["lore"]["memes"] == ["律师函警告"] and cfg["lore"]["catchphrases"] == ["喵", "泥嚎"]       # 已有的不重复
    assert cfg["lore"]["recurring_topics"] == ["做局", "钓鱼"] and cfg["taboo"] == ["静态禁词", "某某事件"]
    assert base["lore"]["catchphrases"] == ["喵"]                                                       # 不改原配置
    from clipper.asr import build_hotwords
    from clipper.subtitles import lore_keywords
    assert "律师函警告" in {w["text"] for w in build_hotwords(cfg)} and "律师函警告" in lore_keywords(cfg)
    from clipper.context import profile
    assert "律师函警告" in profile(cfg)
    assert find_taboo("这件某某事件我不想讲", cfg["taboo"]) == ["某某事件"] and find_taboo("", cfg["taboo"]) == []
    yml = tmp_path / "k.yaml"
    yml.write_text("lore: {nicknames: [kiya]}\n", encoding="utf-8")
    assert load_cfg(yml, dec)["lore"]["memes"] == ["律师函警告"] and load_cfg(yml, {})["taboo"] == []


def test_rerank_taboo(tmp_path):
    import rerank
    f = tmp_path / "rerank.json"
    cands = [{"rank": r, "clip_start": 30 * r, "clip_end": 30 * r + 30, "llm": {"score": s, "funny": True}}
             for r, s in ((1, 9), (2, 8), (3, 7))]
    f.write_text(json.dumps({"candidates": cands}), encoding="utf-8")
    sents = [{"t0": 35, "t1": 40, "text": "提到了某某事件"}]                     # 在第 1 个候选（30–60 秒）里
    w = {"llm": 0.8, "screen": 0.2, "not_funny": 0.6}
    assert rerank.rescore(f, w, 3, ["某某事件"], sents) == 0
    d = json.loads(f.read_text(encoding="utf-8"))
    assert [c["rank"] for c in d["candidates"]] == [2, 3, 1] and d["candidates"][-1]["final"] == 0
    assert d["candidates"][-1]["taboo"] == ["某某事件"] and d["candidates"][0]["taboo"] == []
    rerank.rescore(f, w, 3, [], sents)                                         # 禁区词删掉后恢复
    assert json.loads(f.read_text(encoding="utf-8"))["candidates"][0]["rank"] == 1
    assert final_score({"score": 9, "funny": True}, 1, 3, w, taboo=True) == 0.0
