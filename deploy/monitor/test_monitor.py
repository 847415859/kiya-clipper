"""监控脚本的场景测试：假 B站接口、假 systemctl、临时录像目录，不需要真实服务器。

运行：python -m pytest -q deploy/monitor/test_monitor.py
"""
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "kiya_monitor.py"


class FakeLive:
    status = 0
    fail = False


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if FakeLive.fail:
            self.send_response(500); self.end_headers(); return
        body = json.dumps({"code": 0, "data": {"live_status": FakeLive.status, "title": "测试直播"}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def api():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/room"
    srv.shutdown()


@pytest.fixture
def env(tmp_path, api):
    rec = tmp_path / "rec"; rec.mkdir()
    sysctl = tmp_path / "systemctl"
    sysctl.write_text('#!/bin/sh\ncat "' + str(tmp_path / "svc") + '"\n'); sysctl.chmod(0o755)
    (tmp_path / "svc").write_text("active\n")
    (tmp_path / ".env").write_text("")
    FakeLive.status, FakeLive.fail = 0, False
    return {
        "KIYA_ENV_FILE": str(tmp_path / ".env"), "REC_DIR": str(rec), "KIYA_SYSTEMCTL": str(sysctl),
        "KIYA_LIVE_API": api, "KIYA_STATE_FILE": str(tmp_path / "state.json"),
        "KIYA_SYNC_LOG": str(tmp_path / "sync.log"), "KIYA_DISK_ALERT_PERCENT": "101",
        "KIYA_AUTO_ANALYZE": "0", "_tmp": str(tmp_path),
    }


def run(env):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("KIYA_", "PUSH", "SERVERCHAN", "WECOM", "DINGTALK"))}
    e.update({k: v for k, v in env.items() if not k.startswith("_")})
    r = subprocess.run([sys.executable, str(SCRIPT), "--dry-run"], capture_output=True, text=True, env=e)
    assert r.returncode == 0, r.stderr
    return [l for l in r.stdout.splitlines() if "通知：" in l]


def state(env):
    return json.loads(Path(env["KIYA_STATE_FILE"]).read_text(encoding="utf-8"))


def set_state(env, **kw):
    s = state(env); s.update(kw)
    Path(env["KIYA_STATE_FILE"]).write_text(json.dumps(s), encoding="utf-8")


def touch(path: Path, age: float = 0, content: str = "x"):
    path.write_text(content, encoding="utf-8")
    t = time.time() - age
    os.utime(path, (t, t))


def test_quiet_when_all_normal(env):
    assert run(env) == []
    assert state(env)["live"] is False if "live" in state(env) else True


def test_service_down_alert_dedup_and_recover(env):
    Path(env["_tmp"], "svc").write_text("inactive\n")
    out = run(env)
    assert len(out) == 1 and "录播服务没有运行" in out[0]
    assert run(env) == []                       # 不重复推送
    Path(env["_tmp"], "svc").write_text("active\n")
    out = run(env)
    assert len(out) == 1 and "已恢复" in out[0]


def test_live_start_recording_ok_then_end(env):
    rec = Path(env["REC_DIR"]) / "1724773744-娜由纪雅kiya"; rec.mkdir()
    FakeLive.status = 1
    touch(rec / "a.flv"); touch(rec / "a.xml", content='<i><d p="1,1">哈</d><d p="2,1">草</d></i>')
    out = run(env)
    assert len(out) == 1 and "开播了" in out[0] and "录制正常" in out[0]
    assert run(env) == []                       # 直播中、一切正常：安静
    FakeLive.status = 0
    out = run(env)
    assert len(out) == 1 and "下播了" in out[0] and "弹幕 2 条" in out[0] and "录像 1 段" in out[0]


def test_live_but_not_recording(env):
    FakeLive.status = 1
    out = run(env)
    assert "开播了" in out[0] and "还没开始写入" in out[0]
    assert run(env) == []                       # 宽限期内不告警
    set_state(env, live_since=time.time() - 600)
    out = run(env)
    assert len(out) == 1 and "没有在录制" in out[0]
    touch(Path(env["REC_DIR"]) / "b.flv"); touch(Path(env["REC_DIR"]) / "b.xml")
    out = run(env)
    assert len(out) == 1 and "录像已恢复写入" in out[0]


def test_danmaku_stale(env):
    FakeLive.status = 1
    run(env)
    set_state(env, live_since=time.time() - 3600)
    touch(Path(env["REC_DIR"]) / "c.flv")
    touch(Path(env["REC_DIR"]) / "c.xml", age=1200)
    out = run(env)
    assert len(out) == 1 and "弹幕可能没在录" in out[0]


def test_disk_and_sync(env):
    env["KIYA_DISK_ALERT_PERCENT"] = "0"
    Path(env["KIYA_SYNC_LOG"]).write_text("[2020-01-01 00:00:00] sync done\n")
    out = run(env)
    assert any("硬盘快满了" in l for l in out)
    assert any("OSS 同步太久没成功" in l for l in out)
    env["KIYA_DISK_ALERT_PERCENT"] = "101"
    Path(env["KIYA_SYNC_LOG"]).write_text(time.strftime("[%Y-%m-%d %H:%M:%S] sync done\n"))
    out = run(env)
    assert sum("已恢复" in l for l in out) == 2


def test_api_failures_alert_after_five(env):
    FakeLive.fail = True
    for _ in range(4):
        assert run(env) == []
    out = run(env)
    assert len(out) == 1 and "查询开播状态失败" in out[0]
    FakeLive.fail = False
    out = run(env)
    assert len(out) == 1 and "开播状态查询已恢复" in out[0]


def test_run_cut(tmp_path, monkeypatch):
    """下播自动剪切：找到刚粗筛完的场次、调用 cut.py、把结果整理成推送；没有候选时不推送。"""
    sys.path.insert(0, str(HERE))
    import kiya_monitor as km
    worker = tmp_path / "worker"
    (worker / ".venv" / "bin").mkdir(parents=True)
    (worker / "scripts").mkdir()
    (worker / "scripts" / "cut.py").write_text("")
    log = tmp_path / "args.txt"
    py = worker / ".venv" / "bin" / "python"
    py.write_text(f'#!/bin/sh\necho "$@" > "{log}"\necho "字幕：样式 default（标准白字黑边）"\n'
                  'echo "  # 1 00:01:00–00:02:00 → 01_00-01-30.mp4"\necho "完成 3/3 段，输出在 x"\n')
    py.chmod(0o755)
    monkeypatch.setattr(km, "REPO", tmp_path)
    assert km.run_cut({}) is None                                   # 还没有粗筛结果
    sess = worker / "output" / "20261112-2000"
    sess.mkdir(parents=True)
    (sess / "candidates.json").write_text(json.dumps({"candidates": [{"rank": 1}, {"rank": 2}, {"rank": 3}]}))
    msg = km.run_cut({"KIYA_CUT_HEIGHT": "480"})
    assert "20261112-2000：完成 3/3 段" in msg and "字幕：样式 default" in msg and "review.py" in msg
    assert "--height 480" in log.read_text() and str(sess / "candidates.json") in log.read_text()
    (sess / "candidates.json").write_text(json.dumps({"candidates": []}))   # 分数线以下全没过：不剪不推
    assert km.run_cut({}) is None
    old = time.time() - 7200                                        # 很久以前的场次不会被误剪
    (sess / "candidates.json").write_text(json.dumps({"candidates": [{"rank": 1}]}))
    os.utime(sess / "candidates.json", (old, old))
    assert km.run_cut({}) is None


def test_run_live_screen(tmp_path, monkeypatch):
    """边播边筛：调用 screen.py --live，把结果整理成 (状态, 说明)。"""
    sys.path.insert(0, str(HERE))
    import kiya_monitor as km
    worker = tmp_path / "worker"
    (worker / ".venv" / "bin").mkdir(parents=True)
    (worker / "scripts").mkdir()
    (worker / "scripts" / "screen.py").write_text("")
    log = tmp_path / "args.txt"
    py = worker / ".venv" / "bin" / "python"
    body = tmp_path / "body.sh"

    def fake(script: str, code: int = 0):
        py.write_text(f'#!/bin/sh\necho "$@" > "{log}"\n{script}\nexit {code}\n'); py.chmod(0o755)

    monkeypatch.setattr(km, "REPO", tmp_path)
    fake('echo "== 20261112-2000：2 个分段 =="\necho "  #  1 20:41:00  分数  7.10  [audio+3.1]"\n'
         'echo "  #  2 20:52:10  分数  5.30  [density+2.2]"\necho "  边播边筛：覆盖前 40 分钟，下播后会重新筛一遍（只补最后一段）"')
    st, msg = km.run_live_screen({}, tmp_path / "rec")
    assert st == "ok" and "覆盖前 40 分钟" in msg and "2 个候选" in msg
    a = log.read_text()
    assert "--live" in a and "--latest" in a and "--no-video" not in a
    assert "--no-video" in (km.run_live_screen({"KIYA_ANALYZE_NO_VIDEO": "1"}, tmp_path) and log.read_text())
    fake('echo "  已录完的部分才 10 分钟，不到 15 分钟，先不筛"')
    assert km.run_live_screen({}, tmp_path)[0] == "skip"
    fake('echo "  边播边筛：覆盖前 40 分钟，下播后会重新筛一遍"')
    assert km.run_live_screen({}, tmp_path)[0] == "empty"
    fake('echo "boom" >&2', code=1)
    assert km.run_live_screen({}, tmp_path) == ("error", "boom")


def test_check_triggers_live_screen_on_interval(env, tmp_path, monkeypatch):
    """开关默认关；打开后直播中每 30 分钟一次，没有可筛的分段时 5 分钟后重试，首次出候选推送一次，下播清状态。"""
    sys.path.insert(0, str(HERE))
    import kiya_monitor as km
    rec = Path(env["REC_DIR"]); touch(rec / "a.flv"); touch(rec / "a.xml")
    calls, pushes, result = [], [], [("ok", "已录完前 30 分钟，3 个候选")]
    monkeypatch.setattr(km, "run_live_screen", lambda e, r: calls.append(1) or result[0])
    monkeypatch.setattr(km, "notify", lambda e, title, body, dry=False: pushes.append(title))
    monkeypatch.setattr(km, "live_status", lambda e: (1, "测试"))
    monkeypatch.setattr(km, "service_active", lambda e: True)
    monkeypatch.setattr(km, "sync_age_hours", lambda p: None)
    e = {**env, "KIYA_AUTO_ANALYZE": "0"}
    km.check(e)                                                      # 开播
    set_state(e, live_since=time.time() - 3600)
    km.check(e)
    assert calls == []                                               # 开关没开
    e["KIYA_LIVE_SCREEN"] = "1"
    set_state(e, live_since=time.time() - 600)
    km.check(e)
    assert calls == []                                               # 才开播 10 分钟，还没到间隔
    set_state(e, live_since=time.time() - 1900)
    km.check(e)
    assert len(calls) == 1 and any("边播边筛" in t for t in pushes)
    km.check(e)
    assert len(calls) == 1                                           # 间隔内不重复
    n = len([t for t in pushes if "边播边筛" in t])
    set_state(e, live_screen_at=time.time() - 1900)
    km.check(e)
    assert len(calls) == 2 and len([t for t in pushes if "边播边筛" in t]) == n   # 同一场只推一次
    result[0] = ("skip", "先不筛")
    set_state(e, live_screen_at=time.time() - 1900)
    km.check(e)
    assert len(calls) == 3
    set_state(e, live_screen_at=state(e)["live_screen_at"])
    assert time.time() - state(e)["live_screen_at"] > 1400           # 往回拨到 5 分钟后重试
    result[0] = ("error", "炸了")
    set_state(e, live_screen_at=time.time() - 1900)
    km.check(e)
    assert any("边播边筛失败" in t for t in pushes)
    monkeypatch.setattr(km, "live_status", lambda e: (0, ""))
    km.check(e)                                                      # 下播
    s = state(e)
    assert s["live"] is False and not s.get("live_screen_at") and "live_screen" not in s["alerts"]
