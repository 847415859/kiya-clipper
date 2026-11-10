"""梗库：B站热门短语提取、热度衰减、建库（来源 / 准入 / 黑名单 / 禁区 / 降温 / 保留率）、检索（向量 + 字面备用）、
剪辑初稿里的梗（标记保留 / 数量与间隔 / 编造的梗去掉）、scripts/memes.py。不联网、不花钱。
"""
import json
import os
import subprocess
import sys
import threading
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from clipper import hot  # noqa: E402
from clipper.bili import BiliError  # noqa: E402
from clipper.edit import validate_edit, validate_item  # noqa: E402
from clipper.embed import Embedder, EmbedError, lexical_scores  # noqa: E402
from clipper.memes import (build_library, enforce_memes, meme_stats, memes_in_edit, offer_text, retrieve,  # noqa: E402
                           situation_text, usable)
from fake_xml import make_xml  # noqa: E402

TODAY = date(2026, 11, 10)


def day(n):
    return (TODAY - timedelta(days=n)).isoformat()


# ---------------------------------------------------------------- 热门抓取
TITLES = ["【整活大赏】今天也在认真整活", "整活大赏第三期：翻车现场", "这也太整活大赏了吧", "整活大赏！笑到邻居敲门",
          "平平无奇的一期视频", "另一个完全不同的话题", "随便聊聊今天的天气"]


def test_title_phrases():
    p = hot.title_phrases(TITLES, min_titles=3)
    assert "整活大赏" in p and p["整活大赏"] == 3          # 【】里的不算
    assert "整活" not in p                                   # 被更长的说法几乎包含 → 去掉
    assert not any("天气" in k for k in p)                    # 只出现一次的不算
    assert hot.title_phrases(TITLES, min_titles=5) == {}


class FakeBili:
    def __init__(self, popular=None, weekly=None, search=None):
        self.p, self.w, self.s = popular, weekly, search

    def _r(self, v):
        if v is None:
            raise BiliError("风控")
        return v

    def popular(self): return self._r(self.p)
    def weekly_must_watch(self): return self._r(self.w)
    def hot_search(self): return self._r(self.s)


def test_crawl_partial_and_total_failure():
    snap = hot.crawl(FakeBili(TITLES, None, ["  【热】钓鱼佬 ", "12345", "牢"]), min_titles=3, log=lambda *a: None)
    assert snap["sources"] == {"popular": 7, "hot_search": 1} and any("weekly" in e for e in snap["errors"])
    assert snap["phrases"]["钓鱼佬"]["sources"] == ["热搜"] and snap["phrases"]["整活大赏"]["sources"] == ["热门标题"]
    assert "12345" not in snap["phrases"] and "牢" not in snap["phrases"]
    with pytest.raises(BiliError):
        hot.crawl(FakeBili(), log=lambda *a: None)


def test_snapshot_history_heat_and_expiry(tmp_path):
    d = tmp_path / "hot"
    for n, cnt in ((20, 9), (3, 2), (2, 0), (1, 4), (0, 6)):
        snap = {"date": day(n), "phrases": {"整活大赏": {"count": cnt, "sources": ["热门标题"]}} if cnt else {}}
        hot.save_snapshot(d, snap)
    hist = hot.load_history(d)
    assert day(20) not in hist and list(hist) == [day(3), day(2), day(1), day(0)]      # 超过 14 天的清掉
    h, seen = hot.heat("整活大赏", hist, TODAY)
    assert seen == 3 and h > 0
    rising = {day(0): {"x": 6}, day(1): {"x": 0}}                 # 今天火、昨天没有
    falling = {day(0): {"x": 0}, day(1): {"x": 6}}                # 昨天火、今天没了：越近权重越大 → 热度更低
    assert hot.heat("x", rising, TODAY)[0] > hot.heat("x", falling, TODAY)[0] > 0 == hot.heat("x", {}, TODAY)[0]
    assert hot.heat("x", {day(9): {"x": 99}}, TODAY) == (0.0, 0)                        # 窗口外不算


# ---------------------------------------------------------------- 建库
CFG = {"lore": {"memes": ["牢猫"], "catchphrases": ["晚上好人类酱"]}, "taboo": ["政治"], "memes": {"min_heat": 0.5}}
HIST = {day(0): {"整活大赏": 6, "政治梗": 5, "过气梗": 0, "新热词": 3}, day(1): {"整活大赏": 5, "过气梗": 0, "新热词": 2}}
MEM = {"lore": [{"text": "钓鱼佬", "sessions": 3, "speech": 5, "danmaku": 20, "last_seen": "20261108-2000"},
                {"text": "手动梗", "status": "manual", "note": "来自后台", "speech": 0, "danmaku": 0}]}


def lib_of(**kw):
    args = dict(cfg=CFG, decisions={}, memory=MEM, history=HIST, today=TODAY, stats={}, stream_recent={"钓鱼佬": 4})
    args.update(kw)
    return {e["text"]: e for e in build_library(**args)}


def test_build_library_sources_status_and_filters():
    lib = lib_of()
    assert lib["牢猫"]["status"] == "approved" and lib["牢猫"]["evergreen"] and lib["牢猫"]["sources"] == ["主播档案"]
    assert lib["手动梗"]["status"] == "approved" and lib["手动梗"]["meaning"] == "来自后台"
    assert lib["钓鱼佬"]["status"] == "pending" and lib["钓鱼佬"]["sources"] == ["弹幕 / 转写"] and lib["钓鱼佬"]["heat"] == 4
    assert lib["整活大赏"]["status"] == "pending" and lib["整活大赏"]["sources"] == ["B站热门"] and lib["整活大赏"]["heat"] > 0.5
    assert lib["政治梗"]["status"] == "blacklisted" and lib["政治梗"]["blocked_by"] == "政治"   # 禁区话题直接拉黑
    assert "过气梗" not in lib                                                                  # 从没出现过的不进库
    lib2 = lib_of(decisions={"meme_blacklist": ["晚上好"]})
    assert lib2["晚上好人类酱"]["status"] == "blacklisted"                                       # 黑名单是“包含”匹配


def test_decisions_override_and_manual_entries():
    dec = {"memes": {"整活大赏": {"status": "approved", "meaning": "把事情搞砸后自嘲", "level": 1, "usage": "翻车时用", "tags": ["翻车"]},
                     "钓鱼佬": {"status": "disabled"},
                     "新梗x": {"manual": True, "text": "新梗X", "status": "approved", "meaning": "手动加的"}}}
    lib = lib_of(decisions=dec)
    e = lib["整活大赏"]
    assert e["status"] == "approved" and e["meaning"] == "把事情搞砸后自嘲" and e["usage"] == "翻车时用" and e["tags"] == ["翻车"]
    assert lib["钓鱼佬"]["status"] == "disabled"
    assert lib["新梗X"]["status"] == "approved" and lib["新梗X"]["sources"] == ["手动添加"]
    # 后台就算“通过”了，命中禁区的仍然拉黑
    assert lib_of(decisions={"memes": {"政治梗": {"status": "approved"}}})["政治梗"]["status"] == "blacklisted"


def test_cooling_and_retention_penalty():
    quiet = {day(0): {"冷门梗": 0, "x": 1}, day(1): {"冷门梗": 1}, day(2): {"冷门梗": 0}, day(3): {"冷门梗": 0}, day(4): {"冷门梗": 0}, day(5): {"冷门梗": 0}}
    lib = lib_of(history=quiet, memory={"lore": []}, stream_recent={},
                 decisions={"memes": {"冷门梗": {"status": "approved", "meaning": "m"}, "钓鱼佬": {"status": "approved"}}}, stats={})
    assert lib["冷门梗"]["cooling"] and lib["冷门梗"]["weight"] == 0.3          # 热度跌破阈值 → 降权
    assert lib["牢猫"]["weight"] == 1.0                                            # 主播自己的梗不衰减
    lib = lib_of(decisions={"memes": {"整活大赏": {"status": "approved"}}},
                 stats={"整活大赏": {"uses": 6, "judged": 6, "kept": 1, "retention": 0.17}})
    assert lib["整活大赏"]["low_retention"] and lib["整活大赏"]["weight"] == 0.5
    lib = lib_of(decisions={"memes": {"整活大赏": {"status": "approved"}}},
                 stats={"整活大赏": {"uses": 3, "judged": 3, "kept": 0, "retention": 0.0}})
    assert not lib["整活大赏"].get("low_retention") and lib["整活大赏"]["weight"] == 1.0     # 没攒够不惩罚


def test_meme_stats(tmp_path):
    def put(sess, n, draft_memes, final_memes, human):
        d = tmp_path / sess / "edits"
        d.mkdir(parents=True, exist_ok=True)
        mk = lambda ms: {"items": [{"id": f"i{i}", "type": "text", "start": i * 20, "meme": m} for i, m in enumerate(ms)]}
        (d / f"{n:02d}.ai.json").write_text(json.dumps({"edit": mk(draft_memes)}), encoding="utf-8")
        fin = mk(final_memes)
        if human:
            fin["human_saved_at"] = "2026-11-10T10:00:00"
        (d / f"{n:02d}.json").write_text(json.dumps(fin), encoding="utf-8")

    put("s1", 1, ["A", "B"], ["A"], True)             # A 留了，B 被删
    put("s1", 2, ["A"], [], True)                       # A 被删
    put("s2", 1, ["A"], ["A"], False)                   # 人没看过：只算使用，不算保留
    (tmp_path / "s3" / "edits").mkdir(parents=True)
    (tmp_path / "s3" / "edits" / "01.ai.json").write_text("{坏的", encoding="utf-8")
    st = meme_stats(tmp_path)
    assert st["a"] == {"uses": 3, "judged": 2, "kept": 1, "retention": 0.5}
    assert st["b"]["uses"] == 1 and st["b"]["retention"] == 0.0
    assert meme_stats(tmp_path / "nope") == {}


# ---------------------------------------------------------------- 检索
class FakeEmb:
    """按关键词分桶的假向量：钓鱼 / 内衣 / 翻车 / 其他。"""
    available = True

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        keys = ["钓鱼", "内衣", "翻车"]
        m = np.array([[t.count(k) for k in keys] + [0.1] for t in texts], dtype=np.float32)
        return m / np.linalg.norm(m, axis=1, keepdims=True)


LIB = [
    {"text": "钓鱼佬", "status": "approved", "level": 1, "meaning": "总在钓鱼的人", "usage": "钓鱼相关", "tags": [], "weight": 1.0},
    {"text": "内衣梗", "status": "approved", "level": 1, "meaning": "寄内衣的名场面", "usage": "", "tags": [], "weight": 1.0},
    {"text": "翻车了", "status": "approved", "level": 1, "meaning": "翻车", "usage": "翻车时", "tags": [], "weight": 0.3},
    {"text": "待定梗", "status": "pending", "level": 1, "meaning": "钓鱼钓鱼", "weight": 1.0},
    {"text": "谨慎梗", "status": "approved", "level": 3, "meaning": "钓鱼钓鱼", "weight": 1.0},
    {"text": "政治钓鱼", "status": "approved", "level": 1, "meaning": "钓鱼", "weight": 1.0},
]


def test_usable_filters():
    names = [e["text"] for e in usable(LIB, {}, ["政治"])]
    assert names == ["钓鱼佬", "内衣梗", "翻车了"]          # 待确认 / 尺度过高 / 禁区 都不给


def test_retrieve_vector_threshold_and_weight():
    rows, method = retrieve("主播今天钓鱼钓了一整晚", LIB, {}, embedder=FakeEmb(), taboo=["政治"])
    assert method == "vector" and [r["text"] for r in rows] == ["钓鱼佬"]            # 只给相符的，宁缺毋滥
    rows, _ = retrieve("钓鱼翻车了，翻车", LIB, {"memes": {"min_sim": 0.2}}, embedder=FakeEmb(), taboo=["政治"])
    by = {r["text"]: r for r in rows}
    assert by["翻车了"]["score"] == pytest.approx(by["翻车了"]["sim"] * 0.3, abs=1e-3)   # 降温的梗乘权重
    assert rows[0]["text"] == "钓鱼佬" or rows[0]["score"] >= rows[-1]["score"]
    assert retrieve("", LIB, {}, embedder=FakeEmb())[1] == "none" and retrieve("钓鱼", [], {}, embedder=FakeEmb())[1] == "none"


def test_retrieve_lexical_fallback(monkeypatch):
    for k in ("DASHSCOPE_API_KEY",):
        monkeypatch.delenv(k, raising=False)
    emb = Embedder(api_key="")
    assert not emb.available
    rows, method = retrieve("主播又在钓鱼佬的路上寄了内衣梗", LIB, {}, embedder=emb)
    assert method == "lexical" and {r["text"] for r in rows} >= {"钓鱼佬", "内衣梗"}
    assert lexical_scores("abc", ["xyz"])[0] == 0.0


def test_retrieve_embed_error_falls_back():
    class Broken(FakeEmb):
        def embed(self, texts):
            raise EmbedError("断网")
    logs = []
    rows, method = retrieve("钓鱼佬", LIB, {}, embedder=Broken(), log=logs.append)
    assert method == "lexical" and rows and any("退回字面匹配" in x for x in logs)


def test_situation_and_offer_text():
    llm = {"what_happened": "舰礼寄了内衣", "story": {"setup": "说寄舰礼", "punch": "寄了内衣", "reaction": "弹幕哈哈"}}
    assert situation_text(llm) == "舰礼寄了内衣。说寄舰礼。寄了内衣。弹幕哈哈"
    assert situation_text({}, extra="弹幕样本") == "弹幕样本"
    assert "没有和这段情境相符" in offer_text([])
    t = offer_text([{"text": "钓鱼佬", "meaning": "总在钓鱼", "usage": "钓鱼时", "level": 2}])
    assert "《钓鱼佬》" in t and "含义：总在钓鱼" in t and "尺度：圈层" in t


# ---------------------------------------------------------------- 剪辑脚本里的梗
def item(i, start, meme=None, t="text"):
    d = {"id": f"i{i}", "type": t, "start": start, "duration": 2}
    if t == "text":
        d["text"] = "花字"
    elif t == "sticker":
        d["asset"] = "a.png"
    if meme:
        d["meme"] = meme
        d["meme_reason"] = "因为好笑"
    return d


def test_validate_item_keeps_meme_only_on_text_and_sticker():
    v = validate_item(item(1, 3, "钓鱼佬"), 60)
    assert v["meme"] == "钓鱼佬" and v["meme_reason"] == "因为好笑"
    assert validate_item(item(2, 3, "钓鱼佬", "sticker"), 60)["meme"] == "钓鱼佬"
    z = validate_item({"type": "zoom", "start": 3, "meme": "钓鱼佬"}, 60)
    assert "meme" not in z
    assert "meme" not in validate_item({**item(3, 3), "meme": "  "}, 60)
    assert len(validate_item({**item(4, 3), "meme": "x" * 99, "meme_reason": "y" * 200}, 60)["meme"]) == 40
    e = validate_edit({"trim": {"start": 0, "end": 50}, "items": [item(1, 3, "钓鱼佬")]}, 60)
    assert e["items"][0]["meme"] == "钓鱼佬"                  # 后台保存一遍不会把梗标记丢掉


def test_enforce_memes():
    edit = {"items": [item(1, 5, "钓鱼佬"), item(2, 12, "内衣梗"), item(3, 25, "编的"), item(4, 40, "内衣梗"), item(5, 60, "钓鱼佬")]}
    out, notes = enforce_memes(edit, {"钓鱼佬", "内衣梗"}, max_per_clip=2, min_gap=15)
    ids = [i["id"] for i in out["items"]]
    assert ids == ["i1", "i3", "i4"]                           # i2 离上一个才 7 秒、i5 已经第 3 个 → 整个效果去掉
    assert "meme" not in out["items"][1] and out["items"][2]["meme"] == "内衣梗"      # i3 的梗不在可用列表 → 只去标记，花字留着
    assert len(notes) == 3 and any("不在可用的梗里" in n for n in notes) and sum("太密" in n for n in notes) == 2
    assert [m["meme"] for m in memes_in_edit(out)] == ["钓鱼佬", "内衣梗"]
    same, none = enforce_memes({"items": [item(1, 5)]}, set())
    assert same["items"][0]["id"] == "i1" and none == []


# ---------------------------------------------------------------- 脚本 + 剪辑初稿
class FakeServer:
    """同时假冒 /embeddings 和 /chat/completions。"""

    def __init__(self):
        self.chat = []
        api = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path.endswith("/embeddings"):
                    keys = ["钓鱼", "内衣", "翻车"]
                    out = {"data": [{"index": i, "embedding": [t.count(k) for k in keys] + [0.1]} for i, t in enumerate(body["input"])]}
                else:
                    api.chat.append(body)
                    out = {"choices": [{"message": {"content": json.dumps(api.edit(body), ensure_ascii=False)}}],
                           "usage": {"prompt_tokens": 100, "completion_tokens": 10}}
                b = json.dumps(out, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def edit(self, body=None):
        prompt = json.dumps(body or {}, ensure_ascii=False)
        if "克制版：" in prompt:                              # 多版本：克制版想加 4 个，应被限制到 3 个
            return {"trim": {"start": 2, "end": 50}, "note": "克制", "items": [
                {"type": "text", "start": 10 * i, "duration": 2, "text": f"字{i}"} for i in range(1, 5)]}
        if "热闹版：" in prompt:                              # 热闹版加 9 个
            return {"trim": {"start": 2, "end": 50}, "note": "热闹", "items": [
                {"type": "text", "start": 4 * i, "duration": 2, "text": f"闹{i}"} for i in range(1, 10)]}
        return {"trim": {"start": 2, "end": 50}, "note": "n", "items": [
            {"type": "text", "start": 20, "duration": 2, "text": "钓鱼佬又来了", "meme": "钓鱼佬", "meme_reason": "主播又在钓鱼"},
            {"type": "text", "start": 24, "duration": 2, "text": "编的梗", "meme": "自己编的梗"},
            {"type": "text", "start": 35, "duration": 2, "text": "内衣梗", "meme": "内衣梗", "meme_reason": "和前面隔得近"},
            {"type": "zoom", "start": 19.8, "duration": 2, "scale": 1.6}]}


@pytest.fixture()
def world(tmp_path):
    srv = FakeServer()
    out = tmp_path / "out"
    sess = out / "20261112-2000"
    sess.mkdir(parents=True)
    st = datetime(2026, 11, 12, 20, 0, tzinfo=timezone(timedelta(hours=8)))
    stem = tmp_path / "rec" / f"录制-1724773744-{st.strftime('%Y%m%d-%H%M%S')}-000-测试"
    stem.parent.mkdir()
    stem.with_suffix(".xml").write_text(make_xml(st, 300, [(150, "钓鱼钓鱼哈哈哈")], base_rate=0.5), encoding="utf-8")
    (sess / "candidates.json").write_text(json.dumps({"delay_seconds": 5, "segments": [{"video": str(stem.with_suffix(".flv")), "offset": 0}],
        "candidates": [{"rank": 1, "t": 150, "clip_start": 120, "clip_end": 180, "score": 2.1}]}), encoding="utf-8")
    (sess / "rerank.json").write_text(json.dumps({"candidates": [{"rank": 1, "new_rank": 1, "llm": {
        "what_happened": "主播钓鱼翻车了", "punch_time": 21, "story": {"setup": "说要去钓鱼", "punch": "钓到了内衣", "reaction": "弹幕哈哈"}},
        "refined": {"start": 125.7, "end": 175}}]}), encoding="utf-8")
    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps({"memes": {"钓鱼佬": {"status": "approved", "meaning": "总在钓鱼", "level": 1},
                                               "内衣梗": {"status": "approved", "meaning": "寄内衣的名场面", "level": 1}}}), encoding="utf-8")
    cfgp = tmp_path / "kiya.yaml"
    cfgp.write_text("streamer: {name: 测试}\nlore: {memes: []}\nmemes: {min_sim: 0.5}\nautoedit: {prompt: edit_v4, max_items: 8}\n", encoding="utf-8")
    mem = out / "_memory"
    mem.mkdir()
    (mem / "memory.json").write_text(json.dumps({"lore": [{"text": "钓鱼佬", "sessions": 3, "speech": 5, "danmaku": 9},
                                                         {"text": "内衣梗", "sessions": 3, "speech": 5, "danmaku": 9}]}), encoding="utf-8")
    env = dict(os.environ, DASHSCOPE_BASE_URL=srv.url, DASHSCOPE_API_KEY="sk-test", DASHSCOPE_MODEL="fake",
               KIYA_LLM_CACHE=str(tmp_path / "cache"), KIYA_EMBED_CACHE=str(tmp_path / "ecache"), KIYA_ENV_FILE=str(tmp_path / "none.env"),
               KIYA_MEMORY_DECISIONS=str(decisions), KIYA_STICKERS=str(tmp_path / "st"))

    def run(script, *a):
        extra = ["--out", str(out)] if script == "memes.py" else []
        args = [str(ROOT / "scripts" / script), "--config", str(cfgp), *extra]
        if script == "memes.py":
            args += [str(x) for x in a]
        else:
            args = [str(ROOT / "scripts" / script), str(a[0]), *map(str, a[1:]), "--config", str(cfgp)]
        return subprocess.run([sys.executable, *args], capture_output=True, text=True, env=env)
    return type("W", (), dict(srv=srv, out=out, sess=sess, run=run, env=env, tmp=tmp_path, cfg=cfgp))


def test_memes_script_refresh_and_search(world):
    snap_dir = world.out / "_memory" / "hot"
    hot.save_snapshot(snap_dir, {"date": date.today().isoformat(), "phrases": {"整活大赏": {"count": 6, "sources": ["热搜"]}}})
    r = world.run("memes.py", "refresh", "--no-fetch")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "梗库共 3 条：已启用 2" in r.stdout and "待确认 1" in r.stdout and "文本向量：2 条已启用的梗已就绪" in r.stdout
    lib = json.loads((world.out / "_memory" / "memes.json").read_text(encoding="utf-8"))["memes"]
    assert {e["text"]: e["status"] for e in lib} == {"钓鱼佬": "approved", "内衣梗": "approved", "整活大赏": "pending"}
    r = world.run("memes.py", "search", "主播钓鱼钓了一晚上")
    assert r.returncode == 0 and "文本向量" in r.stdout and "《钓鱼佬》" in r.stdout and "《内衣梗》" not in r.stdout


def test_autoedit_uses_memes_and_enforces(world):
    assert world.run("memes.py", "refresh", "--no-fetch").returncode == 0
    r = world.run("autoedit.py", world.sess / "candidates.json", "--rank", 1)
    assert r.returncode == 0, r.stdout + r.stderr
    prompt = json.dumps(world.srv.chat[-1], ensure_ascii=False)
    assert "【可用的梗】" in prompt and "《钓鱼佬》" in prompt and "总在钓鱼" in prompt and "整活大赏" not in prompt
    e = json.loads((world.sess / "edits" / "01.json").read_text(encoding="utf-8"))
    memes = [(i["start"], i["meme"]) for i in e["items"] if i.get("meme")]
    assert memes == [(20.0, "钓鱼佬")]                                     # 编的梗、没被检索到的梗，标记都去掉（花字留着）
    assert [i["text"] for i in e["items"] if i["type"] == "text"] == ["钓鱼佬又来了", "编的梗", "内衣梗"]
    ai = json.loads((world.sess / "edits" / "01.ai.json").read_text(encoding="utf-8"))
    assert ai["meme_method"] == "vector" and [m["meme"] for m in ai["memes_offered"]] == ["钓鱼佬"] and len(ai["meme_notes"]) == 2
    assert "梗库（vector）：给了 1 个，用了 1 个：钓鱼佬" in r.stdout


def test_autoedit_without_library_strips_memes(world):
    r = world.run("autoedit.py", world.sess / "candidates.json", "--rank", 1)       # 没跑过 memes.py：没有梗库
    assert r.returncode == 0, r.stdout + r.stderr
    assert "梗库里没有和这段情境相符的梗" in json.dumps(world.srv.chat[-1], ensure_ascii=False)
    e = json.loads((world.sess / "edits" / "01.json").read_text(encoding="utf-8"))
    assert not [i for i in e["items"] if i.get("meme")]                              # 没提供梗，模型写的梗标记一律去掉
