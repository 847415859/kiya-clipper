"""多主播抽象层 streamer.py 的测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper import streamer as S  # noqa: E402


def test_normalize():
    assert S.normalize("Kiya") == "kiya"
    assert S.normalize("kiya2") == "kiya2"
    assert S.normalize("kiya-2") == "kiya-2"
    assert S.normalize("kiya_2") == "kiya_2"
    with pytest.raises(ValueError):
        S.normalize("")           # 空
    with pytest.raises(ValueError):
        S.normalize("2kiya")      # 数字开头
    with pytest.raises(ValueError):
        S.normalize("kiya/..")    # 路径穿越
    with pytest.raises(ValueError):
        S.normalize("kiya 中文")  # 含空格和中文
    with pytest.raises(ValueError):
        S.normalize("a" * 33)     # 超长


def test_default_streamer(monkeypatch):
    monkeypatch.delenv("KIYA_STREAMER", raising=False)
    assert S.default_streamer() == "kiya"
    monkeypatch.setenv("KIYA_STREAMER", "Miko")
    assert S.default_streamer() == "miko"


def test_streamer_from_args(monkeypatch):
    monkeypatch.delenv("KIYA_STREAMER", raising=False)
    assert S.streamer_from_args(None) == "kiya"
    assert S.streamer_from_args("Miko") == "miko"
    with pytest.raises(ValueError):
        S.streamer_from_args("bad id")


def test_streamer_from_session_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("KIYA_STREAMER", raising=False)
    # 新结构 output/<streamer>/<场次>
    new_dir = tmp_path / "output" / "miko" / "20261110-2000"
    assert S.streamer_from_session_dir(new_dir) == "miko"
    # 旧结构 output/<场次> → 默认主播
    old_dir = tmp_path / "output" / "20261110-2000"
    assert S.streamer_from_session_dir(old_dir) == "kiya"


def test_config_path(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "REPO", tmp_path)
    (tmp_path / "config" / "streamers").mkdir(parents=True)
    (tmp_path / "config").mkdir(exist_ok=True)
    # 新路径存在 → 用新路径
    new_cfg = tmp_path / "config" / "streamers" / "miko.yaml"
    new_cfg.write_text("streamer: {name: miko}", encoding="utf-8")
    assert S.config_path("miko") == new_cfg
    # 新路径不存在、旧路径存在 → 回退旧路径
    legacy_cfg = tmp_path / "config" / "kiya.yaml"
    legacy_cfg.write_text("streamer: {name: kiya}", encoding="utf-8")
    assert S.config_path("kiya") == legacy_cfg
    # 都不存在 → 返回新路径（读取时会报错）
    assert S.config_path("nobody") == tmp_path / "config" / "streamers" / "nobody.yaml"


def test_output_and_eval_roots(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "REPO", tmp_path)
    assert S.output_root("miko") == tmp_path / "worker" / "output" / "miko"
    assert S.session_dir("miko", "20261110-2000") == tmp_path / "worker" / "output" / "miko" / "20261110-2000"
    assert S.eval_root("miko") == tmp_path / "eval" / "miko"
    assert S.reviews_dir("miko") == tmp_path / "eval" / "miko" / "reviews"
    monkeypatch.delenv("KIYA_STREAMER", raising=False)
    assert S.output_root() == tmp_path / "worker" / "output" / "kiya"


def test_runtime_state_paths(tmp_path, monkeypatch):
    """admin_overrides.json / memory_decisions.json 按主播隔离；kiya 兼容旧 config/ 路径。"""
    monkeypatch.setattr(S, "REPO", tmp_path)
    monkeypatch.delenv("KIYA_STREAMER", raising=False)
    # 新主播：直接 output/<id>/ 下
    assert S.overrides_path("miko") == tmp_path / "worker" / "output" / "miko" / "admin_overrides.json"
    assert S.decisions_path("miko") == tmp_path / "worker" / "output" / "miko" / "memory_decisions.json"
    # kiya：旧 config/ 文件存在时回退旧路径
    legacy_ov = tmp_path / "config" / "admin_overrides.json"
    legacy_dc = tmp_path / "config" / "memory_decisions.json"
    legacy_ov.parent.mkdir(parents=True)
    legacy_ov.write_text("{}", encoding="utf-8")
    legacy_dc.write_text("{}", encoding="utf-8")
    assert S.overrides_path("kiya") == legacy_ov
    assert S.decisions_path("kiya") == legacy_dc
    # kiya：旧文件不存在时用新路径
    legacy_ov.unlink()
    legacy_dc.unlink()
    assert S.overrides_path("kiya") == tmp_path / "worker" / "output" / "kiya" / "admin_overrides.json"
    assert S.decisions_path("kiya") == tmp_path / "worker" / "output" / "kiya" / "memory_decisions.json"


def test_legacy_output_and_eval_fallback(tmp_path, monkeypatch):
    """kiya 兼容旧结构：output/kiya 不存在而 output/ 下直接是场次或 _memory 时，继续用 output/ 本身。"""
    monkeypatch.setattr(S, "REPO", tmp_path)
    monkeypatch.delenv("KIYA_STREAMER", raising=False)
    out = tmp_path / "worker" / "output"
    ev = tmp_path / "eval"
    # 全新部署：什么都没有 → 新结构
    assert S.output_root("kiya") == out / "kiya"
    assert S.eval_root("kiya") == ev / "kiya"
    # 只有 _llm_cache（try_llm 会建）不算旧数据
    (out / "_llm_cache").mkdir(parents=True)
    assert S.output_root("kiya") == out / "kiya"
    # 旧场次：output/<场次>/candidates.json
    (out / "20261001-2000").mkdir()
    (out / "20261001-2000" / "candidates.json").write_text("{}", encoding="utf-8")
    assert S.output_root("kiya") == out
    assert S.output_root("miko") == out / "miko"            # 其他主播永远走新结构
    assert S.decisions_path("kiya") == out / "memory_decisions.json"
    # 旧评测：eval/reviews
    (ev / "reviews").mkdir(parents=True)
    assert S.eval_root("kiya") == ev
    assert S.reviews_dir("kiya") == ev / "reviews"
    assert S.eval_root("miko") == ev / "miko"
    # 迁移后 output/kiya 存在 → 自动走新结构
    (out / "kiya").mkdir()
    (ev / "kiya").mkdir()
    assert S.output_root("kiya") == out / "kiya"
    assert S.eval_root("kiya") == ev / "kiya"
