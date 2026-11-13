"""固定工具链编排器。

Agent 的判断由现有的 rerank / autoedit 脚本完成；本模块只负责白名单工具的顺序、状态、重试入口和审计记录。
它不执行模型返回的任意命令，也不绕过剪辑脚本校验和成片质检。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path


STAGES = ("rerank", "cut", "autoedit", "validate", "render", "quality")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _unique_ranks(values: list[int] | None) -> list[int]:
    return sorted({int(x) for x in (values or []) if int(x) > 0})


class AgentRunError(RuntimeError):
    """编排阶段失败，详细 stdout / stderr 保存在 agent_run.log。"""


class AgentOrchestrator:
    """把现有脚本串成可恢复的固定流程。"""

    def __init__(self, candidates: Path, *, ranks: list[int] | None = None, max_ranks: int = 3,
                 height: int = 480, config: Path | None = None, force: bool = False,
                 timeout: int | None = None):
        self.worker_root = Path(__file__).resolve().parents[1]
        self.candidates = candidates.expanduser().resolve()
        self.session = self.candidates.parent
        self.requested_ranks = _unique_ranks(ranks)
        self.max_ranks = max(1, int(max_ranks))
        self.height = int(height)
        self.config = config.expanduser().resolve() if config else None
        self.force = force
        self.timeout = timeout
        self.state_path = self.session / "agent_run.json"
        self.log_path = self.session / "agent_run.log"
        self.state = self._load_state()

    def _signature(self) -> dict:
        return {"candidates": str(self.candidates), "requested_ranks": self.requested_ranks,
                "max_ranks": self.max_ranks, "height": self.height,
                "config": str(self.config) if self.config else None}

    def _new_state(self) -> dict:
        return {"version": 1, "status": "pending", "created_at": _now(),
                "updated_at": _now(), "signature": self._signature(), "ranks": [],
                "selection": {}, "stages": {}}

    def _load_state(self) -> dict:
        if not self.force and self.state_path.exists():
            try:
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
                if state.get("signature") == self._signature():
                    return state
            except (OSError, ValueError):
                pass
        return self._new_state()

    def _save(self) -> None:
        self.state["updated_at"] = _now()
        self.session.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    def _log(self, text: str) -> None:
        self.session.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(text)

    def _script(self, name: str) -> Path:
        return self.worker_root / "scripts" / name

    def _base_args(self) -> list[str]:
        return ["--config", str(self.config)] if self.config else []

    def _command(self, script: str, *args: str) -> list[str]:
        return [sys.executable, str(self._script(script)), str(self.candidates), *args]

    def _script_command(self, script: str, *args: str) -> list[str]:
        return [sys.executable, str(self._script(script)), *args]

    def _run_command(self, stage: str, command: list[str], attempt: int) -> int:
        started = _now()
        self._log(f"\n[{started}] stage={stage} attempt={attempt}\n$ {' '.join(command)}\n")
        try:
            result = subprocess.run(command, cwd=self.worker_root, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=self.timeout)
            output = (result.stdout or "") + ("\n[stderr]\n" + result.stderr if result.stderr else "")
            self._log(output)
            if output:
                print(output, end="" if output.endswith("\n") else "\n")
            return result.returncode
        except subprocess.TimeoutExpired as e:
            out = (e.stdout or "") if isinstance(e.stdout, str) else ""
            err = (e.stderr or "") if isinstance(e.stderr, str) else ""
            self._log(out + "\n[timeout]\n" + err + "\n")
            print(f"阶段 {stage} 超时（{self.timeout} 秒），详情见 {self.log_path}", file=sys.stderr)
            return 124
        except OSError as e:
            self._log(f"\n[process error] {type(e).__name__}: {e}\n")
            print(f"无法执行阶段 {stage}：{e}", file=sys.stderr)
            return 127

    def _run_stage(self, name: str, commands: list[list[str]]) -> None:
        old = self.state["stages"].get(name) or {}
        if old.get("status") == "done" and not self.force:
            print(f"跳过已完成阶段：{name}")
            return
        if not commands:
            self.state["stages"][name] = {"status": "skipped", "reason": "没有待处理候选", "finished_at": _now()}
            self._save()
            print(f"跳过阶段 {name}：没有待处理候选")
            return
        rec = {"status": "running", "started_at": _now(), "commands": [], "attempts": 0}
        self.state["stages"][name] = rec
        self.state["status"] = "running"
        self._save()
        for command in commands:
            rec["commands"].append(command)
            rec["attempts"] += 1
            self._save()
            code = self._run_command(name, command, rec["attempts"])
            if code != 0:
                rec.update(status="failed", returncode=code, finished_at=_now())
                self.state["status"] = "failed"
                self._save()
                raise AgentRunError(f"阶段 {name} 失败，退出码 {code}；详情见 {self.log_path}")
        rec.update(status="done", returncode=0, finished_at=_now())
        self._save()

    def _select_ranks(self) -> list[int]:
        if self.requested_ranks:
            self.state["selection"] = {"source": "cli", "ranks": self.requested_ranks}
            return self.requested_ranks
        path = self.session / "rerank.json"
        try:
            rows = json.loads(path.read_text(encoding="utf-8")).get("candidates") or []
        except (OSError, ValueError):
            rows = []
        picks = [int(x["rank"]) for x in rows if x.get("pick") and str(x.get("rank", "")).isdigit()]
        if picks:
            selected = picks[: self.max_ranks]
            source = "rerank.pick"
        else:
            selected = []
            source = "rerank.none"
        self.state["selection"] = {"source": source, "ranks": selected}
        return selected

    def _candidate_lengths(self) -> dict[int, float]:
        try:
            rows = json.loads(self.candidates.read_text(encoding="utf-8")).get("candidates") or []
        except (OSError, ValueError) as e:
            raise AgentRunError(f"候选文件无法读取：{e}") from e
        return {int(x["rank"]): max(0.0, float(x["clip_end"]) - float(x["clip_start"])) for x in rows}

    def _commands_for_ranks(self, script: str, extra: list[str] | None = None) -> list[list[str]]:
        args = ["--ranks", *(str(x) for x in self.state.get("ranks", []))]
        return [self._command(script, *args, *(extra or []), *self._base_args())]

    def _per_rank_commands(self, script: str, extra: list[str] | None = None, *, use_config: bool = True) -> list[list[str]]:
        out = []
        for rank in self.state.get("ranks", []):
            args = ["--rank", str(rank), *(extra or []), *(self._base_args() if use_config else [])]
            if script == "autoedit.py" and self.force:
                args.insert(0, "--force")
            out.append(self._command(script, *args))
        return out

    def _autoedit_commands(self) -> list[list[str]]:
        """只为没有人工终稿的候选生成 AI 初稿命令。"""
        commands = []
        reused, protected = [], []
        for rank in self.state.get("ranks", []):
            edit = self.session / "edits" / f"{rank:02d}.json"
            if edit.exists():
                try:
                    saved = json.loads(edit.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    saved = {}
                if saved.get("human_saved_at"):
                    protected.append(rank)
                    continue
                if not self.force:
                    reused.append(rank)
                    continue
            args = ["--rank", str(rank), *(self._base_args())]
            if self.force:
                args.insert(0, "--force")
            commands.append(self._command("autoedit.py", *args))
        self.state["selection"]["autoedit_reused"] = reused
        self.state["selection"]["human_protected"] = protected
        if protected:
            print(f"保留人工终稿，不重新生成 AI 初稿：{protected}")
        return commands

    def plan(self) -> list[list[str]]:
        """返回当前配置下的白名单命令，不写状态、不调用外部服务。"""
        ranks = self.requested_ranks or self.state.get("ranks")
        if not ranks:
            try:
                rows = json.loads(self.candidates.read_text(encoding="utf-8")).get("candidates") or []
                ranks = [int(x["rank"]) for x in rows[: self.max_ranks]]
            except (OSError, ValueError):
                ranks = []
        old = self.state.get("ranks")
        self.state["ranks"] = ranks
        commands = [self._command("rerank.py", *(self.requested_ranks and ["--ranks", *map(str, self.requested_ranks)] or []), *self._base_args())]
        if ranks:
            commands += self._commands_for_ranks("cut.py", ["--height", str(self.height)])
            commands += self._autoedit_commands()
            lengths = self._candidate_lengths()
            commands += [self._script_command("edit_check.py", str(self.session / "edits" / f"{rank:02d}.json"),
                                              "--clip-len", f"{lengths.get(rank, 0):g}") for rank in ranks]
            commands += self._per_rank_commands("render.py", ["--height", str(self.height)])
            commands += self._per_rank_commands("quality_check.py", use_config=False)
        self.state["ranks"] = old
        return commands

    def run(self, *, stop_after: str | None = None) -> int:
        if not self.candidates.is_file():
            raise AgentRunError(f"候选文件不存在：{self.candidates}")
        self._run_stage("rerank", [self._command("rerank.py", *(["--ranks", *map(str, self.requested_ranks)] if self.requested_ranks else []), *self._base_args())])
        self.state["ranks"] = self._select_ranks()
        self._save()
        if stop_after == "rerank":
            self.state["status"] = "stopped"
            self._save()
            return 0
        if not self.state["ranks"]:
            for name in STAGES[1:]:
                self._run_stage(name, [])
            self.state["status"] = "no_candidates"
            self._save()
            return 0
        self._run_stage("cut", self._commands_for_ranks("cut.py", ["--height", str(self.height)]))
        if stop_after == "cut":
            self.state["status"] = "stopped"
            self._save()
            return 0
        self._run_stage("autoedit", self._autoedit_commands())
        if stop_after == "autoedit":
            self.state["status"] = "stopped"
            self._save()
            return 0
        lengths = self._candidate_lengths()
        validate = [self._script_command("edit_check.py", str(self.session / "edits" / f"{rank:02d}.json"),
                                         "--clip-len", f"{lengths.get(rank, 0):g}") for rank in self.state["ranks"]]
        self._run_stage("validate", validate)
        if stop_after == "validate":
            self.state["status"] = "stopped"
            self._save()
            return 0
        self._run_stage("render", self._per_rank_commands("render.py", ["--height", str(self.height)]))
        if stop_after == "render":
            self.state["status"] = "stopped"
            self._save()
            return 0
        self._run_stage("quality", self._per_rank_commands("quality_check.py", use_config=False))
        self.state["status"] = "done"
        self._save()
        print(f"Agent 编排完成：候选 {self.state['ranks']}；状态 {self.state_path}")
        return 0
