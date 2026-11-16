import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper.agent import AgentOrchestrator, AgentRunError  # noqa: E402


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
    i = commands[0].index("--ranks")
    assert commands[0][i + 1:i + 3] == ["1", "3"]
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
    assert runner._select_ranks() == [2]          # 两条都是 pick，取精排名次更靠前的（new_rank 1）
    assert runner.state["selection"] == {"source": "rerank.pick", "ranks": [2]}


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


# ---------------------------------------------------------------- 用假脚本跑完整 run()
FAKE = """import json, os, sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
name = Path(__file__).name
with open(root / "calls.log", "a", encoding="utf-8") as f:
    f.write(json.dumps([name] + sys.argv[1:]) + "\\n")
codes = json.loads((root / "codes.json").read_text()) if (root / "codes.json").exists() else {}
if name == "edit_check.py":
    print(json.dumps({"edit": {}, "warnings": codes.get("warnings", [])}))
if name == "autoedit.py":
    r = sys.argv[sys.argv.index("--rank") + 1]
    d = Path(sys.argv[1]).parent / "edits"; d.mkdir(exist_ok=True)
    (d / f"{int(r):02d}.json").write_text("{}")
sys.exit(int(codes.get(name, 0)))
"""


def fake_root(tmp_path: Path, codes: dict | None = None) -> Path:
    root = tmp_path / "fakeworker"
    (root / "scripts").mkdir(parents=True)
    for n in ("rerank.py", "cut.py", "autoedit.py", "edit_check.py", "render.py", "quality_check.py"):
        (root / "scripts" / n).write_text(FAKE, encoding="utf-8")
    if codes:
        (root / "codes.json").write_text(json.dumps(codes), encoding="utf-8")
    return root


def calls(root: Path) -> list[list[str]]:
    f = root / "calls.log"
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


def write_rerank(path: Path, picks: list[int], ranks=(1, 2, 3)) -> None:
    rows = [{"rank": r, "new_rank": i + 1, "llm": {"score": 5}, "pick": r in picks} for i, r in enumerate(ranks)]
    (path.parent / "rerank.json").write_text(json.dumps({"candidates": rows}), encoding="utf-8")


def runner_for(path: Path, root: Path, **kw) -> AgentOrchestrator:
    r = AgentOrchestrator(path, **kw)
    r.worker_root = root
    return r


def test_dry_run_selects_same_ranks_as_run(tmp_path):
    path = make_candidates(tmp_path, ranks=(1, 2, 3, 4, 5, 6))
    (path.parent / "rerank.json").write_text(json.dumps({"candidates": [
        {"rank": 6, "new_rank": 2, "llm": {"score": 6}, "pick": True},
        {"rank": 5, "new_rank": 1, "llm": {"score": 6}, "pick": True},
        {"rank": 1, "new_rank": 3, "llm": {"score": 6}, "pick": False},
    ]}), encoding="utf-8")
    runner = AgentOrchestrator(path, max_ranks=2)
    planned = sorted({int(c[c.index("--rank") + 1]) for c in runner.plan() if "--rank" in c})
    assert planned == [5, 6]
    assert runner._select_ranks() == [5, 6]       # 按 new_rank 排序，不看行顺序
    assert not (path.parent / "agent_run.json").exists()


def test_dry_run_without_rerank_explains_instead_of_guessing(tmp_path):
    path = make_candidates(tmp_path)
    runner = AgentOrchestrator(path)
    commands = runner.plan()
    assert [Path(c[1]).name for c in commands] == ["rerank.py"]
    assert "精排完成后" in runner.plan_note


def test_existing_rerank_is_reused_and_not_rerun(tmp_path):
    path = make_candidates(tmp_path)
    write_rerank(path, picks=[2])
    root = fake_root(tmp_path)
    assert runner_for(path, root).run() == 0
    names = [c[0] for c in calls(root)]
    assert "rerank.py" not in names
    assert names == ["cut.py", "autoedit.py", "edit_check.py", "render.py", "quality_check.py"]
    state = json.loads((path.parent / "agent_run.json").read_text(encoding="utf-8"))
    assert state["stages"]["rerank"]["status"] == "reused" and state["status"] == "done"


def test_rerank_flag_forces_new_rerank(tmp_path):
    path = make_candidates(tmp_path)
    write_rerank(path, picks=[2])
    root = fake_root(tmp_path)
    runner_for(path, root, rerank=True).run(stop_after="rerank")
    assert [c[0] for c in calls(root)] == ["rerank.py"]


def test_resume_only_reruns_unfinished_ranks(tmp_path):
    path = make_candidates(tmp_path)
    write_rerank(path, picks=[1, 2])
    root = fake_root(tmp_path)
    runner_for(path, root).run(stop_after="render")
    first = calls(root)
    assert sum(1 for c in first if c[0] == "render.py") == 2
    # 精排结论变了：多了一条 pick。续跑时只给新候选补剪片 / 初稿 / 渲染，旧的不重渲
    write_rerank(path, picks=[1, 2, 3])
    (root / "calls.log").unlink()
    runner_for(path, root).run()
    again = calls(root)
    rendered = [c[c.index("--rank") + 1] for c in again if c[0] == "render.py"]
    assert rendered == ["3"]
    assert any(c[0] == "cut.py" and c[c.index("--ranks") + 1] == "3" for c in again)
    assert sum(1 for c in again if c[0] == "quality_check.py") == 3


def test_failed_render_resumes_from_failed_rank(tmp_path):
    path = make_candidates(tmp_path)
    write_rerank(path, picks=[1, 2])
    root = fake_root(tmp_path, {"render.py": 1})
    import pytest
    with pytest.raises(AgentRunError):
        runner_for(path, root).run()
    (root / "codes.json").unlink()
    (root / "calls.log").unlink()
    runner_for(path, root).run()
    rendered = [c[c.index("--rank") + 1] for c in calls(root) if c[0] == "render.py"]
    assert rendered == ["1", "2"]                  # 第 1 条第一次就失败了，所以两条都要补
    assert not any(c[0] in ("cut.py", "autoedit.py") for c in calls(root))


def test_quality_failure_is_recorded_not_fatal_by_default(tmp_path):
    path = make_candidates(tmp_path)
    write_rerank(path, picks=[1])
    root = fake_root(tmp_path, {"quality_check.py": 2})
    assert runner_for(path, root).run() == 0
    state = json.loads((path.parent / "agent_run.json").read_text(encoding="utf-8"))
    assert state["status"] == "done_with_quality_issues"
    assert state["quality"]["1"] == {"ok": False, "returncode": 2}


def test_strict_quality_makes_run_fail(tmp_path):
    path = make_candidates(tmp_path)
    write_rerank(path, picks=[1])
    root = fake_root(tmp_path, {"quality_check.py": 2})
    import pytest
    with pytest.raises(AgentRunError):
        runner_for(path, root, strict_quality=True).run()


def test_rerank_flag_runs_again_after_completed_rerank(tmp_path):
    path = make_candidates(tmp_path)
    write_rerank(path, picks=[1])
    root = fake_root(tmp_path)
    for _ in range(2):
        runner_for(path, root, rerank=True).run(stop_after="rerank")
    assert [c[0] for c in calls(root)] == ["rerank.py", "rerank.py"]


def test_strict_quality_rechecks_previous_nonfatal_failure(tmp_path):
    import pytest
    path = make_candidates(tmp_path)
    write_rerank(path, picks=[1])
    root = fake_root(tmp_path, {"quality_check.py": 2})
    runner_for(path, root).run()
    (root / "calls.log").unlink()
    with pytest.raises(AgentRunError):
        runner_for(path, root, strict_quality=True).run()
    assert [c[0] for c in calls(root)] == ["quality_check.py"]


def test_validate_warnings_saved_in_state(tmp_path):
    path = make_candidates(tmp_path)
    write_rerank(path, picks=[1])
    root = fake_root(tmp_path, {"warnings": ["贴图库里没有 a.png，渲染会失败"]})
    runner_for(path, root).run(stop_after="validate")
    state = json.loads((path.parent / "agent_run.json").read_text(encoding="utf-8"))
    assert state["warnings"]["1"] == ["贴图库里没有 a.png，渲染会失败"]


def test_existing_non_human_edit_is_reused_without_force(tmp_path):
    path = make_candidates(tmp_path, ranks=(1,))
    edit_dir = path.parent / "edits"
    edit_dir.mkdir()
    (edit_dir / "01.json").write_text("{}", encoding="utf-8")       # 老数据：没有 .ai.json，也不是人工保存
    runner = AgentOrchestrator(path, ranks=[1])
    assert runner._autoedit_commands([1]) == []
    assert runner.state["selection"]["autoedit_reused"] == [1]
    forced = AgentOrchestrator(path, ranks=[1], force=True)._autoedit_commands([1])
    assert forced and "--force" in forced[0]


def test_timeout_kills_child_process_tree(tmp_path):
    import os
    if os.name == "nt":
        return
    path = make_candidates(tmp_path, ranks=(1,))
    marker = tmp_path / "grandchild_alive"
    root = fake_root(tmp_path)
    (root / "scripts" / "rerank.py").write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3); open({str(marker)!r}, \"w\").write(\"x\")'])\n"
        "time.sleep(30)\n", encoding="utf-8")
    runner = runner_for(path, root, ranks=[1], timeout=1)
    import pytest, time
    with pytest.raises(AgentRunError):
        runner.run()
    time.sleep(3.5)
    assert not marker.exists()
