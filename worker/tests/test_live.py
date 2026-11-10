"""边播边筛：直播中只筛已录完的分段，不污染整场缓存，下播后的正式粗筛复用分段缓存。

沿用 test_phase1 的模拟直播（30 分钟、3 个分段），把前两段的修改时间改老，最后一段保持“刚写过”，
当作第三段还在录。需要 ffmpeg。
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from clipper.danmaku import Segment  # noqa: E402
from clipper.session import drop_active  # noqa: E402
from test_phase1 import SEG, session  # noqa: E402,F401

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")
SCREEN = str(ROOT / "scripts" / "screen.py")


def run(rec, out, *extra):
    r = subprocess.run([sys.executable, SCREEN, str(rec.parent), "--latest", "--out", str(out), "--top", "10", *extra],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def test_drop_active_only_trailing(tmp_path):
    segs = []
    for i in range(3):
        x = tmp_path / f"s{i}.xml"
        x.write_text("<i/>")
        segs.append(Segment(xml_path=x, start_time=None, offset=i * 10))
    now = time.time()
    for s in segs:
        os.utime(s.xml_path, (now - 3600, now - 3600))
    assert len(drop_active(segs, 120, now)) == 3                 # 都定稿了（下播之后）
    os.utime(segs[2].xml_path, (now - 30, now - 30))
    assert [s.xml_path.name for s in drop_active(segs, 120, now)] == ["s0.xml", "s1.xml"]
    os.utime(segs[1].xml_path, (now - 10, now - 10))              # 末尾在写、前一段也很新：保守地一起去掉
    assert [s.xml_path.name for s in drop_active(segs, 120, now)] == ["s0.xml"]
    os.utime(segs[2].xml_path, (now - 3600, now - 3600))          # 只有中间的新、末尾已定稿：只从末尾剥，不动它
    assert len(drop_active(segs, 120, now)) == 3


def test_live_then_final(session, tmp_path):                      # noqa: F811
    segs = sorted(session.glob("*.xml"))
    old = time.time() - 3600
    for x in segs[:2]:
        for f in (x, x.with_suffix(".flv")):
            os.utime(f, (old, old))
    now = time.time()
    for f in (segs[2], segs[2].with_suffix(".flv")):
        os.utime(f, (now, now))
    out = tmp_path / "out"

    # 直播中：只筛前两段，且不写整场缓存
    log = run(session, out, "--live")
    d = out / "20261110-2000"
    live = json.loads((d / "candidates.json").read_text(encoding="utf-8"))
    assert live["live"] is True and live["segments_done"] == 2 and live["segments_total"] == 3
    assert 2 * SEG - 5 <= live["covered_seconds"] <= 2 * SEG + 5
    assert all(c["t"] < 2 * SEG + 5 for c in live["candidates"]), log
    assert not (d / "features.npz").exists()
    assert len(list((d / "segfeat").glob("*.npz"))) == 2
    assert not (d / "rerank.json").exists()

    # 下一轮 live：前两段直接用缓存
    log = run(session, out, "--live")
    assert log.count("用缓存") == 2

    # 下播（第三段也定稿）：正式粗筛只补第三段，结果覆盖 live
    for f in (segs[2], segs[2].with_suffix(".flv")):
        os.utime(f, (old, old))
    log = run(session, out)
    assert log.count("用缓存") == 2 and "音频" in log
    final = json.loads((d / "candidates.json").read_text(encoding="utf-8"))
    assert "live" not in final and (d / "features.npz").exists()
    assert len(list((d / "segfeat").glob("*.npz"))) == 3

    # 复用缓存的结果和从头提取的结果一样
    out2 = tmp_path / "out2"
    run(session, out2, "--refresh")
    fresh = json.loads((out2 / "20261110-2000" / "candidates.json").read_text(encoding="utf-8"))
    assert [(c["rank"], round(c["t"]), round(c["score"], 2)) for c in final["candidates"]] == \
           [(c["rank"], round(c["t"]), round(c["score"], 2)) for c in fresh["candidates"]]

    # 已经有正式结果，再来 live 不能覆盖
    log = run(session, out, "--live")
    assert "不再做边播边筛" in log
    assert "live" not in json.loads((d / "candidates.json").read_text(encoding="utf-8"))


def test_live_too_short_is_skipped(session, tmp_path):            # noqa: F811
    segs = sorted(session.glob("*.xml"))
    old = time.time() - 3600
    for x in segs:
        for f in (x, x.with_suffix(".flv")):
            os.utime(f, (old, old))
    now = time.time()
    for x in segs[1:]:
        for f in (x, x.with_suffix(".flv")):
            os.utime(f, (now, now))                               # 只有第一段录完：10 分钟 < 15 分钟
    out = tmp_path / "o"
    log = run(session, out, "--live")
    assert "先不筛" in log and not (out / "20261110-2000" / "candidates.json").exists()
