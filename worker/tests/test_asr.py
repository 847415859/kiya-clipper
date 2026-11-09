"""语音转写流程测试：假的百炼接口 + 假的 rclone，不花钱、不联网。

覆盖：整场多分段转写并按分段偏移拼时间轴、缓存跳过、候选片段附上主播原话（candidates.json / peaks.csv）、
try_asr.py 单文件试用、识别失败的分段被如实报告。
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
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_xml import make_xml  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")

SEG = 120
START = datetime(2026, 11, 12, 20, 0, 0, tzinfo=timezone(timedelta(hours=8)))
KEY = "sk-test-not-real"


class Api:
    """记录提交的请求；第 fail_index 个文件返回识别失败。"""

    def __init__(self):
        self.requests, self.tasks, self.fail_index = [], {}, None
        handler = self._handler()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.host = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _handler(self):
        api = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj, code=200):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if self.headers.get("Authorization") != f"Bearer {KEY}":
                    return self._send({"code": "InvalidApiKey"}, 401)
                if self.path.endswith("/services/audio/asr/transcription"):
                    assert self.headers.get("X-DashScope-Async") == "enable"
                    req = json.loads(body)
                    api.requests.append(req)
                    tid = f"t{len(api.requests)}"
                    api.tasks[tid] = {"urls": req["input"]["file_urls"], "polls": 0}
                    return self._send({"output": {"task_id": tid, "task_status": "PENDING"}})
                tid = self.path.rsplit("/", 1)[-1]
                t = api.tasks[tid]
                t["polls"] += 1
                if t["polls"] < 2:
                    return self._send({"output": {"task_id": tid, "task_status": "RUNNING"}})
                results = []
                for i, u in enumerate(t["urls"]):
                    if i == api.fail_index:
                        results.append({"file_url": u, "subtask_status": "FAILED", "code": "FILE_DOWNLOAD_FAILED",
                                        "message": "mock"})
                    else:
                        results.append({"file_url": u, "subtask_status": "SUCCEEDED",
                                        "transcription_url": f"{api.host}/res?i={i}"})
                # 真实接口的结果顺序不保证，这里故意倒序，验证按 file_url 对应
                return self._send({"output": {"task_id": tid, "task_status": "SUCCEEDED",
                                              "results": results[::-1]}})

            def do_GET(self):
                i = int(parse_qs(urlparse(self.path).query)["i"][0])
                sents = [
                    {"begin_time": 5000, "end_time": 7000, "text": f"第{i + 1}段开头人类酱晚上好",
                     "words": [{"begin_time": 5000, "end_time": 5600, "text": "人类酱", "punctuation": ""}]},
                    {"begin_time": 60000, "end_time": 63000, "text": f"第{i + 1}段中间舰长寄来律师函",
                     "words": [{"begin_time": 60000, "end_time": 61000, "text": "律师函", "punctuation": "！"}]},
                ]
                self._send({"file_url": "x", "transcripts": [{"channel_id": 0, "sentences": sents}]})

        return H


@pytest.fixture(scope="module")
def api():
    a = Api()
    yield a
    a.server.shutdown()


@pytest.fixture(scope="module")
def env(tmp_path_factory, api):
    d = tmp_path_factory.mktemp("bin")
    log = d / "rclone.log"
    fake = d / "rclone"
    fake.write_text(f"""#!/bin/sh
echo "$@" >> "{log}"
case "$1" in
  copyto) mkdir -p "{d}/oss" && cp "$2" "{d}/oss/$(basename "$3")";;
  link) echo "http://example.invalid/$(basename "$4")?Expires=1&Signature=x";;
  deletefile) rm -f "{d}/oss/$(basename "$2")";;
esac
""")
    fake.chmod(0o755)
    e = dict(os.environ, RCLONE=str(fake), DASHSCOPE_API_KEY=KEY, OSS_BUCKET="kiya-clipper-rec",
             OSS_ENDPOINT_INTERNAL="oss-cn-beijing-internal.aliyuncs.com", DASHSCOPE_API_HOST=api.host)
    return e, log, d


@pytest.fixture(scope="module")
def rec(tmp_path_factory):
    root = tmp_path_factory.mktemp("rec")
    room = root / "1724773744-娜由纪雅kiya"
    room.mkdir()
    for i in range(2):
        st = START + timedelta(seconds=i * SEG)
        name = f"录制-1724773744-{st.strftime('%Y%m%d-%H%M%S')}-000-测试"
        (room / f"{name}.xml").write_text(make_xml(st, SEG, [(55, "哈哈哈哈律师函")], base_rate=1.0, seed=i),
                                          encoding="utf-8")
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"sine=f=440:r=16000:d={SEG}", "-f", "lavfi", "-i", f"color=s=64x36:r=2:d={SEG}",
                        "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                        str(room / f"{name}.flv")], check=True)
    return root


def run(script, *args, env):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / script), *map(str, args)],
                          capture_output=True, text=True, env=env)


def test_transcribe_session(rec, env, api, tmp_path_factory):
    e, log, d = env
    out = tmp_path_factory.mktemp("out")
    r = run("transcribe.py", rec, "--latest", "--out", out, "--model", "paraformer-v2", env=e)
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads((out / "20261112-2000" / "transcript.json").read_text(encoding="utf-8"))
    assert data["model"] == "paraformer-v2" and abs(data["duration"] - 2 * SEG) < 3
    assert data["cost"] == pytest.approx(2 * SEG * 0.00008, abs=0.001)
    t = [(s["t0"], s["text"][:3]) for s in data["sentences"]]
    assert t == [(5.0, "第1段"), (60.0, "第1段"), (SEG + 5.0, "第2段"), (SEG + 60.0, "第2段")]   # 第 2 段加了偏移
    assert data["sentences"][3]["words"][0] == [SEG + 60.0, SEG + 61.0, "律师函！"]
    # 一个任务提交整场；带热词表 / 语言参数；OSS 临时文件已清理；签名链接走公网 endpoint
    req = api.requests[-1]
    assert len(req["input"]["file_urls"]) == 2 and req["parameters"]["language_hints"] == ["zh", "en", "ja"]
    assert "vocabulary_id" not in req["parameters"]
    assert not list((d / "oss").glob("*.mp3"))
    assert "oss,endpoint=oss-cn-beijing.aliyuncs.com:kiya-clipper-rec/asr-tmp/" in log.read_text()
    assert KEY not in r.stdout + r.stderr
    assert "人类酱" in (out / "20261112-2000" / "transcript.txt").read_text(encoding="utf-8")
    assert not [p for p in (out / "20261112-2000").iterdir() if p.name.startswith("asr-")]   # 临时音频已删

    # 已转写的场次跳过
    n = len(api.requests)
    r = run("transcribe.py", rec, "--latest", "--out", out, env=e)
    assert r.returncode == 0 and "跳过" in r.stdout and len(api.requests) == n

    # 粗筛后候选带上主播原话
    r = run("screen.py", rec, "--latest", "--out", out, "--danmaku-only", "--top", "5", env=e)
    assert r.returncode == 0 and "已附上主播原话" in r.stdout, r.stdout + r.stderr
    cj = json.loads((out / "20261112-2000" / "candidates.json").read_text(encoding="utf-8"))
    assert cj["transcript"] == "paraformer-v2"
    hit = [c for c in cj["candidates"] if any("律师函" in s["text"] for s in c["speech"])]
    assert hit, cj["candidates"]
    with open(out / "20261112-2000" / "peaks.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert "主播原话" in rows[0] and any("律师函" in r["主播原话"] for r in rows)


def test_failed_segment_reported(rec, env, api, tmp_path_factory):
    e, _, _ = env
    out = tmp_path_factory.mktemp("out2")
    api.fail_index = 1
    try:
        r = run("transcribe.py", rec, "--latest", "--out", out, "--model", "fun-asr", env=e)
    finally:
        api.fail_index = None
    assert r.returncode == 1 and "1 个分段失败" in r.stdout, r.stdout + r.stderr
    assert "language_hints" not in api.requests[-1]["parameters"]       # fun-asr 不传 language_hints
    data = json.loads((out / "20261112-2000" / "transcript.json").read_text(encoding="utf-8"))
    assert [s["ok"] for s in data["segments"]] == [True, False]
    assert all(s["t0"] < SEG for s in data["sentences"])


def test_try_asr(rec, env, tmp_path):
    e, _, _ = env
    video = next(rec.rglob("*.flv"))
    r = run("try_asr.py", video, "--start", 10, "--end", 70, "--model", "paraformer-v2", "fun-asr",
            "--out", tmp_path, env=e)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "4 小时直播约 ¥1.15" in r.stdout and "4 小时直播约 ¥3.17" in r.stdout
    run_dir = next(tmp_path.iterdir())
    assert (run_dir / "fun-asr.txt").read_text(encoding="utf-8").count("\n") == 2


def test_bad_key(rec, env, tmp_path):
    e, _, _ = env
    r = run("transcribe.py", rec, "--latest", "--out", tmp_path, env=dict(e, DASHSCOPE_API_KEY="wrong"))
    assert r.returncode == 1 and "401" in r.stderr
