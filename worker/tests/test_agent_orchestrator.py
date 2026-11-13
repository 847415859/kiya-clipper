import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper.agent import AgentOrchestrator  # noqa: E402


def make_candidates(tmp_path: Path, ranks=(1, 2, 3)) -> Path:
    session = tmp_path / "output" / "20261113-1200"
    session.mkdir(parents=True)
    path = session / "candidates.json"
    path.write_text(json.dumps({"candidates": [
        {"rank": r, "t": r * 10, "clip_start": r * 10 - 30, "clip_end": r * 10 + 30}
        for r in ranks
    ]}, ensure_ascii=False), encoding="utf-8")
    return path


def test_plan_uses_whitelisted_commands_and_explicit_ranks(tmp_path):
    path = make_candidates(tmp_path)
    config = tmp_path / "streamer.yaml"
    config.write_text("name: test\n", encoding="utf-8")
    runner = AgentOrchestrator(path, ranks=[3, 1], height=360, config=config)
    commands = runner.plan()
    assert commands[0][1].endswith("rerank.py")
    assert "--ranks" in commands[0] and commands[0][commands[0].index("--ranks") + 1:] == ["1", "3"]
    assert any(str(ROOT / "scripts" / "edit_check.py") == c[1] for c in commands)
    assert all(c[0] == sys.executable for c in commands)
    assert all("--height" not in c or "360" in c for c in commands)
    quality = [c for c in commands if Path(c[1]).name == "quality_check.py"]
    assert quality and "--config" not in quality[0]


def test_selects_only_rerank_picks_and_caps_count(tmp_path):
    path = make_candidates(tmp_path)
    rerank = path.parent / "rerank.json"
    rerank.write_text(json.dumps({"candidates": [
        {"rank": 1, "new_rank": 2, "pick": True},
        {"rank": 2, "new_rank": 1, "pick": True},
        {"rank": 3, "new_rank": 3, "pick": False},
    ]}), encoding="utf-8")
    runner = AgentOrchestrator(path, max_ranks=1)
    assert runner._select_ranks() == [1]
    assert runner.state["selection"] == {"source": "rerank.pick", "ranks": [1]}


def test_no_pick_does_not_fallback_to_unjudged_candidate(tmp_path):
    path = make_candidates(tmp_path)
    (path.parent / "rerank.json").write_text(json.dumps({"candidates": [
        {"rank": 1, "pick": False}, {"rank": 2, "pick": False}
    ]}), encoding="utf-8")
    runner = AgentOrchestrator(path)
    assert runner._select_ranks() == []
    assert runner.state["selection"]["source"] == "rerank.none"


def test_force_does_not_overwrite_human_edit(tmp_path):
    path = make_candidates(tmp_path, ranks=(1,))
    edit_dir = path.parent / "edits"
    edit_dir.mkdir()
    (edit_dir / "01.json").write_text(json.dumps({"human_saved_at": "2026-11-13T12:00:00"}), encoding="utf-8")
    runner = AgentOrchestrator(path, ranks=[1], force=True)
    runner.state["ranks"] = [1]
    assert runner._autoedit_commands() == []
    assert runner.state["selection"]["human_protected"] == [1]
