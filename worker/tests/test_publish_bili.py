"""一键发布到 B站：时间换算、安全闸（违禁词 / 重复 / 限频 / 没成片 / 参数不认识）、命令拼装、结果记录。
真正的 B站上传在云端测不了，这里用假的 biliup 脚本代替，只验证我们这边的逻辑。"""
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import publish_bili as pb  # noqa: E402

FAKE = """#!/bin/bash
echo "$@" >> "$FAKE_LOG"
if [ "$1" = "-u" ]; then shift; shift; fi
if [ "$2" = "--help" ]; then echo "$FAKE_FLAGS"; exit 0; fi
echo "uploading..."; echo "投稿成功 BV1ab411c7de"; exit ${FAKE_EXIT:-0}
"""
ALL_FLAGS = "--copyright --source --tid --title --desc --tag --limit --retry --submit --line --cover --dtime"


def setup(tmp_path, taboo=(), final=True, extra_cfg=""):
    sess = tmp_path / "output" / "20261109-2014"
    (sess / "publish").mkdir(parents=True)
    (sess / "candidates.json").write_text(json.dumps({"segments": [], "candidates": [{"rank": 1, "t": 60}]}), encoding="utf-8")
    pack = {"title": "怪不得叫金油！", "description": "抹精油手滑", "tags": ["虚拟主播", "金油"],
            "source": "https://live.bilibili.com/1724773744", "taboo_hits": list(taboo)}
    (sess / "publish" / "01.json").write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    (sess / "publish" / "01_cover.jpg").write_bytes(b"jpg")
    if final:
        (sess / "final").mkdir()
        (sess / "final" / "01_00-01-00_480p.mp4").write_bytes(b"lo")
        (sess / "final" / "01_00-01-00_720p.mp4").write_bytes(b"hi")
    cfg = tmp_path / "k.yaml"
    cfg.write_text("streamer: {name: kiya}\npublish:\n  bili:\n    min_interval_sec: 600\n    submit: web\n" + extra_cfg, encoding="utf-8")
    fake = tmp_path / "biliup"
    fake.write_text(FAKE, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    cookie = tmp_path / "cookies.json"
    cookie.write_text('{"cookie_info": {}}', encoding="utf-8")
    env = dict(os.environ, KIYA_BILIUP=str(fake), KIYA_BILIUP_COOKIE=str(cookie), FAKE_LOG=str(tmp_path / "log.txt"),
               FAKE_FLAGS=ALL_FLAGS, KIYA_MEMORY_DECISIONS=str(tmp_path / "none.json"))
    return sess, cfg, env


def run(sess, cfg, env, *a):
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "publish_bili.py"), str(sess / "candidates.json"),
                        "--rank", "1", "--config", str(cfg), *map(str, a)], capture_output=True, text=True, env=env)
    return r


def last(r):
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_resolve_dtime():
    now = 1_800_000_000
    assert pb.resolve_dtime("now", None, 14, now) is None
    d = pb.resolve_dtime("hold", None, 14, now)
    assert d == now + 14 * 86400
    assert pb.resolve_dtime("hold", None, 99, now) < now + 15 * 86400          # 夹在 15 天以内
    assert pb.resolve_dtime("hold", None, 0.01, now) >= now + 4 * 3600          # 不会短于 4 小时
    at = time.strftime("%Y-%m-%dT%H:%M", time.localtime(now + 86400))
    assert abs(pb.resolve_dtime("timed", at, 14, now) - (now + 86400)) < 60
    for bad in (None, "明天", time.strftime("%Y-%m-%dT%H:%M", time.localtime(now + 3600)),
                time.strftime("%Y-%m-%dT%H:%M", time.localtime(now + 20 * 86400))):
        try:
            pb.resolve_dtime("timed", bad, 14, now)
            assert False, bad
        except pb.Refuse:
            pass


def test_hold_uploads_with_timer_and_records_result(tmp_path):
    sess, cfg, env = setup(tmp_path)
    r = run(sess, cfg, env)
    assert r.returncode == 0, r.stdout + r.stderr
    res = last(r)
    assert res["status"] == "ok" and res["mode"] == "hold" and res["bvid"] == "BV1ab411c7de" and res["video"].endswith("720p.mp4")
    assert res["dtime"] > time.time() + 13 * 86400
    line = (tmp_path / "log.txt").read_text(encoding="utf-8").splitlines()
    cmd = next(x for x in line if " upload " in x and "--help" not in x)
    assert "--copyright 2" in cmd and "--source https://live.bilibili.com/1724773744" in cmd and "--dtime" in cmd
    assert "--tag 虚拟主播,金油" in cmd and "720p.mp4" in cmd and "01_cover.jpg" in cmd and "--submit web" in cmd
    assert json.loads((sess / "publish" / "01.bili.json").read_text(encoding="utf-8"))["status"] == "ok"


def test_now_has_no_timer(tmp_path):
    sess, cfg, env = setup(tmp_path)
    r = run(sess, cfg, env, "--mode", "now")
    assert r.returncode == 0 and last(r)["dtime"] is None
    cmd = next(x for x in (tmp_path / "log.txt").read_text(encoding="utf-8").splitlines() if " upload " in x and "--help" not in x)
    assert "--dtime" not in cmd


def test_refuses_taboo_duplicate_and_rate(tmp_path):
    sess, cfg, env = setup(tmp_path, taboo=["律师函"])
    r = run(sess, cfg, env)
    assert r.returncode == 2 and "律师函" in r.stderr and not (tmp_path / "log.txt").exists()
    r = run(sess, cfg, env, "--allow-taboo")
    assert r.returncode == 0, r.stderr
    r = run(sess, cfg, env, "--allow-taboo")                      # 已经投过
    assert r.returncode == 2 and "已经" in r.stderr
    r = run(sess, cfg, env, "--allow-taboo", "--again")           # 重投被限频挡住
    assert r.returncode == 2 and "太近" in r.stderr
    (sess.parent / "_bili_last.json").write_text(json.dumps({"at": time.time() - 3600}), encoding="utf-8")
    r = run(sess, cfg, env, "--allow-taboo", "--again")
    assert r.returncode == 0 and last(r)["previous"]["bvid"] == "BV1ab411c7de"


def test_no_final_video_refused(tmp_path):
    sess, cfg, env = setup(tmp_path, final=False)
    r = run(sess, cfg, env)
    assert r.returncode == 2 and "渲染" in r.stderr


def test_unknown_flag_stops_before_upload(tmp_path):
    sess, cfg, env = setup(tmp_path)
    env["FAKE_FLAGS"] = "--title --desc"
    r = run(sess, cfg, env)
    assert r.returncode == 2 and "不认识" in r.stderr
    assert not any(" upload " in x and "--help" not in x for x in (tmp_path / "log.txt").read_text(encoding="utf-8").splitlines())


def test_private_needs_verified_args(tmp_path):
    sess, cfg, env = setup(tmp_path)
    r = run(sess, cfg, env, "--mode", "private")
    assert r.returncode == 2 and "private_args" in r.stderr
    sess, cfg, env = setup(tmp_path / "b", extra_cfg="    private_args: ['--is-only-self']\n")
    env["FAKE_FLAGS"] = ALL_FLAGS + " --is-only-self"
    r = run(sess, cfg, env, "--mode", "private")
    assert r.returncode == 0, r.stderr
    assert any("--is-only-self" in x for x in (tmp_path / "b" / "log.txt").read_text(encoding="utf-8").splitlines())


def test_upload_failure_recorded_and_not_blocking_retry_record(tmp_path):
    sess, cfg, env = setup(tmp_path)
    env["FAKE_EXIT"] = "1"
    r = run(sess, cfg, env)
    assert r.returncode == 1 and last(r)["status"] == "failed"
    assert json.loads((sess / "publish" / "01.bili.json").read_text(encoding="utf-8"))["status"] == "failed"


def test_dry_run_and_status(tmp_path):
    sess, cfg, env = setup(tmp_path)
    r = run(sess, cfg, env, "--dry-run")
    assert r.returncode == 0 and last(r)["status"] == "dry_run" and "<cookie>" in last(r)["command"]
    assert str(tmp_path / "cookies.json") not in r.stdout and not (sess / "publish" / "01.bili.json").exists()
    assert not (tmp_path / "log.txt").exists()
    s = subprocess.run([sys.executable, str(ROOT / "scripts" / "publish_bili.py"), "--status", "--config", str(cfg)],
                       capture_output=True, text=True, env=env)
    st = json.loads(s.stdout.strip().splitlines()[-1])
    assert st["biliup"] and st["logged_in"] and st["private"] is False
