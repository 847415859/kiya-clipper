"""主播周报：梗 TOP5、高光场面、类型占比（攒够才出）、词云；导出文档只含适合主播看的内容。"""
import csv
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper.weekly import build_report, render_html, word_cloud  # noqa: E402

CFG = {"streamer": {"name": "娜由纪雅kiya"}, "taboo": ["某某事件"],
       "lore": {"catchphrases": ["空你几哇"], "memes": [], "recurring_topics": ["律师函"]},
       "weekly": {"min_mentions": 5, "min_type_items": 5}}
END = date(2026, 11, 10)


def ng(speech=None, danmaku=None, hot=None, dm_count=100):
    return {"version": 1, "speech": speech or {}, "hot": hot or {}, "danmaku": danmaku or {}, "sentences": 10, "danmaku_count": dm_count}


def mk(out: Path, name: str, grams: dict, cands: list[dict] | None = None, speech_by_rank: dict | None = None):
    d = out / name
    d.mkdir(parents=True)
    (d / "transcript.json").write_text(json.dumps({"sentences": [{"t0": 1, "t1": 2, "text": "x"}]}), encoding="utf-8")
    (d / "candidates.json").write_text(json.dumps({"segments": [], "candidates": [
        {"rank": c["rank"], "t": c["t"], "clip_start": c["t"] - 30, "clip_end": c["t"] + 30,
         "speech": [{"t0": 0, "t1": 1, "text": (speech_by_rank or {}).get(c["rank"], "")}]} for c in cands or []]}), encoding="utf-8")
    (d / "ngrams.json").write_text(json.dumps(grams), encoding="utf-8")
    if cands:
        (d / "rerank.json").write_text(json.dumps({"candidates": cands}), encoding="utf-8")


def cand(rank, t, typ="破防", funny=True, final=5.0, what="", taboo=False, error=None):
    c = {"rank": rank, "t": t, "final": final, "taboo": taboo,
         "llm": {"funny": funny, "score": final, "type": typ, "what_happened": what or f"事件{rank}", "title_candidates": [f"标题{rank}"]}}
    if error:
        c["error"] = error
    return c


def build(tmp_path, with_marks=True, many=True):
    out = tmp_path / "out"
    out.mkdir()
    cs1 = [cand(1, 3700, "破防", final=8, what="舰长寄来律师函，主播当场愣住"), cand(2, 600, "口胡", final=6),
           cand(3, 900, "高能", final=7, what="聊到某某事件"), cand(4, 1200, "破防", final=3, funny=False),
           cand(5, 1500, "破防", final=9)]
    cs2 = [cand(1, 300, "口胡", final=4), cand(2, 800, "互动", final=5), cand(3, 1000, "破防", final=6)] if many else []
    mk(out, "20261109-2000", ng(speech={"律师函": 6, "空你几哇": 4, "某某事件": 9, "今天天气": 2},
                                danmaku={"律师函": 30, "空你几哇哇": 3, "哈哈哈": 40, "牢猫来了": 12, "牢猫": 15}), cs1,
       {1: "舰长寄来律师函啊"})
    mk(out, "20261107-2100", ng(speech={"律师函": 2, "钓鱼佬": 8}, danmaku={"律师函": 12, "钓鱼佬": 20, "牢猫来了": 9, "牢猫": 11}), cs2)
    mk(out, "20261103-2000", ng(speech={"律师函": 4}, danmaku={"律师函": 4, "钓鱼佬": 2}), [cand(1, 100)])   # 上一周
    mk(out, "20261001-2000", ng(speech={"远古梗": 50}))                                                    # 太早，不在两周内
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    if with_marks:
        with open(reviews / "20261109-2000.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["序号", "片段起", "是否有趣(Y/N)", "备注", "原因", "模式", "耗时(秒)"])
            w.writerow([5, "00:24:30", "N", "", "not_funny", "blind", 20])
    return out, reviews


def test_memes_trend_taboo_and_threshold(tmp_path):
    out, reviews = build(tmp_path)
    rep = build_report(out, CFG, END, reviews)
    memes = {m["text"]: m for m in rep["public"]["memes"]}
    assert "律师函" in memes and "钓鱼佬" in memes
    m = memes["律师函"]
    assert (m["speech"], m["danmaku"], m["sessions"]) == (8, 42, 2) and m["known"] is True
    assert m["prev"] == 8 and m["trend"] == round((50 - 8) / 8, 2) and not m["is_new"]
    assert memes["钓鱼佬"]["is_new"] is False or memes["钓鱼佬"]["prev"] == 2          # 上周也提到过 2 次
    assert "某某事件" not in memes                                                      # 禁区词不进梗
    assert "今天天气" not in memes and "远古梗" not in memes                             # 次数不够 / 不在本周
    assert m["moment"] and "律师函" in m["moment"]["what"]                              # 代表场面
    assert len(rep["public"]["memes"]) <= 5
    assert rep["period"]["start"] == "2026-11-04" and rep["public"]["overview"]["sessions"] == 2   # 11-03 在上周


def test_highlights_exclude_taboo_and_marked_n(tmp_path):
    out, reviews = build(tmp_path)
    rep = build_report(out, CFG, END, reviews)
    hl = rep["public"]["highlights"]
    assert [h["what"] for h in hl][0].startswith("舰长寄来律师函")                       # 精排总分最高的（8；9 的被标了 N）
    whats = " ".join(h["what"] for h in hl)
    assert "某某事件" not in whats                                                       # 命中禁区的不出现
    assert all(h["rank"] != 5 or h["session"] != "20261109-2000" for h in hl)           # 标 N 的不出现
    assert all(h["type"] for h in hl) and len(hl) <= 5
    inn = rep["internal"]
    assert inn["excluded_taboo"] == 1 and inn["excluded_marked_n"] == 1 and inn["reranked"] == 2


def test_type_table_gate_and_views(tmp_path):
    out, reviews = build(tmp_path)
    rep = build_report(out, CFG, END, reviews, {"type_stats": {"破防": {"n": 5, "relative": 1.6}}})
    ty = rep["public"]["types"]
    assert ty["enough"] and ty["have"] == 5 and ty["need"] == 5                          # 好笑的 7 个，去掉禁区 1、标 N 1：剩 5（刚好够）
    rows = {r["type"]: r for r in ty["rows"]}
    assert rows["破防"]["n"] == 2 and rows["口胡"]["n"] == 2 and rows["互动"]["n"] == 1 and sum(r["n"] for r in ty["rows"]) == 5
    assert abs(sum(r["share"] for r in ty["rows"]) - 1) < 0.01
    assert rows["破防"]["view_rel"] == 1.6 and rows["口胡"]["view_rel"] is None and ty["has_views"]
    out2, rv2 = build(tmp_path / "b", many=False) if (tmp_path / "b").mkdir() is None else (None, None)
    rep2 = build_report(out2, CFG, END, rv2)
    t2 = rep2["public"]["types"]
    assert not t2["enough"] and t2["rows"] == [] and t2["have"] == 2                    # 不够 5 个：不下结论
    assert any("不到 5 个" in n for n in rep2["internal"]["notes"])


def test_word_cloud_dedupe_and_filters():
    per = {"a": ng(danmaku={"律师函": 30, "师函": 29, "牢猫来啦": 12, "牢猫": 15, "钓鱼": 20, "钓鱼佬": 5, "牢猫来了": 40, "某某事件": 50, "哈哈哈哈": 99, "我的": 80, "娜由纪雅kiya": 70}),
           "b": ng(danmaku={"律师函": 10})}
    ws = word_cloud(per, CFG, 10)
    texts = [w["text"] for w in ws]
    assert texts[0] == "律师函" and "师函" not in texts                                  # 子串去重
    assert "牢猫来啦" in texts and "牢猫" not in texts                                    # 12 ≥ 0.6×15：短的被长的几乎包含，只留长的
    assert "钓鱼" in texts and "钓鱼佬" in texts                                          # 5 < 0.6×20：长的只是少数，两个都留
    assert "牢猫来了" not in texts                                                       # 以“了”结尾的不是词
    assert not {"某某事件", "哈哈哈哈", "我的", "娜由纪雅kiya"} & set(texts)               # 禁区、无意义词、主播名
    assert ws[0]["weight"] == 1.0 and all(0 <= w["weight"] <= 1 for w in ws)


def test_export_html_has_only_streamer_content(tmp_path):
    out, reviews = build(tmp_path)
    rep = build_report(out, CFG, END, reviews, {"type_stats": {"破防": {"n": 5, "relative": 1.6}}})
    h = render_html(rep)
    assert "律师函" in h and "舰长寄来律师函" in h and "11月04日" not in h and "11月4日 – 11月10日" in h
    assert "<table>" in h and "1.6× 中位数" in h and 'class="cloud"' in h
    for bad in ("审核", "通过率", "标注", "禁区", "精排", "taboo", "不有趣", "20261109-2000", "excluded"):
        assert bad not in h, bad
    assert "某某事件" not in h
    # 特殊字符要转义
    rep["public"]["memes"][0]["text"] = "<script>x</script>"
    assert "<script>x" not in render_html(rep)


def test_empty_week_and_cli(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    rep = build_report(out, CFG, END)
    assert rep["public"]["overview"]["sessions"] == 0 and rep["public"]["memes"] == [] and rep["public"]["words"] == []
    assert rep["internal"]["notes"] and "没有场次" in rep["internal"]["notes"][0]
    assert "还没有" in render_html(rep)
    out, reviews = build(tmp_path / "c") if (tmp_path / "c").mkdir() is None else (None, None)
    cfgf = tmp_path / "kiya.yaml"
    import yaml
    cfgf.write_text(yaml.safe_dump(CFG, allow_unicode=True), encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "weekly_report.py"), "--config", str(cfgf), "--out", str(out),
                        "--reviews", str(reviews), "--end", "2026-11-10"], capture_output=True, text=True,
                       env={"KIYA_MEMORY_DECISIONS": str(tmp_path / "none.json"), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stdout + r.stderr
    jp = Path(r.stdout.strip().splitlines()[-1])
    assert jp.name == "weekly-2026-11-10.json" and jp.with_suffix(".html").exists()
    assert json.loads(jp.read_text(encoding="utf-8"))["public"]["overview"]["sessions"] == 2
    assert "2 场" in r.stdout
