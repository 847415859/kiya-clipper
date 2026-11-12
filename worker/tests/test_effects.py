"""剪辑效果扩展：鼓点检测、曲库（授权必填）、吸附鼓点、吐槽旁白 / 伪 UI 的校验和数量限制、edit_check 的音乐提示。"""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper import beats as B  # noqa: E402
from clipper import music as M  # noqa: E402
from clipper.edit import (EditError, limit_overlays, snap_to_beats, validate_edit, validate_item)  # noqa: E402


def click_track(bpm, offset, dur=40.0, noise=0.02, seed=1):
    rng = np.random.default_rng(seed)
    y = rng.normal(0, noise, int(dur * B.SR)).astype(np.float32)
    t = offset
    while t < dur - 0.1:
        n, i = int(0.06 * B.SR), int(t * B.SR)
        k = np.arange(n) / B.SR
        y[i:i + n] += (np.sin(2 * np.pi * 900 * k) * np.exp(-k * 60)).astype(np.float32)
        t += 60 / bpm
    return y


def write_wav(path: Path, y: np.ndarray):
    import wave
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(B.SR)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes())


def test_beat_detection_on_click_tracks():
    for bpm, off in [(120, 0.3), (100, 0.17), (140, 0.41), (90, 0.0)]:
        env, fps = B.onset_envelope(click_track(bpm, off))
        r = B.beats_from_envelope(env, fps)
        exp = np.arange(off, 40, 60 / bpm)
        assert abs(r["bpm"] - bpm) < 1.0 and r["confidence"] > 0.5
        err = [abs(b - exp[np.argmin(abs(exp - b))]) for b in r["beats"][:30]]
        assert max(err) < 0.05, (bpm, max(err))                       # 鼓点误差 50ms 以内


def test_no_beat_in_noise_has_low_confidence():
    y = np.random.default_rng(3).normal(0, 0.1, 40 * B.SR).astype(np.float32)
    env, fps = B.onset_envelope(y)
    r = B.beats_from_envelope(env, fps)
    assert r["confidence"] < B.SNAP_MIN_CONFIDENCE


def test_music_registry_requires_license_and_picks(tmp_path, monkeypatch):
    monkeypatch.setenv("KIYA_MUSIC", str(tmp_path / "music"))
    (tmp_path / "music").mkdir()
    write_wav(tmp_path / "music" / "beat120.wav", click_track(120, 0.3, 20))
    write_wav(tmp_path / "music" / "noise.wav", np.random.default_rng(1).normal(0, 0.1, 20 * B.SR).astype(np.float32))
    for bad in ("", "   ", "abc"):                                      # 授权说明必填
        try:
            M.register("beat120.wav", "节拍", bad)
            assert False
        except M.MusicError as e:
            assert "授权" in str(e)
    assert not M.load_tracks()
    try:
        M.register("不存在.wav", "x", "CC0 来源 https://x")
        assert False
    except M.MusicError as e:
        assert "先把文件放进去" in str(e)
    t = M.register("beat120.wav", "节拍 120", "CC0，来源 https://example.com/a", mood="轻快 反差")
    M.register("noise.wav", "噪声", "CC0，来源 https://example.com/b")
    assert M.pick_track(1) is None                                      # 还没做鼓点检测：不配 BGM
    for tr in M.load_tracks():
        M.analyze(tr)
    rows = {r["id"]: r for r in M.summary()}
    assert rows["beat120"]["snap_ok"] and abs(rows["beat120"]["bpm"] - 120) < 1 and not rows["noise"]["snap_ok"]
    assert M.pick_track(1)["id"] == "beat120" and M.pick_track(2, "反差")["id"] == "beat120"     # 噪声曲子节拍不清晰，不会被选
    assert M.remove(t["id"]) and not M.pick_track(1)


def test_snap_is_idempotent_and_keeps_far_items():
    items = [{"type": "text", "start": 1.05, "duration": 2}, {"type": "zoom", "start": 3.4, "duration": 2},
             {"type": "fakeui", "start": 5.0, "duration": 2}]
    beats = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]          # 120 BPM；曲子从第 0 秒开始
    out, moved = snap_to_beats(items, beats, 0.0, 30)
    assert [round(i["start"], 2) for i in out] == [1.0, 3.5, 5.0] and moved == 2        # 5.0 本来就在鼓点上，不算挪动
    assert all(o["duration"] == i["duration"] for o, i in zip(out, items))
    out2, moved2 = snap_to_beats(out, beats, 0.0, 30)
    assert out2 == out and moved2 == 0
    far, m = snap_to_beats([{"type": "text", "start": 1.25, "duration": 1}], beats, 0.0, 30)      # 离两个鼓点都 0.25 > 0.2
    assert m == 0 and far[0]["start"] == 1.25
    shifted, _ = snap_to_beats([{"type": "text", "start": 0.55, "duration": 1}], beats, 2.0, 30)      # 曲子从第 2 秒开始：成片里的鼓点 = 曲子时刻 - 2
    assert shifted[0]["start"] == 0.5


def test_new_item_types_validate():
    n = validate_item({"type": "narration", "start": 1, "text": "这就是传说中的做局"}, 30)
    assert n["tag"] == "吐槽" and n["style"] == "strip" and n["y"] == 0.16 and n["duration"] == 2.0
    f = validate_item({"type": "fakeui", "kind": "achievement", "start": 2, "text": "连续手滑三次"}, 30)
    assert f["title"] == "成就解锁" and f["scale"] == 0.4 and f["y"] == 0.14
    for bad in ({"type": "fakeui", "kind": "x", "start": 1, "text": "a"}, {"type": "fakeui", "kind": "popup", "start": 1, "text": ""},
                {"type": "fakeui", "kind": "chat", "start": 1, "text": "a" * 41}, {"type": "narration", "start": 1, "text": ""},
                {"type": "narration", "start": 1, "text": "a" * 51}, {"type": "narration", "start": 1, "text": "ok", "tag": "标签太长了啊啊啊啊"},
                {"type": "narration", "start": 1, "text": "ok", "style": "neon"}):
        try:
            validate_item(bad, 30)
            assert False, bad
        except EditError:
            pass
    e = validate_edit({"items": [], "music": {"track": "a", "volume": 0.3}}, 30)
    assert e["music"] == {"track": "a", "start": 0.0, "volume": 0.3, "duck": True, "snap": False}
    assert "music" not in validate_edit({"items": [], "music": {"track": ""}}, 30)
    try:
        validate_edit({"items": [], "music": {"track": "a", "volume": 5}}, 30)
        assert False
    except EditError:
        pass


def test_limit_overlays():
    def n(t):
        return {"type": "narration", "start": t, "duration": 2}
    def u(t):
        return {"type": "fakeui", "start": t, "duration": 2}
    kept, dropped = limit_overlays([n(2), n(5), n(12), n(30), u(3), u(10), u(20), {"type": "text", "start": 1, "duration": 1}])
    assert [(i["type"], i["start"]) for i in kept] == [("text", 1), ("narration", 2), ("fakeui", 3), ("fakeui", 10), ("narration", 12)]
    assert len(dropped) == 3                                            # 间隔不到 8 秒的旁白、超过 2 条的旁白、第 3 个伪 UI


def test_edit_check_snaps_and_warns(tmp_path):
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    write_wav(music_dir / "beat120.wav", click_track(120, 0.0, 20))
    env = dict(os.environ, KIYA_MUSIC=str(music_dir), KIYA_STICKERS=str(tmp_path / "st"))
    py = [sys.executable, str(ROOT / "scripts" / "music.py")]
    r = subprocess.run(py + ["add", "beat120.wav", "--license", "CC0，来源 https://example.com"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    r = subprocess.run(py + ["add", "beat120.wav", "--license", ""], capture_output=True, text=True, env=env)
    assert r.returncode != 0 and "授权" in r.stderr
    r = subprocess.run(py + ["scan"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and "可吸附" in r.stdout, r.stdout + r.stderr

    def check(music):
        f = tmp_path / "e.json"
        f.write_text(json.dumps({"items": [{"type": "text", "start": 1.05, "duration": 2, "text": "哈"},
                                           {"type": "narration", "start": 3.4, "duration": 2, "text": "旁白"}], "music": music}), encoding="utf-8")
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "edit_check.py"), str(f), "--clip-len", "30"],
                           capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)

    res = check({"track": "beat120", "snap": True})
    got = [i["start"] for i in res["edit"]["items"]]
    assert abs(got[0] - 1.0) < 0.03 and abs(got[1] - 3.5) < 0.03 and got != [1.05, 3.4] and not res["warnings"]      # 吸附到检测出的鼓点（误差在 30ms 内）
    res = check({"track": "beat120", "snap": False})                      # 没勾吸附：不动
    assert [i["start"] for i in res["edit"]["items"]] == [1.05, 3.4]
    res = check({"track": "没有这首", "snap": True})
    assert any("曲库里没有" in w for w in res["warnings"]) and [i["start"] for i in res["edit"]["items"]] == [1.05, 3.4]
