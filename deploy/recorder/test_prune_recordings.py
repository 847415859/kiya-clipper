import os
import subprocess
import time
from types import SimpleNamespace

import pytest

import prune_recordings as retention


def recording(root, name, start, *, age=12_000):
    room = root / "录播房间"
    room.mkdir(exist_ok=True)
    xml = room / f"{name}.xml"
    video = xml.with_suffix(".flv")
    xml.write_text(f'<i><BililiveRecorderRecordInfo start_time="{start}"/></i>', encoding="utf-8")
    video.write_bytes(b"recording")
    for path in (xml, video):
        old = time.time() - age
        os.utime(path, (old, old))
    return video


@pytest.fixture
def scenes(tmp_path):
    oldest = [
        recording(tmp_path, "直播一 第一段", "2026-11-24T22:00:00+08:00"),
        recording(tmp_path, "直播一 第二段", "2026-11-24T22:30:00+08:00"),
    ]
    newer = recording(tmp_path, "直播二", "2026-11-25T20:00:00+08:00")
    return oldest, newer


@pytest.mark.parametrize("free,plan", [(retention.GIB, False), (0, True)])
def test_safe_space_and_plan_never_call_oss_or_delete(tmp_path, scenes, monkeypatch, free, plan):
    monkeypatch.setattr(retention.shutil, "disk_usage", lambda path: SimpleNamespace(free=free))
    monkeypatch.setattr(retention.subprocess, "run", lambda *args, **kwargs: pytest.fail("unexpected OSS call"))
    retention.prune(tmp_path, "oss:test", retention.GIB, plan)
    assert all(p.is_file() for p in [*scenes[0], scenes[1]])


def test_recently_written_scene_is_kept(tmp_path, monkeypatch):
    video = recording(tmp_path, "正在写入", "2026-11-25T20:00:00+08:00", age=60)
    monkeypatch.setattr(retention.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))
    monkeypatch.setattr(retention.subprocess, "run", lambda *args, **kwargs: pytest.fail("unexpected OSS call"))
    retention.prune(tmp_path, "oss:test", retention.GIB, False)
    assert video.exists()


@pytest.mark.parametrize("outcome", ["match", "missing", "failed", "changed"])
def test_pressure_only_deletes_oldest_after_all_segments_are_verified(tmp_path, scenes, monkeypatch, outcome):
    oldest, newer = scenes
    monkeypatch.setattr(retention.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))

    def check(command, *, input, **kwargs):
        assert command[:4] == ["rclone", "check", str(tmp_path), "oss:test"]
        assert input.splitlines() == [p.relative_to(tmp_path).as_posix() for p in oldest]
        if outcome == "failed":
            raise subprocess.CalledProcessError(1, command)
        if outcome == "changed":
            oldest[1].write_bytes(b"changed")
        return SimpleNamespace(stdout=input if outcome != "missing" else "")

    monkeypatch.setattr(retention.subprocess, "run", check)
    if outcome == "match":
        retention.prune(tmp_path, "oss:test", retention.GIB, False)
    else:
        with pytest.raises((ValueError, subprocess.CalledProcessError)):
            retention.prune(tmp_path, "oss:test", retention.GIB, False)
    assert all(p.exists() == (outcome != "match") for p in oldest)
    assert all(p.with_suffix(".xml").is_file() for p in oldest)
    assert newer.is_file()
