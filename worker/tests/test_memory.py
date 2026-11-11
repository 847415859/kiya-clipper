"""精排记忆：梗挖掘、审核例子、权重校准、B站投稿拉取（假接口）、learn.py 端到端、按新权重重排。"""
import csv
import json
import os
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper.bili import Bili, mixin_key, sign  # noqa: E402
from clipper.llm import fill, load_prompt  # noqa: E402
from clipper.memory import calibrate, context_text, mine_lore, pick_examples, session_ngrams  # noqa: E402


def test_wbi_sign():
    # B站网页端签名的公开示例
    img, sub = "7cd084941338484aae1ad9425b84077c", "4932caff0ff746eab6f01bf08b70ac45"
    assert mixin_key(img, sub) == "ea1db124af3c7062474693fa704f4ff8"
    p = sign({"foo": "114", "bar": "514", "zab": 1919810}, img, sub, 1702204169)
    assert p["w_rid"] == "8f6f2b5b3d485fe1886cec6a0be8c5d4"


def sent(t, text):
    return {"t0": t, "t1": t + 2, "text": text}


def test_mine_lore(tmp_path):
    per = {}
    for i, name in enumerate(["20261101-2000", "20261103-2000", "20261105-2000"]):
        d = tmp_path / name
        d.mkdir()
        speech = [sent(10 * k, s) for k, s in enumerate(
            ["这个真的是律师函警告了", "我们今天聊一下这个", "律师函警告！你们别闹", "这个这个", "做局了做局了", "牢猫冲浪中",
             "独有话题" + ["芝士雪豹", "丁真珍珠", "电子烟枪"][i]] * 2)]
        dm = [(5.0, "律师函警告"), (6.0, "律师函警告hhh"), (7.0, "哈哈哈哈"), (8.0, "哈哈哈哈"), (9.0, "哈哈哈哈"),
              (10.0, "律师函警告"), (11.0, "？？？"), (12.0, "？？？"), (13.0, "？？？")]
        per[name] = session_ngrams(d, speech, dm, [(0, 30)])
        assert (d / "ngrams.json").exists()
    lore = mine_lore(per, known={"做局"}, rejected={"牢猫冲浪中"}, titles=["【kiya】律师函警告合集"], min_sessions=2)
    texts = [x["text"] for x in lore]
    assert texts[0] == "律师函警告"                      # 主播说、观众刷、标题里也有
    top = lore[0]
    assert top["speech"] and top["danmaku"] and top["in_titles"] and top["sessions"] == 3
    assert "师函警" not in texts and "律师函" not in texts   # 被更长的说法包含的子串去掉
    assert not any(t in texts for t in ("这个", "哈哈哈哈", "做局", "牢猫冲浪中"))  # 常用词 / 已知梗 / 后台删掉的
    assert not any(w in t for t in texts for w in ("芝士雪豹", "丁真珍珠", "电子烟枪"))   # 只在一场出现
    one = mine_lore({k: per[k] for k in list(per)[:1]}, set(), set(), [], min_sessions=2)
    assert one == []                                     # 只有一场：不加任何梗


def test_examples_and_context():
    ex = [{"id": f"{s}#{r}", "session": s, "rank": r, "mark": m, "note": n, "what_happened": w, "type": "", "speech": "",
           "excluded": x}
          for s, r, m, n, w, x in [("A", 1, "Y", "舰长互动", "寄了内衣", False), ("A", 2, "N", "只是念SC", "念SC", False),
                                   ("B", 1, "Y", "", "口胡", False), ("B", 3, "N", "", "打招呼", True),
                                   ("C", 1, "Y", "这场自己", "不该出现", False)]]
    got = pick_examples(ex, "C", 3, 3)
    assert {e["id"] for e in got} == {"A#1", "B#1", "A#2"}          # 不含正在精排的 C、不含后台排除的
    mem = {"version": "m-1", "examples": ex, "lore": [{"text": "律师函警告"}, {"text": "坏词", "status": "rejected"}],
           "streamer_uploads": [{"title": "【歌回】晚安", "view": 1200}],
           "type_stats": {"互动": {"n": 3, "median_view": 900, "relative": 1.8, "engage": 0.05}}}
    text, ver = context_text(mem, "C", {})
    assert ver == "m-1" and "律师函警告" in text and "坏词" not in text and "不该出现" not in text
    t2, _ = context_text(mem, "C", {}, {"rejected_lore": ["律师函警告"], "excluded_examples": ["A#1"]})
    assert "律师函警告" not in t2 and "寄了内衣" not in t2                # 后台刚删的立刻生效
    assert "（审核备注：舰长互动）" in text and "《【歌回】晚安》" in text and "是整体的 1.8 倍" in text
    v, system, user = load_prompt("rerank_v2")
    assert v == "rerank-v2" and "{{memory}}" in user
    keys = dict(profile="p", window="60", peak="30", context_before="60", before_speech="", speech="", danmaku="",
                gifts="", frames_note="", memory=text)
    assert "律师函警告" in fill(user, **keys)


def write_session(out: Path, name: str, rows: list[tuple[int, int, bool, str]], reviews: Path):
    """rows = [(序号, 大模型分, funny, Y/N)]：粗筛名次越靠前，大模型分越低（粗筛和人工口味相反）。"""
    d = out / name
    d.mkdir(parents=True)
    cands = [{"rank": r, "t": 100 * r, "clip_start": 100 * r - 30, "clip_end": 100 * r + 30, "score": 3 - r / 10}
             for r, *_ in rows]
    (d / "candidates.json").write_text(json.dumps({"segments": [], "candidates": cands}), encoding="utf-8")
    rr = [{"rank": r, "llm": {"score": s, "funny": f, "type": "互动" if s > 6 else "其他", "what_happened": f"第{r}个"},
           "clip_start": 100 * r - 30} for r, s, f, _ in rows]
    (d / "rerank.json").write_text(json.dumps({"candidates": rr}, ensure_ascii=False), encoding="utf-8")
    reviews.mkdir(exist_ok=True)
    with open(reviews / f"{name}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["序号", "片段起", "片段止", "是否有趣(Y/N)", "备注"])
        for r, _, _, m in rows:
            w.writerow([r, f"00:{(100 * r - 30) // 60:02d}:{(100 * r - 30) % 60:02d}", "", m, "备注" if m == "Y" else ""])


def make_sessions(out, reviews, n, start=0):
    for i in range(start, start + n):
        rows = [(r, 3 if r <= 10 else 8, r > 10, "Y" if r > 10 else "N") for r in range(1, 21)]
        write_session(out, f"2026110{i}-2000", rows, reviews)


def test_calibrate(tmp_path):
    out, reviews = tmp_path / "output", tmp_path / "reviews"
    make_sessions(out, reviews, 2)
    from clipper.memory import load_marks
    bad = {"llm": 0.5, "screen": 0.5, "not_funny": 1.0}
    rep = calibrate(out, load_marks(reviews), bad, min_sessions=3)
    assert rep["proposed"] is None and "还差 1 场" in rep["status"]          # 两场不够
    make_sessions(out, reviews, 1, start=2)
    rep = calibrate(out, load_marks(reviews), bad, min_sessions=3)
    assert rep["sessions"] == 3 and rep["proposed"] and rep["loo_hits"] > rep["active_hits"]
    assert rep["proposed"]["llm"] > 0.5 or rep["proposed"]["not_funny"] < 1.0


class FakeBili:
    def __init__(self):
        self.calls = []
        api = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                q = dict(urllib.parse.parse_qsl(u.query))
                api.calls.append((u.path, q, self.headers.get("Cookie")))
                if u.path == "/x/web-interface/nav":
                    d = {"wbi_img": {"img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
                                     "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png"}}
                elif u.path == "/x/space/wbi/arc/search":
                    exp = sign({k: v for k, v in q.items() if k not in ("w_rid", "wts")}, "7cd084941338484aae1ad9425b84077c",
                               "4932caff0ff746eab6f01bf08b70ac45", int(q["wts"]))["w_rid"]
                    if q.get("w_rid") != exp:
                        return self._send({"code": -403, "message": "签名错误"})
                    if q["mid"] == "646830928":
                        vl = [{"bvid": "BVkiya1", "title": "【kiya】律师函警告合集", "created": 1762700000, "play": 5000},
                              {"bvid": "BVkiya2", "title": "牢猫钓鱼实录", "created": 1762600000, "play": 3000}]
                    else:
                        vl = [{"bvid": f"BVme{i}", "title": f"切片{i}", "created": 1762000000 + i, "play": 100 * i} for i in range(1, 7)]
                    d = {"list": {"vlist": vl}}
                elif u.path == "/x/web-interface/view":
                    i = int(q["bvid"][-1])
                    d = {"title": f"切片{i}", "pubdate": 1762000000 + i, "stat": {"view": 1000 * i, "like": 10 * i, "coin": i}}
                else:
                    return self._send({"code": -404})
                # 真实接口：没登录时 nav 返回 -101，但 data 里照样有签名密钥
                self._send({"code": -101 if u.path == "/x/web-interface/nav" else 0, "message": "账号未登录", "data": d})

            def _send(self, obj):
                b = json.dumps(obj, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def test_bili_client(tmp_path):
    fb = FakeBili()
    ck = tmp_path / "ck.txt"
    ck.write_text("# Netscape HTTP Cookie File\n.bilibili.com\tTRUE\t/\tFALSE\t0\tbuvid3\tabc\n", encoding="utf-8")
    b = Bili(base=fb.url, cookie_file=str(ck), pause=0)
    ups = b.uploads(646830928, 20)
    assert [u["bvid"] for u in ups] == ["BVkiya1", "BVkiya2"] and ups[0]["view"] == 5000
    assert fb.calls[-1][2] == "buvid3=abc"
    assert b.stat("BVme3")["view"] == 3000


def test_learn_end_to_end(tmp_path):
    fb = FakeBili()
    out, reviews = tmp_path / "output", tmp_path / "reviews"
    make_sessions(out, reviews, 3)
    for i, d in enumerate(sorted(p for p in out.iterdir())):
        (d / "transcript.json").write_text(json.dumps({"sentences": [
            sent(100, "这下真的律师函警告了"), sent(200, "律师函警告你们"), sent(300, f"只在第{i}场说的话题甲乙丙")]},
            ensure_ascii=False), encoding="utf-8")
    dec = tmp_path / "decisions.json"
    dec.write_text(json.dumps({"my_uid": "123", "rejected_lore": [], "excluded_examples": ["20261100-2000#11"],
                               "manual_lore": [{"text": "QQ糖战神", "note": "粉丝起的外号"}],
                               "published": {f"BVme{i}": {"session": "20261101-2000", "rank": 10 + i} for i in range(1, 6)},
                               "weights": {"llm": 0.5, "screen": 0.5, "not_funny": 1.0}}, ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ, KIYA_BILI_API=fb.url, KIYA_MEMORY_DECISIONS=str(dec), KIYA_COOKIE_FILE=str(tmp_path / "none"),
               KIYA_ENV_FILE=str(tmp_path / "none.env"))
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "learn.py"), "--out", str(out), "--reviews", str(reviews)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    mem = json.loads((out / "_memory" / "memory.json").read_text(encoding="utf-8"))
    lore = [x["text"] for x in mem["lore"]]
    assert lore[0] == "QQ糖战神" and "律师函警告" in lore and not any("甲乙丙" in t for t in lore)
    assert mem["streamer_uploads"][0]["title"] == "【kiya】律师函警告合集"
    assert len(mem["my_uploads"]) == 6 and sum(1 for u in mem["my_uploads"] if u.get("session")) == 5
    assert mem["type_stats"] and "互动" in mem["type_stats"]          # 绑定满 5 条：按类型汇总
    assert mem["weights"]["proposed"] and "建议换权重" in r.stdout
    assert next(e for e in mem["examples"] if e["id"] == "20261100-2000#11")["excluded"]
    assert mem["version"].startswith("m-")
    v1 = mem["version"]
    # 不联网再跑：沿用上次拉到的投稿，内容不变则版本号不变
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "learn.py"), "--out", str(out), "--reviews", str(reviews),
                        "--no-fetch"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and json.loads((out / "_memory" / "memory.json").read_text(encoding="utf-8"))["version"] == v1

    # 后台确认新权重 → rerank.py --rescore 按新权重重排（不调大模型）
    d = json.loads(dec.read_text(encoding="utf-8"))
    d["weights"] = mem["weights"]["proposed"]
    dec.write_text(json.dumps(d), encoding="utf-8")
    sess = out / "20261100-2000"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "rerank.py"), str(sess / "candidates.json"), "--rescore"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    rr = json.loads((sess / "rerank.json").read_text(encoding="utf-8"))
    assert all(c["rank"] > 10 for c in rr["candidates"][:10])        # 大模型认为好笑的排到前面
