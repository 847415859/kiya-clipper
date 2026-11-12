"""跨场次呼应：转写全文索引（内存实现 + MySQL 实现的 SQL 核对）、检索与判定校验、剪辑脚本 callback 字段、
autoedit 并入标准版、渲染前先剪旧画面（假渲染器）。大模型换成假的，不联网。MySQL 用假连接核对 SQL，没有真跑 ngram。"""
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper import callbacks as cb  # noqa: E402
from clipper.edit import EditError, validate_callback, validate_edit  # noqa: E402
from clipper.transcript_index import (MemoryIndex, MySQLIndex, date_label, is_flag, open_index,  # noqa: E402
                                      rows_from_sentences, SCHEMA)


def S(*spans):
    return [{"t0": a, "t1": b, "text": t} for a, b, t in spans]


OLD = S((500, 504, "我今天再也不碰辣椒酱了"), (504, 509, "吃一口就哭了真的受不了"), (509, 512, "谁再吃谁是小狗"),
        (800, 805, "今天玩的这个游戏特别简单"), (900, 903, "嗯"))
NEW = S((40, 45, "这瓶辣椒酱真的太香了我一口气吃光"), (45, 50, "我现在已经吃了一整瓶辣椒酱了"), (60, 64, "你们不要笑我"))


def make_index():
    idx = MemoryIndex()
    idx.replace_session("kiya", "20261105-2000", OLD)
    idx.replace_session("kiya", "20261112-2000", NEW)
    idx.replace_session("kiya", "20261120-2000", S((10, 14, "后面的场次不该被检索到辣椒酱")))
    return idx


# ---------------------------------------------------------------- 索引
def test_flag_and_rows():
    assert is_flag("我今天再也不碰辣椒酱了") and is_flag("明天一定早睡") and is_flag("谁再吃谁是小狗")
    assert not is_flag("今天天气不错呀") and not is_flag("不可能")                  # 太短不算
    rows = rows_from_sentences(OLD)
    assert [r[0] for r in rows] == [0, 1, 2, 3] and rows[0][4] == 1 and rows[1][4] == 0   # “嗯”不入库；idx 保留原序号
    assert date_label("20261103-2014") == "11月3日" and date_label("乱七八糟") == "乱七八糟"


def test_memory_index_search_before_and_flag():
    idx = make_index()
    assert idx.sessions("kiya") == ["20261105-2000", "20261112-2000", "20261120-2000"]
    hits = idx.search("kiya", "辣椒酱太香了", before="20261112-2000")
    assert [h.session for h in hits] == ["20261105-2000"]                       # 本场和之后的场次都不看
    assert hits[0].text == "我今天再也不碰辣椒酱了" and hits[0].flag
    assert idx.search("kiya", "辣椒酱", before="20261112-2000", flag_only=True)[0].idx == 0
    assert idx.search("kiya", "游戏简单", before="20261112-2000", flag_only=True) == []
    assert idx.search("other", "辣椒酱") == []                                   # 主播隔离
    assert [h.idx for h in idx.neighbors("kiya", "20261105-2000", 0, 1)] == [0, 1]


def test_open_index_falls_back(tmp_path, monkeypatch):
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    root = tmp_path / "output" / "kiya"
    (root / "20261105-2000").mkdir(parents=True)
    (root / "20261105-2000" / "transcript.json").write_text(json.dumps({"sentences": OLD}, ensure_ascii=False), encoding="utf-8")
    idx, how = open_index("kiya", root)
    assert idx.kind == "files" and "没用 MySQL" in how and idx.sessions("kiya") == ["20261105-2000"]


class FakeConn:
    """记下执行过的 SQL，核对语句和参数（没有真的 MySQL）。"""
    def __init__(self, rows=None):
        self.log, self.rows = [], rows or []

    def cursor(self):
        return self

    def execute(self, sql, args=None):
        self.log.append(("x", " ".join(sql.split()), list(args) if args else None))

    def executemany(self, sql, seq):
        self.log.append(("m", " ".join(sql.split()), [list(r) for r in seq]))

    def fetchall(self):
        return self.rows


def test_mysql_index_sql():
    assert "FULLTEXT KEY" in SCHEMA and "WITH PARSER ngram" in SCHEMA and "utf8mb4" in SCHEMA and "uk_segment" in SCHEMA
    conn = FakeConn()
    idx = MySQLIndex(lambda: conn)
    assert idx.replace_session("kiya", "20261105-2000", OLD) == 4
    assert conn.log[0][1].startswith("DELETE FROM transcript_segment WHERE streamer_id=%s AND session_name=%s")
    kind, sql, rows = conn.log[1]
    assert kind == "m" and "INSERT INTO transcript_segment" in sql and len(rows) == 4 and rows[0][:3] == ["kiya", "20261105-2000", 0] and rows[0][6] == 1
    conn.rows = [("20261105-2000", 0, 500.0, 504.0, "我今天再也不碰辣椒酱了", 1, 3.2)]
    hits = idx.search("kiya", "辣椒酱，太香了！", before="20261112-2000", limit=5, flag_only=True)
    _, sql, args = conn.log[-1]
    assert "MATCH(text) AGAINST (%s IN NATURAL LANGUAGE MODE)" in sql and "session_name < %s" in sql and "flag = 1" in sql and "LIMIT %s" in sql
    assert args == ["辣椒酱太香了", "kiya", "辣椒酱太香了", "20261112-2000", 5]          # 标点被去掉，参数顺序和占位符一致
    assert hits[0].flag and hits[0].score == 3.2 and hits[0].session == "20261105-2000"
    assert idx.search("kiya", "啊") == []                                       # 太短不查库


# ---------------------------------------------------------------- 检索 + 校验
CFG = cb.settings({})


def test_queries_retrieve_passages():
    q = cb.build_queries(NEW, 30, 90, punch=48, what_happened="她吃光了一整瓶辣椒酱")
    assert q[0] in ("这瓶辣椒酱真的太香了我一口气吃光", "我现在已经吃了一整瓶辣椒酱了") and q[-1] == "她吃光了一整瓶辣椒酱" and "你们不要笑我" not in q[:1]
    hits = cb.retrieve(make_index(), "kiya", "20261112-2000", q, CFG)
    assert hits and hits[0].session == "20261105-2000" and hits[0].idx == 0
    assert not cb.retrieve(make_index(), "kiya", "20261112-2000", ["今天天气真好啊朋友们"], CFG)       # 没有共同的内容词
    ps = cb.build_passages(make_index(), "kiya", hits, CFG)
    assert len(ps) == 1 and ps[0]["id"] == "P1" and ps[0]["date"] == "11月5日" and ps[0]["flag"]
    assert [s["idx"] for s in ps[0]["sentences"]] == [0, 1, 2]                    # 前后文合并成一段，不含 300 秒外的句子
    assert "[P1] 11月5日" in cb.passages_text(ps) and "我今天再也不碰辣椒酱了" in cb.passages_text(ps)


def passages():
    return cb.build_passages(make_index(), "kiya", cb.retrieve(make_index(), "kiya", "20261112-2000", ["我现在已经吃了一整瓶辣椒酱了"], CFG), CFG)


def item(**kw):
    d = dict(passage="P1", relation="flag_payoff", quote="我今天再也不碰辣椒酱了", current_quote="我现在已经吃了一整瓶辣椒酱了", reason="说不碰又吃光", confidence=0.9)
    d.update(kw)
    return cb.CbItem(**d)


def test_verify_is_strict():
    clip = " ".join(s["text"] for s in NEW)
    ok, why = cb.verify([item()], passages(), clip, CFG)
    assert why == [] and len(ok) == 1
    c = ok[0]
    assert c["session"] == "20261105-2000" and c["start"] == 499.0 and c["end"] == pytest.approx(505.5) and c["date"] == "11月5日"
    assert c["label"] == "11月5日｜她说：「我今天再也不碰辣椒酱了」"                       # 标签是程序用原话拼的
    for bad, msg in [(item(quote="我从来没说过这句话呀"), "找不到"), (item(current_quote="她从来没吃过辣椒酱呀"), "找不到"),
                     (item(confidence=0.5), "置信度"), (item(passage="P9"), "不存在"), (item(relation="callback"), "关系类型")]:
        ok, why = cb.verify([bad], passages(), clip, CFG)
        assert ok == [] and msg in why[0], (bad, why)
    assert cb.verify([item(relation="none", quote="")], passages(), clip, CFG) == ([], [])
    long = S((10, 40, "我再也不吃辣椒酱了" + "啊" * 30))
    idx = MemoryIndex()
    idx.replace_session("kiya", "20261101-2000", long)
    ps = cb.build_passages(idx, "kiya", cb.retrieve(idx, "kiya", "20261112-2000", ["这瓶辣椒酱真的太香了"], CFG), CFG)
    ok, _ = cb.verify([item(quote="我再也不吃辣椒酱了")], ps, clip, CFG)
    assert ok[0]["end"] - ok[0]["start"] == 8.0 and len(ok[0]["label"]) <= 40         # 太长的句子只取开头一段


class FakeClient:
    def __init__(self, items):
        self.items, self.calls = items, []

    def chat_json(self, messages, schema, tag=""):
        self.calls.append(messages)
        return SimpleNamespace(data=cb.CbOut(callbacks=self.items), error=None)


def sessions_on_disk(tmp_path, footage: bool):
    root = tmp_path / "output" / "kiya"
    old = root / "20261105-2000"
    old.mkdir(parents=True)
    video = tmp_path / "old.flv"
    if footage:
        video.write_bytes(b"x")
    (old / "candidates.json").write_text(json.dumps({"segments": [{"video": str(video), "offset": 0}], "candidates": []}), encoding="utf-8")
    return root


def run_find(root, items, owner="20261112-2000#1", idx=None):
    return cb.find_callback(index=idx or make_index(), streamer="kiya", session="20261112-2000", sents=NEW, a=30, b=90, punch=48,
                            what_happened="她吃光了一整瓶辣椒酱", cfg=CFG, client=FakeClient(items),
                            prompt=("callback-v1", "系统", "{{what_happened}}{{clip_speech}}{{passages}}"), sess_root=root, owner=owner,
                            speech_text="[10s] 我现在已经吃了一整瓶辣椒酱了")


def test_find_callback_clip_card_and_used(tmp_path):
    root = sessions_on_disk(tmp_path, footage=True)
    c, notes, dbg = run_find(root, [item()])
    assert c["mode"] == "clip" and c["enabled"] and dbg["passages"] and dbg["queries"]
    cb.mark_used(root, c["session"], c["start"], "20261112-2000#1")
    c2, notes, _ = run_find(root, [item()], owner="20261112-2000#2")                    # 同一句原话已被另一条用过
    assert c2 is None and any("用过" in n for n in notes)
    assert run_find(root, [item()], owner="20261112-2000#1")[0] is not None            # 自己重新生成不算冲突
    assert cb.count_owned(root, "20261112-2000") == 1 and cb.count_owned(root, "20261112-2000", "20261112-2000#1") == 0
    c3, notes, _ = run_find(sessions_on_disk(tmp_path / "x", footage=False), [item()])
    assert c3["mode"] == "card" and any("文字卡" in n for n in notes)                  # 旧录像不在：文字卡
    assert run_find(root, [])[0] is None
    assert run_find(root, [item(confidence=0.3)])[0] is None


def test_footage_available(tmp_path):
    root = sessions_on_disk(tmp_path, footage=True)
    assert cb.footage_available(root, "20261105-2000", 499, 505)
    assert not cb.footage_available(root, "20261101-0000", 499, 505)                    # 没有这场的 candidates.json
    root2 = sessions_on_disk(tmp_path / "y", footage=False)
    assert not cb.footage_available(root2, "20261105-2000", 499, 505)


# ---------------------------------------------------------------- 剪辑脚本字段
def test_validate_callback_and_edit_check(tmp_path):
    good = {"session": "20261105-2000", "start": 499, "end": 505.5, "label": "11月5日｜她说：「再也不碰辣椒酱了」", "relation": "flag_payoff",
            "date": "11月5日", "quote": "再也不碰辣椒酱了", "confidence": 0.9}
    c = validate_callback(good)
    assert c["mode"] == "clip" and c["enabled"] is True and c["confidence"] == 0.9
    assert validate_callback(None) is None and validate_callback({}) is None
    for bad, msg in [({**good, "end": 500}, "1.5–12"), ({**good, "label": ""}, "label"), ({**good, "session": "../x"}, "session"),
                     ({**good, "relation": "x"}, "relation"), ({**good, "mode": "x"}, "mode"), ({**good, "quote": "x" * 200}, "quote")]:
        with pytest.raises(EditError, match=msg):
            validate_callback(bad)
    e = validate_edit({"items": [], "callback": good}, 60)
    assert e["callback"]["session"] == "20261105-2000" and "callback" not in validate_edit({"items": []}, 60)
    # 后台保存：旧录像不在时提醒（edit_check 按文件所在的 edits 目录找到场次）
    root = sessions_on_disk(tmp_path, footage=False)
    cur = root / "20261112-2000" / "edits"
    cur.mkdir(parents=True)
    f = cur / ".check-1.json"
    f.write_text(json.dumps({"items": [], "callback": good}), encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "edit_check.py"), str(f), "--clip-len", "60"], capture_output=True, text=True)
    out = json.loads(r.stdout)
    assert r.returncode == 0 and out["edit"]["callback"]["label"] == good["label"] and any("文字卡" in w for w in out["warnings"])


# ---------------------------------------------------------------- 渲染：先剪旧画面（假渲染器）
@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")
def test_render_callback(tmp_path):
    def mkvideo(path, secs):
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=640x360:r=10:d={secs}",
                        "-f", "lavfi", "-i", "sine=d=%d" % secs, "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(path)], check=True)

    old_video, new_video = tmp_path / "old.flv", tmp_path / "new.flv"
    mkvideo(old_video, 60)
    mkvideo(new_video, 120)
    root = tmp_path / "output" / "kiya"
    old, sess = root / "20261105-2000", root / "20261112-2000"
    (sess / "edits").mkdir(parents=True)
    old.mkdir(parents=True)
    (old / "candidates.json").write_text(json.dumps({"segments": [{"video": str(old_video), "offset": 0}], "candidates": []}), encoding="utf-8")
    words = lambda s, t0: [[t0 + i * 0.3, t0 + (i + 1) * 0.3, ch] for i, ch in enumerate(s)]
    (old / "transcript.json").write_text(json.dumps({"sentences": [{"t0": 10, "t1": 13, "text": "", "words": words("我再也不碰辣椒酱了", 10)}]}, ensure_ascii=False), encoding="utf-8")
    (sess / "candidates.json").write_text(json.dumps({"segments": [{"video": str(new_video), "offset": 0}],
        "candidates": [{"rank": 3, "t": 60, "clip_start": 30, "clip_end": 90}]}), encoding="utf-8")
    (sess / "transcript.json").write_text(json.dumps({"sentences": [{"t0": 41, "t1": 44, "text": "", "words": words("辣椒酱真香啊", 41)}]}, ensure_ascii=False), encoding="utf-8")
    label = "11月5日｜她说：「我再也不碰辣椒酱了」"
    (sess / "edits" / "03.json").write_text(json.dumps({
        "trim": {"start": 5, "end": 25}, "subtitles": {"enabled": True, "style": {"font_size": 70}, "style_name": "测试"},
        "items": [{"type": "text", "start": 12, "duration": 2, "text": "打脸！"}],
        "callback": {"session": "20261105-2000", "start": 10, "end": 15, "label": label, "date": "11月5日", "relation": "flag_payoff"}}, ensure_ascii=False), encoding="utf-8")
    fake = tmp_path / "fake_render.py"
    fake.write_text("import sys, shutil, os\njob, props, out = sys.argv[1:4]\nshutil.copy(os.path.join(job, 'source.mp4'), out)\n", encoding="utf-8")
    env = dict(os.environ, KIYA_RENDER_CMD=f"{sys.executable} {fake}", KIYA_OVERRIDES=str(tmp_path / "none.json"),
               KIYA_STICKERS=str(tmp_path / "st"))

    def render():
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "render.py"), str(sess / "candidates.json"), "--rank", "3", "--height", "360"],
                           capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stdout + r.stderr
        return r.stdout, json.loads((sess / "render" / "job_03" / "props.json").read_text(encoding="utf-8"))

    out, props = render()
    c = props["callback"]
    assert "呼应：开头插 11月5日 的画面" in out and c["label"] == label and c["video"] == "callback.mp4"
    assert 4.7 <= c["duration"] <= 5.3 and props["mainDuration"] == 20 and props["duration"] == pytest.approx(20 + c["duration"], abs=0.01)
    assert (sess / "render" / "job_03" / "callback.mp4").exists()
    ev = c["subtitles"]["events"]
    assert c["subtitles"]["enabled"] and ev and ev[0]["t0"] == pytest.approx(0.0, abs=0.01) and "再也不碰" in ev[0]["text"]     # 旧字幕时间从旧画面开头算
    assert [i["type"] for i in props["items"]] == ["text"]                          # 正片效果的时间不受前情影响
    assert props["items"][0]["start"] == 7.0
    # 旧录像被移走：改成开头的文字卡，日志里说清楚，不悄悄省掉
    old_video.unlink()
    shutil.rmtree(sess / "render")
    out, props = render()
    assert "改放文字卡" in out and props["callback"] is None and props["duration"] == 20
    card = props["items"][0]
    assert card["type"] == "fakeui" and card["title"] == "前情提要" and card["text"] == label and card["start"] == 0.3


# ---------------------------------------------------------------- autoedit 并入标准版
class FakeLLM:
    def __init__(self):
        self.calls = []
        api = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                api.calls.append(body)
                system = body["messages"][0]["content"]
                if "flag_payoff" in system:                                # 呼应判定
                    out = {"callbacks": [{"passage": "P1", "relation": "flag_payoff", "quote": "我今天再也不碰辣椒酱了",
                                          "current_quote": "我现在已经吃了一整瓶辣椒酱了", "reason": "说不碰又吃光", "confidence": 0.92}]}
                else:                                                      # 剪辑初稿
                    out = {"trim": {"start": 2, "end": 50}, "note": "ok", "items": [{"type": "text", "start": 20, "duration": 2, "text": "香！", "preset": "pop"}]}
                b = json.dumps({"choices": [{"message": {"content": json.dumps(out, ensure_ascii=False)}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.mark.parametrize("footage", [True, False])
def test_autoedit_merges_callback(tmp_path, footage):
    llm = FakeLLM()
    root = tmp_path / "output" / "kiya"
    old, sess = root / "20261105-2000", root / "20261112-2000"
    old.mkdir(parents=True)
    sess.mkdir(parents=True)
    old_video = tmp_path / "old.flv"
    if footage:
        old_video.write_bytes(b"x")
    (old / "candidates.json").write_text(json.dumps({"segments": [{"video": str(old_video), "offset": 0}], "candidates": []}), encoding="utf-8")
    (old / "transcript.json").write_text(json.dumps({"sentences": OLD}, ensure_ascii=False), encoding="utf-8")
    (sess / "transcript.json").write_text(json.dumps({"sentences": NEW}, ensure_ascii=False), encoding="utf-8")
    (sess / "candidates.json").write_text(json.dumps({"segments": [{"video": str(tmp_path / "new.flv"), "offset": 0}],
        "candidates": [{"rank": 1, "t": 48, "clip_start": 30, "clip_end": 90, "score": 2.0}]}), encoding="utf-8")
    stickers = tmp_path / "stickers"
    stickers.mkdir()
    env = dict(os.environ, DASHSCOPE_BASE_URL=llm.url, DASHSCOPE_API_KEY="sk-test", DASHSCOPE_MODEL="fake", KIYA_LLM_CACHE=str(tmp_path / "cache"),
               KIYA_STICKERS=str(stickers), KIYA_ENV_FILE=str(tmp_path / "none.env"), KIYA_MEMORY_DECISIONS=str(tmp_path / "none.json"),
               KIYA_MEMORY_FILE=str(tmp_path / "none-mem.json"), KIYA_MUSIC=str(tmp_path / "nomusic"))
    env.pop("DB_PASSWORD", None)
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "autoedit.py"), str(sess / "candidates.json"), "--rank", "1"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "呼应检索（直接读 transcript.json" in r.stdout and "11月5日｜她说：「我今天再也不碰辣椒酱了」" in r.stdout
    e = json.loads((sess / "edits" / "01.json").read_text(encoding="utf-8"))
    label = "11月5日｜她说：「我今天再也不碰辣椒酱了」"
    if footage:
        c = e["callback"]
        assert c["mode"] == "clip" and c["session"] == "20261105-2000" and c["label"] == label and c["relation"] == "flag_payoff"
        assert [i["type"] for i in e["items"]] == ["text"]
    else:                                                                    # 旧录像不在：开头一张文字卡，没有 callback 字段
        assert "callback" not in e
        card = next(i for i in e["items"] if i["type"] == "fakeui")
        assert card["title"] == "前情提要" and card["text"] == label
    used = json.loads((root / "_callbacks_used.json").read_text(encoding="utf-8"))
    assert used == {"20261105-2000@499": "20261112-2000#1"}
    assert any("flag_payoff" in c["messages"][0]["content"] for c in llm.calls)
    # 重跑（--force）不被自己占用的原话挡住；已有两条以上时才限流
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "autoedit.py"), str(sess / "candidates.json"), "--rank", "1", "--force"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0 and "找到" in r.stdout


def test_callbacks_script_no_llm(tmp_path):
    root = tmp_path / "output" / "kiya"
    old, sess = root / "20261105-2000", root / "20261112-2000"
    old.mkdir(parents=True)
    sess.mkdir(parents=True)
    (old / "transcript.json").write_text(json.dumps({"sentences": OLD}, ensure_ascii=False), encoding="utf-8")
    (sess / "transcript.json").write_text(json.dumps({"sentences": NEW}, ensure_ascii=False), encoding="utf-8")
    (sess / "candidates.json").write_text(json.dumps({"segments": [], "candidates": [{"rank": 1, "t": 48, "clip_start": 30, "clip_end": 90}]}), encoding="utf-8")
    env = dict(os.environ, KIYA_ENV_FILE=str(tmp_path / "none.env"))
    env.pop("DB_PASSWORD", None)
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "callbacks.py"), str(sess / "candidates.json"), "--rank", "1", "--no-llm"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "查询句" in r.stdout and "[P1] 11月5日" in r.stdout and "我今天再也不碰辣椒酱了" in r.stdout
    assert not (sess / "edits").exists()                                   # 只看不写


def test_index_script_without_mysql(tmp_path):
    root = tmp_path / "output" / "kiya"
    d = root / "20261105-2000"
    d.mkdir(parents=True)
    (d / "transcript.json").write_text(json.dumps({"sentences": OLD}, ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ, KIYA_ENV_FILE=str(tmp_path / "none.env"))
    env.pop("DB_PASSWORD", None)
    script = str(ROOT / "scripts" / "transcript_index.py")
    r = subprocess.run([sys.executable, script, "index", str(d), "--soft"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and "没有写入全文索引" in r.stdout                  # 转写后自动入库：库不可用不让任务失败
    r = subprocess.run([sys.executable, script, "index", str(d)], capture_output=True, text=True, env=env)
    assert r.returncode == 1 and ("DB_PASSWORD" in r.stderr or "pymysql" in r.stderr)      # 手动运行：明确报错并说怎么办
    r = subprocess.run([sys.executable, script, "search", "辣椒酱", "--streamer", "kiya"], capture_output=True, text=True,
                       env=env)
    assert r.returncode == 0 and "检索方式：直接读 transcript.json" in r.stdout
