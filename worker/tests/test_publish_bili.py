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
echo "uploading..."; printf "\\033[32m INFO\\033[0m 投稿成功 BV1ab411c7de\\n"; exit ${FAKE_EXIT:-0}
"""
ALL_FLAGS = "--copyright --source --tid --title --desc --tag --limit --submit --line --cover --dtime"


def setup_case(tmp_path, taboo=(), final=True, extra_cfg=""):
    sess = tmp_path / "output" / "20261109-2014"
    (sess / "publish").mkdir(parents=True)
    (sess / "candidates.json").write_text(json.dumps({"segments": [], "candidates": [{"rank": 1, "t": 60}]}), encoding="utf-8")
    pack = {"title": "怪不得叫金油！", "description": "抹精油手滑", "tags": ["虚拟主播", "金油"],
            "source": "https://live.bilibili.com/1724773744", "taboo_hits": list(taboo),
            "include_schedule": False, "subtitle_checked": True, "title_fulfilled": True,
            "materials_complete": True}
    (sess / "publish" / "01.json").write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    (sess / "publish" / "01_cover.jpg").write_bytes(b"jpg")
    if final:
        (sess / "final").mkdir()
        (sess / "final" / "01_00-01-00_480p.mp4").write_bytes(b"lo")
        (sess / "final" / "01_00-01-00_720p.mp4").write_bytes(b"hi")
        (sess / "final" / "01_00-01-00_1080p.mp4").write_bytes(b"max")
    cfg = tmp_path / "k.yaml"
    cfg.write_text("streamer: {name: kiya}\npublish:\n  bili:\n    min_interval_sec: 600\n    submit: web\n" + extra_cfg, encoding="utf-8")
    fake = tmp_path / "biliup"
    if os.name == "nt":
        script = tmp_path / "fake_biliup.py"
        script.write_text("""import os, sys
args = sys.argv[1:]
with open(os.environ['FAKE_LOG'], 'a', encoding='utf-8') as f:
    f.write(' '.join(args) + '\\n')
if args[:1] == ['-u']: args = args[2:]
if args[1:2] == ['--help']:
    print(os.environ['FAKE_FLAGS']); sys.exit(0)
print('uploading...')
print('\\x1b[32m INFO\\x1b[0m 投稿成功 BV1ab411c7de')
sys.exit(int(os.environ.get('FAKE_EXIT', '0')))
""", encoding="utf-8")
        fake = fake.with_suffix(".cmd")
        fake.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8")
    else:
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
    at = pb.parse_at(now + 86400).strftime("%Y-%m-%dT%H:%M")
    assert abs(pb.resolve_dtime("timed", at, 14, now) - (now + 86400)) < 60
    for bad in (None, "明天", pb.parse_at(now + 3600).strftime("%Y-%m-%dT%H:%M"),
                pb.parse_at(now + 20 * 86400).strftime("%Y-%m-%dT%H:%M")):
        try:
            pb.resolve_dtime("timed", bad, 14, now)
            assert False, bad
        except pb.Refuse:
            pass


def test_hold_uploads_with_timer_and_records_result(tmp_path):
    sess, cfg, env = setup_case(tmp_path)
    r = run(sess, cfg, env)
    assert r.returncode == 0, r.stdout + r.stderr
    res = last(r)
    assert "\x1b" not in res["output_tail"] and "投稿成功" in res["output_tail"]
    assert res["status"] == "ok" and res["mode"] == "hold" and res["bvid"] == "BV1ab411c7de" and res["video"].endswith("_720p.mp4")
    assert res["preflight"]["ok"] is True and res["final_video"].endswith("_720p.mp4")
    assert res["final_description"] == "抹精油手滑"
    assert res["dtime"] > time.time() + 13 * 86400
    line = (tmp_path / "log.txt").read_text(encoding="utf-8").splitlines()
    cmd = next(x for x in line if " upload " in x and "--help" not in x)
    assert "--copyright 1" in cmd and "--source" not in cmd and "--dtime" in cmd
    assert res["copyright"] == "自制"
    assert "--retry" not in cmd and "--limit 3" in cmd
    assert "--tag 虚拟主播,金油" in cmd and "720p.mp4" in cmd and "01_cover.jpg" in cmd and "--submit web" in cmd
    dec = json.loads((tmp_path / "none.json").read_text(encoding="utf-8"))          # BV 号自动绑定到精排记忆
    assert res["bound"] is True and dec["published"] == {"BV1ab411c7de": {"session": "20261109-2014", "rank": 1}}
    assert json.loads((sess / "publish" / "01.bili.json").read_text(encoding="utf-8"))["status"] == "ok"


def test_legacy_pack_uses_current_copyright_and_requires_source_only_for_repost(tmp_path):
    sess, cfg, env = setup_case(tmp_path)
    pack_path = sess / "publish" / "01.json"
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    source = pack.pop("source")
    pack.update(copyright="转载")
    pack_path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")

    r = run(sess, cfg, env, "--dry-run")
    assert r.returncode == 0, r.stderr
    res = last(r)
    assert res["copyright"] == "自制" and "--source" not in res["command"]

    cfg.write_text(cfg.read_text(encoding="utf-8") + "    copyright: 转载\n", encoding="utf-8")
    r = run(sess, cfg, env, "--dry-run")
    assert r.returncode == 2 and "转载类型必须填来源" in r.stderr

    pack["source"] = source
    pack_path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    r = run(sess, cfg, env, "--dry-run")
    assert r.returncode == 0, r.stderr
    res = last(r)
    cmd = res["command"]
    assert res["copyright"] == "转载"
    assert cmd[cmd.index("--copyright") + 1] == "2"
    assert cmd[cmd.index("--source") + 1] == source


def test_pack_and_publish_status_use_same_copyright_setting(tmp_path):
    sess, cfg, env = setup_case(tmp_path)
    candidates = sess / "candidates.json"
    candidates.write_text(json.dumps({"candidates": [{"rank": 1, "t": 60, "clip_start": 30,
                                                     "clip_end": 90}], "segments": []}), encoding="utf-8")
    for setting in ("自制", "转载"):
        if setting == "转载":
            cfg.write_text(cfg.read_text(encoding="utf-8") + "    copyright: 转载\n", encoding="utf-8")
        pack = subprocess.run([sys.executable, str(ROOT / "scripts" / "publish_pack.py"), str(candidates),
                               "--rank", "1", "--no-cover", "--config", str(cfg)],
                              capture_output=True, text=True, env=env)
        status = run(sess, cfg, env, "--status")
        assert pack.returncode == 0, pack.stderr
        assert status.returncode == 0, status.stderr
        assert last(pack)["copyright"] == last(status)["copyright"] == setting


def test_now_has_no_timer(tmp_path):
    sess, cfg, env = setup_case(tmp_path)
    r = run(sess, cfg, env, "--mode", "now")
    assert r.returncode == 0 and last(r)["dtime"] is None
    cmd = next(x for x in (tmp_path / "log.txt").read_text(encoding="utf-8").splitlines() if " upload " in x and "--help" not in x)
    assert "--dtime" not in cmd


def test_refuses_taboo_duplicate_and_rate(tmp_path):
    sess, cfg, env = setup_case(tmp_path, taboo=["律师函"])
    r = run(sess, cfg, env)
    assert r.returncode == 2 and "律师函" in r.stderr and not (tmp_path / "log.txt").exists()
    r = run(sess, cfg, env, "--allow-taboo")
    assert r.returncode == 0, r.stderr
    r = run(sess, cfg, env, "--allow-taboo")                      # 已经投过
    assert r.returncode == 2 and "已经" in r.stderr
    r = run(sess, cfg, env, "--allow-taboo", "--again", "--confirm-no-existing")  # 核查无已有投稿后才进入限频检查
    assert r.returncode == 2 and "太近" in r.stderr
    (sess.parent / "_bili_last.json").write_text(json.dumps({"at": time.time() - 3600}), encoding="utf-8")
    r = run(sess, cfg, env, "--allow-taboo", "--again", "--confirm-no-existing")
    assert r.returncode == 0 and last(r)["previous"]["bvid"] == "BV1ab411c7de"


def test_no_final_video_refused(tmp_path):
    sess, cfg, env = setup_case(tmp_path, final=False)
    r = run(sess, cfg, env)
    assert r.returncode == 2 and "渲染" in r.stderr


def test_preflight_refuses_missing_cover_and_unconfirmed_review(tmp_path):
    sess, cfg, env = setup_case(tmp_path)
    (sess / "publish" / "01_cover.jpg").unlink()
    pack_path = sess / "publish" / "01.json"
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    pack["subtitle_checked"] = False
    pack_path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    r = run(sess, cfg, env)
    assert r.returncode == 2 and "封面" in r.stderr
    assert "字幕检查未通过" in r.stderr
    assert not (tmp_path / "log.txt").exists()


def test_dry_run_shows_final_video_description_and_preflight(tmp_path):
    sess, cfg, env = setup_case(tmp_path)
    r = run(sess, cfg, env, "--dry-run")
    assert r.returncode == 0
    assert "最终视频：" in r.stdout and "最终简介：" in r.stdout and "发布前检查：" in r.stdout
    assert "抹精油手滑" in r.stdout


def test_confirm_review_applies_to_one_attempt_and_does_not_override_failed_check(tmp_path):
    sess, cfg, env = setup_case(tmp_path)
    pack_path = sess / "publish" / "01.json"
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    for key in pb.REVIEW_CHECKS:
        pack.pop(key)
    pack_path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    flags = [item for key in pb.REVIEW_CHECKS for item in ("--confirm-review", key)]

    r = run(sess, cfg, env, "--dry-run")
    assert not last(r)["preflight"]["ok"]
    r = run(sess, cfg, env, "--dry-run", *flags)
    report = last(r)["preflight"]
    assert report["ok"]
    assert all(report["checks"][key]["source"] == "manual_submission" for key in pb.REVIEW_CHECKS)
    assert all(key not in json.loads(pack_path.read_text(encoding="utf-8")) for key in pb.REVIEW_CHECKS)

    pack["subtitle_checked"] = False
    pack_path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    r = run(sess, cfg, env, "--mode", "now", *flags)
    assert r.returncode == 2 and "字幕检查未通过" in r.stderr
    assert not (tmp_path / "log.txt").exists()


def test_manual_submission_allows_missing_review_but_still_blocks_failed_review(tmp_path):
    sess, cfg, env = setup_case(tmp_path)
    pack_path = sess / "publish" / "01.json"
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    for key in pb.REVIEW_CHECKS:
        pack.pop(key)
    pack_path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")

    r = run(sess, cfg, env, "--mode", "now")
    assert r.returncode == 2 and "字幕尚未确认" in r.stderr
    r = run(sess, cfg, env, "--dry-run", "--manual-submission")
    report = last(r)["preflight"]
    assert report["ok"]
    assert all(report["checks"][key]["status"] == "warning" for key in pb.REVIEW_CHECKS)

    pack["subtitle_checked"] = False
    pack_path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    r = run(sess, cfg, env, "--mode", "now", "--manual-submission")
    assert r.returncode == 2 and "字幕检查未通过" in r.stderr
    assert "暂无单独审核记录" not in r.stderr and not (tmp_path / "log.txt").exists()

    pack.pop("subtitle_checked")
    pack_path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    r = run(sess, cfg, env, "--mode", "now", "--manual-submission")
    assert r.returncode == 0 and last(r)["preflight"]["ok"]


def test_unknown_flag_stops_before_upload(tmp_path):
    sess, cfg, env = setup_case(tmp_path)
    env["FAKE_FLAGS"] = "--title --desc"
    r = run(sess, cfg, env)
    assert r.returncode == 2 and "不认识" in r.stderr
    assert not any(" upload " in x and "--help" not in x for x in (tmp_path / "log.txt").read_text(encoding="utf-8").splitlines())


def test_private_needs_verified_args(tmp_path):
    sess, cfg, env = setup_case(tmp_path)
    r = run(sess, cfg, env, "--mode", "private")
    assert r.returncode == 2 and "private_args" in r.stderr
    sess, cfg, env = setup_case(tmp_path / "b", extra_cfg="    private_args: ['--is-only-self']\n")
    env["FAKE_FLAGS"] = ALL_FLAGS + " --is-only-self"
    r = run(sess, cfg, env, "--mode", "private")
    assert r.returncode == 0, r.stderr
    assert any("--is-only-self" in x for x in (tmp_path / "b" / "log.txt").read_text(encoding="utf-8").splitlines())


def test_upload_failure_recorded_and_not_blocking_retry_record(tmp_path):
    sess, cfg, env = setup_case(tmp_path)
    env["FAKE_EXIT"] = "1"
    r = run(sess, cfg, env)
    assert r.returncode == 1 and last(r)["status"] == "failed"
    assert json.loads((sess / "publish" / "01.bili.json").read_text(encoding="utf-8"))["status"] == "failed"


def test_dry_run_and_status(tmp_path):
    sess, cfg, env = setup_case(tmp_path)
    r = run(sess, cfg, env, "--dry-run")
    assert r.returncode == 0 and last(r)["status"] == "dry_run" and "<cookie>" in last(r)["command"]
    assert str(tmp_path / "cookies.json") not in r.stdout and not (sess / "publish" / "01.bili.json").exists()
    assert not (tmp_path / "log.txt").exists()
    s = subprocess.run([sys.executable, str(ROOT / "scripts" / "publish_bili.py"), "--status", "--config", str(cfg)],
                       capture_output=True, text=True, env=env)
    st = json.loads(s.stdout.strip().splitlines()[-1])
    assert st["biliup"] and st["logged_in"] and st["private"] is False


def test_bind_replaces_old_binding_and_keeps_stats(tmp_path):
    sess = tmp_path / "output" / "20261109-2014"
    sess.mkdir(parents=True)
    dec = tmp_path / "d.json"
    dec.write_text(json.dumps({"taboo": ["x"], "published": {
        "BV1old0000001": {"session": "20261109-2014", "rank": 1, "completion": 0.4, "followers": 3},
        "BV1other00002": {"session": "20261109-2014", "rank": 2}}}), encoding="utf-8")
    os.environ["KIYA_MEMORY_DECISIONS"] = str(dec)
    try:
        pb.bind_bvid(sess, 1, "BV1new0000003")
    finally:
        del os.environ["KIYA_MEMORY_DECISIONS"]
    d = json.loads(dec.read_text(encoding="utf-8"))
    assert d["taboo"] == ["x"] and "BV1old0000001" not in d["published"]
    assert d["published"]["BV1new0000003"] == {"session": "20261109-2014", "rank": 1, "completion": 0.4, "followers": 3}
    assert d["published"]["BV1other00002"]["rank"] == 2


def test_find_video_by_height(tmp_path):
    sess, cfg, env = setup_case(tmp_path)
    assert pb.find_video(sess, 1, 720).name.endswith("_720p.mp4")        # 默认 720：不为了清晰度多传一倍的体积
    assert pb.find_video(sess, 1, 1080).name.endswith("_1080p.mp4")
    assert pb.find_video(sess, 1, 500).name.endswith("_480p.mp4")
    (sess / "final" / "01_00-01-00_480p.mp4").unlink()
    (sess / "final" / "01_00-01-00_720p.mp4").unlink()
    assert pb.find_video(sess, 1, 720).name.endswith("_1080p.mp4")       # 没有不超过的，用最接近的更高一份


def test_default_binding_path_uses_session_streamer_without_env_override(tmp_path, monkeypatch):
    from clipper import streamer
    monkeypatch.setattr(streamer, "REPO", tmp_path)
    monkeypatch.delenv("KIYA_MEMORY_DECISIONS", raising=False)
    monkeypatch.setenv("KIYA_STREAMER", "wrong")
    sess = tmp_path / "worker/output/miko/20261117-1200"
    sess.mkdir(parents=True)
    pb.bind_bvid(sess, 1, "BV1ab411c7de")
    path = sess.parent / "memory_decisions.json"
    assert json.loads(path.read_text(encoding="utf-8"))["published"]["BV1ab411c7de"]["rank"] == 1
    assert not (tmp_path / "worker/output/wrong/memory_decisions.json").exists()


def test_binding_type_error_does_not_lose_successful_submission(tmp_path, monkeypatch):
    import argparse
    sess, cfg, env = setup_case(tmp_path)
    for key, value in env.items():
        if key.startswith("KIYA_"):
            monkeypatch.setenv(key, value)
    monkeypatch.setattr(pb, "check_flags", lambda *a: None)
    monkeypatch.setattr(pb, "run_biliup", lambda *a: ("投稿成功 BV1ab411c7de", 0))
    receipt = sess / "publish/01.bili.json"
    def broken(*a):
        assert json.loads(receipt.read_text(encoding="utf-8"))["bvid"] == "BV1ab411c7de"
        raise TypeError("binding interface mismatch")
    monkeypatch.setattr(pb, "bind_bvid", broken)
    args = argparse.Namespace(candidates=str(sess / "candidates.json"), rank=1, config=str(cfg), mode="now", at=None,
                              again=False, dry_run=False, allow_taboo=False)
    result = pb.run(args)
    assert result["status"] == "ok" and result["bound"] is False
    assert result["bind_error"] == "binding interface mismatch"
    stored = json.loads(receipt.read_text(encoding="utf-8"))
    assert stored["status"] == "ok" and stored["bvid"] == "BV1ab411c7de"
    assert stored["upload_status"] == "completed" and stored["submission_status"] == "submitted"
    assert stored["binding_status"] == "failed"
    import pytest
    with pytest.raises(pb.Refuse, match="已经"):
        pb.run(args)


def test_uncertain_submission_requires_platform_check_before_retry(tmp_path):
    sess, cfg, env = setup_case(tmp_path)
    receipt = sess / "publish/01.bili.json"
    receipt.write_text(json.dumps({"status": "failed", "upload_status": "in_progress", "submission_status": "unknown"}), encoding="utf-8")
    r = run(sess, cfg, env, "--again")
    assert r.returncode == 2 and "核查" in r.stderr
    r = run(sess, cfg, env, "--again", "--confirm-no-existing")
    assert r.returncode == 0, r.stdout + r.stderr


def test_repair_binding_records_receipt_and_never_uploads(tmp_path, monkeypatch):
    sess, cfg, env = setup_case(tmp_path)
    monkeypatch.setenv("KIYA_MEMORY_DECISIONS", env["KIYA_MEMORY_DECISIONS"])
    monkeypatch.setattr(pb, "run_biliup", lambda *a: (_ for _ in ()).throw(AssertionError("must not upload")))
    result = pb.repair_binding(sess / "candidates.json", 1, "BV1ab411c7de")
    assert result["status"] == "ok" and result["bound"] and result["recovered"]
    assert json.loads((sess / "publish/01.bili.json").read_text(encoding="utf-8"))["bvid"] == "BV1ab411c7de"
    assert json.loads(Path(env["KIYA_MEMORY_DECISIONS"]).read_text(encoding="utf-8"))["published"]["BV1ab411c7de"]["rank"] == 1


def test_replace_receipt_requires_matching_old_bvid_and_backs_up_original(tmp_path, monkeypatch):
    import pytest
    sess, cfg, env = setup_case(tmp_path)
    monkeypatch.setenv("KIYA_MEMORY_DECISIONS", env["KIYA_MEMORY_DECISIONS"])
    monkeypatch.setattr(pb, "run_biliup", lambda *a: (_ for _ in ()).throw(AssertionError("must not upload")))
    receipt = sess / "publish/01.bili.json"
    old = {"status": "ok", "bvid": "BV1k9WX6nEyq", "at": 100, "at_text": "11-12 14:55", "mode_text": "立即发布",
           "exit_code": 0, "output_tail": "old upload log", "video": "old.mp4"}
    original = json.dumps(old, ensure_ascii=False).encode("utf-8")
    receipt.write_bytes(original)
    for expected in (None, "BV1wrong0000"):
        with pytest.raises(pb.Refuse):
            pb.repair_binding(sess / "candidates.json", 1, "BV1UGsg63E8Y", expected)
        assert receipt.read_bytes() == original
        assert not (receipt.parent / "history").exists()
    result = pb.repair_binding(sess / "candidates.json", 1, "BV1UGsg63E8Y", old["bvid"])
    assert result["bound"] and result["bvid"] == "BV1UGsg63E8Y"
    assert result["previous"]["bvid"] == old["bvid"] and result["previous"]["at_text"] == "11-12 14:55"
    assert (sess / result["previous_receipt"]).read_bytes() == original
    assert result["time_source"] == "repair" and result["at"] != 100
    assert "output_tail" not in result and "video" not in result
    repeated = pb.repair_binding(sess / "candidates.json", 1, result["bvid"], old["bvid"])
    assert repeated["bound"] and len(list((receipt.parent / "history").iterdir())) == 1


def test_replace_cli_only_repairs_and_rejects_upload_or_dry_run_flags(tmp_path):
    sess, cfg, env = setup_case(tmp_path)
    receipt = sess / "publish/01.bili.json"
    receipt.write_text(json.dumps({"status": "ok", "bvid": "BV1k9WX6nEyq"}), encoding="utf-8")
    for flags in [("--replace-bvid", "BV1k9WX6nEyq"),
                  ("--bind-bvid", "BV1UGsg63E8Y", "--dry-run"),
                  ("--bind-bvid", "BV1UGsg63E8Y", "--again")]:
        assert run(sess, cfg, env, *flags).returncode == 2
        assert json.loads(receipt.read_text(encoding="utf-8"))["bvid"] == "BV1k9WX6nEyq"
    r = run(sess, cfg, env, "--bind-bvid", "BV1UGsg63E8Y", "--replace-bvid", "BV1k9WX6nEyq")
    assert r.returncode == 0, r.stdout + r.stderr
    assert last(r)["bvid"] == "BV1UGsg63E8Y" and last(r)["bound"]
    assert not (tmp_path / "log.txt").exists()
