import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from clipper.danmaku import all_events, load_session, parse_xml  # noqa: E402
from clipper.peaks import find_top_peaks, hhmmss  # noqa: E402
from fake_xml import make_xml, write_session  # noqa: E402

CFG = yaml.safe_load((ROOT.parent / "config" / "kiya.yaml").read_text(encoding="utf-8"))
TZ = timezone(timedelta(hours=8))
START = datetime(2026, 11, 9, 20, 0, 0, tzinfo=TZ)


def test_parse_normal_xml(tmp_path):
    p = tmp_path / "a.xml"
    p.write_text(make_xml(START, 300, [(100, "哈哈哈哈")]), encoding="utf-8")
    seg = parse_xml(p)
    kinds = {e.kind for e in seg.events}
    assert kinds == {"danmaku", "gift", "sc", "guard"}
    assert seg.start_time == START
    assert any(e.text == "<符号>测试" for e in seg.events)
    guard = next(e for e in seg.events if e.kind == "guard")
    assert guard.value == 198.0


def test_parse_truncated_xml_falls_back(tmp_path):
    p = tmp_path / "broken.xml"
    p.write_text(make_xml(START, 300, [(100, "草")], truncated=True), encoding="utf-8")
    seg = parse_xml(p)
    assert seg.start_time == START
    assert sum(e.kind == "danmaku" for e in seg.events) > 100
    assert any(e.kind == "sc" and e.value == 50 for e in seg.events)


def test_session_offsets_follow_wall_clock(tmp_path):
    # 3 段各 30 分钟，第 1 段后断流 2 分钟
    paths = write_session(tmp_path, START, 1800, 3, {}, gap_after={0: 120})
    segs = load_session(list(reversed(paths)))  # 打乱顺序也应按时间排好
    assert [s.offset for s in segs] == [0, 1920, 3720]
    ev = [e for e in segs[2].events if e.kind == "danmaku"][0]
    assert abs(ev.t - (ev.local_t + 3720)) < 1e-6


def test_peaks_found_at_bursts(tmp_path):
    bursts = {0: [(600, "哈哈哈哈哈")], 2: [(900, "？？？")]}  # 整场第 600 秒、第 4500 秒
    paths = write_session(tmp_path, START, 1800, 3, bursts)
    segs = load_session(paths)
    peaks, _ = find_top_peaks(all_events(segs), segs, CFG)
    top2 = sorted(p.t for p in peaks[:2])
    assert abs(top2[0] - 600) < 40
    assert abs(top2[1] - 4500) < 40
    p2 = next(p for p in peaks[:2] if p.t > 3600)
    assert p2.file_index == 2
    assert abs(p2.local_start - (p2.clip_start - 3600)) < 1e-6
    assert "？？？" in p2.top_danmaku


def test_cli_writes_outputs(tmp_path):
    rec = tmp_path / "rec"
    rec.mkdir()
    write_session(rec, START, 1800, 2, {1: [(300, "笑死")]})
    # 另一场：第二天，应被分成两场
    write_session(rec, START + timedelta(days=1), 1800, 1, {0: [(500, "草")]})
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "analyze_danmaku.py"), str(rec),
                        "--out", str(out), "--top", "5"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    dirs = sorted(d.name for d in out.iterdir())
    assert dirs == ["20261109-2000", "20261110-2000"]
    d = out / "20261109-2000"
    assert (d / "curve.png").stat().st_size > 1000
    csv_text = (d / "peaks.csv").read_text(encoding="utf-8-sig")
    assert "是否有趣(Y/N)" in csv_text
    assert hhmmss(2100 - 5)[:5] in csv_text or "00:34" in csv_text
    assert "ffmpeg" in (d / "cut_candidates.sh").read_text(encoding="utf-8")
