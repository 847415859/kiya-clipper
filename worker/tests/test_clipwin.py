"""审核片段剪切范围：按精排修正后的起止，候选窗口（身份）保持不变。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper.clipwin import clip_origin, cut_window, read_windows, write_windows  # noqa: E402

CAND = {"rank": 1, "t": 5125.0, "clip_start": 5095.0, "clip_end": 5155.0}


def test_cut_window_uses_refined_when_it_contains_peak():
    a, b, ref = cut_window(CAND, {"start": 5085.0, "end": 5173.0})
    assert (a, b, ref) == (5085.0, 5173.0, True)          # 88 秒，且比候选窗口两头都宽


def test_cut_window_falls_back():
    # 没有精排 / 精排缺字段 / 太短 / 不包含高峰 → 候选窗口
    for r in (None, {}, {"start": 5100.0}, {"start": 5120.0, "end": 5123.0}, {"start": 5000.0, "end": 5100.0},
              {"start": "x", "end": "y"}):
        assert cut_window(CAND, r) == (5095.0, 5155.0, False)


def test_cut_window_clamps_negative_start():
    c = {"rank": 2, "t": 10.0, "clip_start": 0.0, "clip_end": 40.0}
    assert cut_window(c, {"start": -5.0, "end": 70.0}) == (0.0, 70.0, True)


def test_windows_file_roundtrip_and_origin(tmp_path):
    assert read_windows(tmp_path) == {}
    assert clip_origin({}, CAND) == 5095.0                  # 旧场次没有 windows.json
    write_windows(tmp_path, {"01": {"start": 5085.0, "end": 5173.0, "refined": True}})
    write_windows(tmp_path, {"02": {"start": 100.0, "end": 160.0, "refined": False}})   # 合并，不覆盖 01
    w = read_windows(tmp_path)
    assert set(w) == {"01", "02"}
    assert clip_origin(w, CAND) == 5085.0
    assert clip_origin(w, {"rank": 3, "clip_start": 9.0}) == 9.0   # 没记录的序号退回候选窗口


def test_read_windows_tolerates_garbage(tmp_path):
    (tmp_path / "clips").mkdir()
    (tmp_path / "clips" / "windows.json").write_text("not json", encoding="utf-8")
    assert read_windows(tmp_path) == {}
    (tmp_path / "clips" / "windows.json").write_text(json.dumps([1, 2]), encoding="utf-8")
    assert read_windows(tmp_path) == {}
