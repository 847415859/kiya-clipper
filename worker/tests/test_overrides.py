"""后台覆盖配置（config/admin_overrides.json）与后台调用的辅助脚本。"""
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from clipper.asr import build_hotwords  # noqa: E402
from clipper.subtitles import get_style, load_config  # noqa: E402
from fake_xml import make_xml  # noqa: E402


def test_overrides_merge(tmp_path, monkeypatch):
    ov = tmp_path / "ov.json"
    monkeypatch.setenv("KIYA_OVERRIDES", str(ov))
    base = load_config()
    assert get_style(None, base)[0] == base["active"]                 # 没有覆盖文件：用 yaml 的 active
    cfg = yaml.safe_load((ROOT.parent / "config" / "kiya.yaml").read_text(encoding="utf-8"))
    n = len(build_hotwords(cfg))

    ov.write_text(json.dumps({"hotwords": ["咪嗷", "舰长"], "corrections": {"key由米": "ki由米"},
                              "subtitle_style": {"font_size": 90, "color": "#FFEE00"},
                              "subtitle_style_name": "后台黄字"}, ensure_ascii=False), encoding="utf-8")
    c = load_config()
    assert c["corrections"]["key由米"] == "ki由米" and c["corrections"]["发件礼"] == "发舰礼"   # 合并，不是替换
    name, st = get_style(None, c)
    assert name == "后台黄字" and st["font_size"] == 90 and st["color"] == "#FFEE00"
    assert get_style("box", c)[0] == "box"                             # 指定样式名时仍用 yaml 里的
    words = [w["text"] for w in build_hotwords(cfg)]
    assert "咪嗷" in words and len(words) == n + 1                     # 舰长已存在，去重

    ov.write_text("{坏掉的 json", encoding="utf-8")                     # 文件损坏不影响 worker
    assert get_style(None, load_config())[0] == base["active"]


def test_sessions_script(tmp_path):
    room = tmp_path / "rec" / "1724773744-kiya"
    room.mkdir(parents=True)
    st = datetime(2026, 11, 12, 20, 0, tzinfo=timezone(timedelta(hours=8)))
    for i in range(2):
        t = st + timedelta(seconds=i * 600)
        stem = room / f"录制-1724773744-{t.strftime('%Y%m%d-%H%M%S')}-000-测试"
        stem.with_suffix(".xml").write_text(make_xml(t, 600, base_rate=0.5, seed=i), encoding="utf-8")
        if i == 0:
            stem.with_suffix(".flv").write_bytes(b"x" * 100)
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "sessions.py"), str(tmp_path / "rec"), "--json"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    s = json.loads(r.stdout)
    assert len(s) == 1 and s[0]["name"] == "20261112-2000" and s[0]["segments"] == 2
    assert s[0]["videos_present"] == 1 and s[0]["video_bytes"] == 100 and 1100 < s[0]["duration_seconds"] <= 1200
    assert s[0]["danmaku"] > 100


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")
def test_preview_style_json(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"font_size": 80, "box": True}), encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "subtitle.py"), "preview", "--style-json", str(f),
                        "--name", "p1", "--out", str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 0 and (tmp_path / "p1.png").stat().st_size > 1000, r.stdout + r.stderr
    f.write_text(json.dumps({"font_size": 999}), encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "subtitle.py"), "preview", "--style-json", str(f),
                        "--out", str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 1 and "超出范围" in r.stderr
