"""阶段 2：边界修正、笑声 / 尖叫 / 音乐检测、大模型调用层（缓存、重试、校验）、精排、剪辑脚本初稿、转写抽测。
大模型换成一个假的 OpenAI 兼容服务，不花钱、不联网。
"""
import csv
import json
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from clipper.boundary import refine_bounds  # noqa: E402
from clipper.edit import density_problems, enforce_density, validate_edit  # noqa: E402
from clipper.llm import LLMClient, extract_json, fill  # noqa: E402
from clipper.peaks import laugh_counts  # noqa: E402
from fake_xml import make_xml  # noqa: E402

SR = 16000


def sents(*spans):
    return [{"t0": a, "t1": b, "text": t} for a, b, t in spans]


# ---------------------------------------------------------------- 边界修正
def test_refine_bounds():
    s = sents((90, 96, "铺垫一"), (97, 104, "铺垫二"), (105, 109, "笑点"), (110, 118, "反应"), (125, 131, "后话"),
              (60, 70, "更早的话"), (75, 85, "再早一点"))
    r = refine_bounds(s, peak=106, punch=107, start=99, end=112)
    assert r["start"] == pytest.approx(96.7)              # 99 落在“铺垫二”中间 → 提前到句首 97（减 0.3）
    assert r["end"] >= 118 + 1.5 - 1e-6                   # 112 落在“反应”中间 → 句末 118 + 余韵
    assert r["end"] - r["start"] >= 30                    # 不够 30 秒就按整句扩展
    long = refine_bounds(s, peak=106, punch=107, start=0, end=400, max_len=40)
    assert long["end"] - long["start"] <= 40 + 1e-6 and long["start"] == 0
    quiet = refine_bounds([], peak=500)                   # 没有句子：按笑点前后补足
    assert 30 <= quiet["end"] - quiet["start"] <= 40 and quiet["start"] <= 500 - 8 < 500 < quiet["end"]
    tiny = refine_bounds([], peak=500, start=495, end=503, min_len=30)
    assert tiny["end"] - tiny["start"] == pytest.approx(30) and "补足" in tiny["note"]
    setup = refine_bounds(s, peak=107, punch=107, start=106, end=115)
    assert setup["start"] <= 107 - 8                      # 至少留 8 秒铺垫


# ---------------------------------------------------------------- 笑声 / 尖叫 / 音乐
def test_laugh_counts():
    c = laugh_counts(sents((5, 9, "哈哈哈哈太好笑了"), (12, 14, "嘿嘿"), (30, 32, "正常说话"), (41, 45, "笑死我了hhh")), 6, 10)
    assert c[0] == 1 and c[1] == 1 and c[2] == 0 and c[3] == 0 and c[4] == 2


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")
def test_audio_events(tmp_path):
    from scipy.io import wavfile
    from clipper.media import audio_features
    rng = np.random.default_rng(3)

    def note(f, d):
        t = np.arange(int(SR * d)) / SR
        ph = 2 * np.pi * np.cumsum(f * (1 + 0.008 * np.sin(2 * np.pi * 5.5 * t))) / SR
        return 0.3 * (np.sin(ph) + 0.5 * np.sin(2 * ph))

    def speech(sec):
        out = []
        while sum(map(len, out)) < SR * sec:
            n = int(SR * rng.uniform(0.08, 0.22))
            f = rng.uniform(140, 320)
            out += [0.2 * np.sin(2 * np.pi * np.cumsum(np.linspace(f, f * rng.uniform(0.75, 1.3), n)) / SR),
                    np.zeros(int(SR * rng.uniform(0.03, 0.15)))]
        return np.concatenate(out)[: SR * sec]
    song = np.concatenate([note(f, d) for f, d in [(262, .6), (294, .7), (330, .5), (392, .8), (349, .6), (330, .8)] * 3])[: SR * 10]
    scream = rng.normal(0, 0.7, SR * 2)
    x = np.concatenate([speech(20), song, speech(5), scream, speech(5)])
    wav = tmp_path / "a.wav"
    wavfile.write(wav, SR, (np.clip(x, -1, 1) * 32767).astype(np.int16))
    f = audio_features(wav)
    assert len(f["db"]) == len(f["hf"]) == len(f["sustain"]) == 42
    assert f["sustain"][21:29].mean() > 0.6 and f["sustain"][:20].mean() < 0.1    # 唱歌段 vs 说话段
    assert f["hf"][35:37].mean() > 0.3 and f["hf"][:20].mean() < 0.1              # 尖叫（高频）


# ---------------------------------------------------------------- 剪辑脚本密度约束
def test_density():
    e = validate_edit({"items": [
        {"type": "zoom", "start": 10, "duration": 2}, {"type": "zoom", "start": 14, "duration": 2},
        {"type": "text", "start": 10.2, "duration": 2, "text": "A"}, {"type": "text", "start": 10.5, "duration": 2, "text": "B"},
        {"type": "zoom", "start": 25, "duration": 2}]}, 60)
    p = density_problems(e)
    assert any("同时有 3 个" in x for x in p) and any("间隔不到" in x for x in p)
    kept, dropped = enforce_density(e)
    assert [(i["type"], i["start"]) for i in kept["items"]] == [("zoom", 10), ("text", 10.2), ("zoom", 25)]
    assert len(dropped) == 2 and not density_problems(kept)


# ---------------------------------------------------------------- 假大模型服务
class FakeLLM:
    def __init__(self):
        self.calls, self.mode = [], {}
        api = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                api.calls.append(body)
                if self.headers.get("Authorization") != "Bearer sk-test":
                    return self._send(401, {"error": "bad key"})
                code, text = api.answer(body)
                if code != 200:
                    return self._send(code, {"error": "busy"})
                self._send(200, {"choices": [{"message": {"content": text}}],
                                 "usage": {"prompt_tokens": 1000, "completion_tokens": 100}})

            def _send(self, code, obj):
                b = json.dumps(obj, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def answer(self, body):
        user = body["messages"][1]["content"]
        text = user if isinstance(user, str) else " ".join(c.get("text", "") for c in user if c.get("type") == "text")
        system = body["messages"][0]["content"]
        if "综艺版" in system:                                   # 剪辑脚本初稿
            return 200, json.dumps({"trim": {"start": 2, "end": 50}, "note": "笑点集中出效果", "items": [
                {"type": "zoom", "start": 19.8, "duration": 2, "scale": 1.6, "x": 0.7, "y": 0.5, "shake": 0.5},
                {"type": "text", "start": 20, "duration": 2, "text": "发舰礼？！", "preset": "slam", "x": 0.4, "y": 0.3},
                {"type": "zoom", "start": 23, "duration": 2, "scale": 1.4},                  # 和上一个推镜太近 → 丢
                {"type": "sticker", "start": 21, "duration": 1.5, "asset": "不存在.png"},   # 不在贴图库 → 丢
                {"type": "sticker", "start": 30, "duration": 1.5, "asset": "doge.png"},
                {"type": "text", "start": 31, "duration": 2, "text": ""}]}, ensure_ascii=False)
        if "【前情" in text:                                     # 精排
            n = self.mode.get("rerank_calls", 0)
            self.mode["rerank_calls"] = n + 1
            if "高峰大约在第 30 秒" in text and "舰礼" in text:       # 第 2 个候选：真有梗
                return 200, ("好的，结论如下：```json\n" + json.dumps({
                    "funny": True, "score": 8.5, "type": "互动", "standalone": True, "what_happened": "舰礼寄了内衣",
                    "reason": "反差", "punch_time": 21, "start_time": 7, "end_time": 35,
                    "story": {"setup": "说寄舰礼", "punch": "寄了内衣", "reaction": "弹幕哈哈"},
                    "title_candidates": ["舰礼寄成了内衣！？", "x" * 60]}, ensure_ascii=False) + "\n```")
            return 200, json.dumps({"funny": False, "score": 3, "type": "未知类型", "what_happened": "打招呼",
                                    "punch_time": -1})
        # 调用层测试
        seq = self.mode.setdefault("seq", [])
        return seq.pop(0) if seq else (200, '{"ok": true, "n": 1}')


class OkOut(BaseModel):
    ok: bool
    n: int


@pytest.fixture(scope="module")
def llm():
    return FakeLLM()


def test_llm_client(llm, tmp_path):
    c = LLMClient(model="m", base_url=llm.url, api_key="sk-test", cache_dir=tmp_path)
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]
    llm.mode["seq"] = [(200, "这不是 JSON"), (200, '{"ok": true, "n": 2}')]
    r = c.chat_json(msgs, OkOut, tag="v1")
    assert r.data.n == 2 and r.attempts == 2 and not r.cached          # 不合法 → 带错误重试一次
    assert "不合法" in llm.calls[-1]["messages"][-1]["content"]
    n = len(llm.calls)
    r2 = c.chat_json(msgs, OkOut, tag="v1")
    assert r2.cached and r2.data.n == 2 and len(llm.calls) == n         # 缓存命中，不再请求
    llm.mode["seq"] = [(503, ""), (200, '{"ok": true, "n": 3}')]
    r3 = c.chat_json(msgs, OkOut, tag="v2")                              # 版本号不同：不命中缓存；503 自动重试
    assert r3.data.n == 3 and len(llm.calls) == n + 2
    llm.mode["seq"] = [(200, "{}"), (200, "仍然不对")]
    r4 = c.chat_json(msgs, OkOut, tag="v3")
    assert r4.data is None and "不合法" in r4.error                      # 两次都不合法 → 放弃
    bad = LLMClient(model="m", base_url=llm.url, api_key="wrong", cache_dir=tmp_path)
    r5 = bad.chat_json(msgs, OkOut, tag="v4")
    assert r5.data is None and "401" in r5.error and r5.attempts == 1    # 401 不重试
    assert c.total["prompt_tokens"] >= 4000
    assert extract_json('前言 {"a": [1, 2]} 后记') == {"a": [1, 2]}
    assert fill("值 {{a}} 和 {\"json\": 1}", a=5) == "值 5 和 {\"json\": 1}"


# ---------------------------------------------------------------- 精排 + 剪辑脚本初稿 + 评测
@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")
def test_rerank_autoedit_evaluate(llm, tmp_path):
    rec = tmp_path / "rec"
    rec.mkdir()
    st = datetime(2026, 11, 12, 20, 0, tzinfo=timezone(timedelta(hours=8)))
    stem = rec / f"录制-1724773744-{st.strftime('%Y%m%d-%H%M%S')}-000-测试"
    stem.with_suffix(".xml").write_text(make_xml(st, 300, [(150, "哈哈哈哈")], base_rate=0.5), encoding="utf-8")
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=5:d=300",
                    "-f", "lavfi", "-i", "sine=d=300", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    str(stem.with_suffix(".flv"))], check=True)
    sess = tmp_path / "out" / "20261112-2000"
    sess.mkdir(parents=True)
    (sess / "candidates.json").write_text(json.dumps({"delay_seconds": 5, "segments": [{"video": str(stem.with_suffix(".flv")), "offset": 0}],
        "candidates": [{"rank": 1, "t": 60, "clip_start": 30, "clip_end": 90, "score": 2.4},
                       {"rank": 2, "t": 150, "clip_start": 120, "clip_end": 180, "score": 2.1}]}), encoding="utf-8")
    (sess / "transcript.json").write_text(json.dumps({"model": "fun-asr", "sentences": sents(
        (100, 110, "前情：今天说说舰长"), (126, 133, "我当时说要寄舰礼"), (139, 143, "结果寄了一盒内衣"), (144, 150, "哈哈哈你们别笑"),
        (152, 160, "真的不是故意的"), (40, 50, "晚上好人类酱"))}, ensure_ascii=False), encoding="utf-8")
    stickers = tmp_path / "stickers"
    stickers.mkdir()
    (stickers / "doge.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    env = dict(os.environ, DASHSCOPE_BASE_URL=llm.url, DASHSCOPE_API_KEY="sk-test", DASHSCOPE_MODEL="fake-vl",
               KIYA_LLM_CACHE=str(tmp_path / "cache"), KIYA_STICKERS=str(stickers), KIYA_ENV_FILE=str(tmp_path / "none.env"),
               KIYA_MEMORY_DECISIONS=str(tmp_path / "none.json"), KIYA_MEMORY_FILE=str(tmp_path / "none-mem.json"))

    def run(*a):
        return subprocess.run([sys.executable, *map(str, a)], capture_output=True, text=True, env=env)

    r = run(ROOT / "scripts" / "rerank.py", sess / "candidates.json")
    assert r.returncode == 0, r.stdout + r.stderr
    rr = json.loads((sess / "rerank.json").read_text(encoding="utf-8"))
    order = [(c["rank"], c["new_rank"]) for c in rr["candidates"]]
    assert order == [(2, 1), (1, 2)]                                   # 大模型把第 2 名提到第 1
    top = rr["candidates"][0]
    assert top["frames"] == 8 and top["llm"]["type"] == "互动" and len(top["llm"]["title_candidates"][1]) == 40
    assert rr["candidates"][1]["llm"]["type"] == "其他"                 # 未知类型归为“其他”
    ref = top["refined"]
    assert ref["start"] == pytest.approx(125.7) and ref["end"] >= 160 + 1.5 - 1e-6   # 对齐到句首 / 句末
    sent = [b for b in llm.calls if "寄舰礼" in json.dumps(b, ensure_ascii=False)][-1]
    assert any(c.get("type") == "image_url" for c in sent["messages"][1]["content"])      # 带了截图
    assert "[-20.0] 前情：今天说说舰长" in json.dumps(sent, ensure_ascii=False)          # 前情（负秒数）
    n = len(llm.calls)
    assert run(ROOT / "scripts" / "rerank.py", sess / "candidates.json").returncode == 0
    assert len(llm.calls) == n                                         # 重跑全走缓存

    r = run(ROOT / "scripts" / "autoedit.py", sess / "candidates.json", "--rank", 2)
    assert r.returncode == 0, r.stdout + r.stderr
    e = json.loads((sess / "edits" / "02.json").read_text(encoding="utf-8"))
    assert [(i["type"], i["start"]) for i in e["items"]] == [("zoom", 19.8), ("text", 20.0), ("sticker", 30.0)]
    assert e["trim"] == {"start": 2.0, "end": 50.0} and e["generated_by"].startswith("edit-v2")
    assert not density_problems(e)
    r = run(ROOT / "scripts" / "autoedit.py", sess / "candidates.json", "--rank", 2)
    assert r.returncode == 1 and "不覆盖" in r.stderr                   # 已有脚本不覆盖
    assert run(ROOT / "scripts" / "autoedit.py", sess / "candidates.json", "--rank", 2, "--force").returncode == 0
    ai = json.loads((sess / "edits" / "02.ai.json").read_text(encoding="utf-8"))          # AI 原稿另存
    assert [(i["type"], i["start"]) for i in ai["edit"]["items"]] == [(i["type"], i["start"]) for i in e["items"]] and ai["prompt"].startswith("edit-v2") and not ai["taste_used"]
    assert "【剪辑师的习惯】\n（还没有积累）" in json.dumps(llm.calls[-1], ensure_ascii=False).replace("\\n", "\n")

    reviews = tmp_path / "reviews"
    reviews.mkdir()
    with open(reviews / "20261112-2000.csv", "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows([["序号", "片段起", "片段止", "分数", "是否有趣(Y/N)"], [1, "00:00:30", "00:01:30", "2.4", "N"],
                                 [2, "00:02:00", "00:03:00", "2.1", "Y"]])
    r = run(ROOT / "scripts" / "evaluate.py", "--rerank", "--out", tmp_path / "out", "--reviews", reviews)
    assert r.returncode == 0 and "一致 2/2 = 100%" in r.stdout, r.stdout + r.stderr


def test_asr_eval(tmp_path):
    tr = tmp_path / "t.json"
    tr.write_text(json.dumps({"model": "fun-asr", "sentences": sents((0, 3, "舰长说要寄发件礼"), (4, 6, "你们懂吗"),
                                                                       (100, 101, "范围外"))}, ensure_ascii=False), encoding="utf-8")
    ref = tmp_path / "ref.txt"
    ref.write_text("舰长说要寄舰礼，你们懂吗？", encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "asr_eval.py"), str(tr), "--start", "0", "--end", "00:00:10",
                        "--ref", str(ref)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "CER = 18.2%" in r.stdout and "舰 → 发件" in r.stdout         # 11 字里错 2 个（1 替换 + 1 多字）
