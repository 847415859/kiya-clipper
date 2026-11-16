"""固定工具链编排器。

Agent 的判断由现有的 rerank / autoedit 脚本完成；本模块只负责白名单工具的顺序、状态、续跑和审计记录。
它不执行模型返回的任意命令，也不绕过剪辑脚本校验和成片质检。

续跑按“阶段 + 候选”记录：每条候选的每一步单独记完成情况，中途失败或精排选中的候选变了，
下次运行只补没做过的那几步，不会把已经渲染好的候选再渲染一遍。
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


STAGES = ("rerank", "cut", "autoedit", "validate", "render", "quality")
QUALITY_FAILED = 2          # quality_check.py：质检没通过时的退出码（其他非 0 是脚本本身出错）


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _unique_ranks(values: list[int] | None) -> list[int]:
    return sorted({int(x) for x in (values or []) if int(x) > 0})


class AgentRunError(RuntimeError):
    """编排阶段失败，详细输出保存在 agent_run.log。"""


class AgentOrchestrator:
    """把现有脚本串成可恢复的固定流程。"""

    def __init__(self, candidates: Path, *, ranks: list[int] | None = None, max_ranks: int = 3,
                 height: int = 480, config: Path | None = None, force: bool = False,
                 timeout: int | None = None, rerank: bool = False, strict_quality: bool = False):
        self.worker_root = Path(__file__).resolve().parents[1]
        self.candidates = Path(candidates).expanduser().resolve()
        self.session = self.candidates.parent
        self.requested_ranks = _unique_ranks(ranks)
        self.max_ranks = max(1, int(max_ranks))
        self.height = int(height)
        self.config = Path(config).expanduser().resolve() if config else None
        self.force = force                  # 忽略已有进度，全部重跑（含精排；人工终稿仍然保护）
        self.rerank = rerank                # 已有精排结果也重新精排
        self.strict_quality = strict_quality  # 质检没通过时让编排失败（默认只记录）
        self.timeout = timeout
        self.state_path = self.session / "agent_run.json"
        self.log_path = self.session / "agent_run.log"
        self.plan_note = ""
        self.state = self._load_state()

    # ------------------------------------------------------------------ 状态
    def _signature(self) -> dict:
        return {"candidates": str(self.candidates), "requested_ranks": self.requested_ranks,
                "max_ranks": self.max_ranks, "height": self.height,
                "config": str(self.config) if self.config else None}

    def _new_state(self) -> dict:
        return {"version": 2, "status": "pending", "created_at": _now(),
                "updated_at": _now(), "signature": self._signature(), "ranks": [],
                "selection": {}, "stages": {}, "steps": {}, "warnings": {}, "quality": {}}

    def _load_state(self) -> dict:
        if not self.force and self.state_path.exists():
            try:
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
                if state.get("signature") == self._signature() and state.get("version") == 2:
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

    # ------------------------------------------------------------------ 命令
    def _script(self, name: str) -> Path:
        return self.worker_root / "scripts" / name

    def _base_args(self) -> list[str]:
        return ["--config", str(self.config)] if self.config else []

    def _command(self, script: str, *args: str) -> list[str]:
        return [sys.executable, str(self._script(script)), str(self.candidates), *args]

    def _script_command(self, script: str, *args: str) -> list[str]:
        return [sys.executable, str(self._script(script)), *args]

    def _kill_tree(self, proc: subprocess.Popen) -> None:
        """超时时连同子进程（比如渲染时起的 node / Chrome）一起结束，避免在小内存服务器上残留。"""
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        try:
            proc.kill()
        except OSError:
            pass

    def _run_command(self, stage: str, command: list[str], attempt: int) -> tuple[int, str]:
        """运行一条命令，边跑边打印、边写日志。返回 (退出码, 全部输出)。"""
        self._log(f"\n[{_now()}] stage={stage} attempt={attempt}\n$ {' '.join(command)}\n")
        kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        try:
            proc = subprocess.Popen(command, cwd=self.worker_root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace", **kwargs)
        except OSError as e:
            self._log(f"\n[process error] {type(e).__name__}: {e}\n")
            print(f"无法执行阶段 {stage}：{e}", file=sys.stderr)
            return 127, ""
        timed_out = threading.Event()
        timer = None
        if self.timeout:
            def on_timeout() -> None:
                timed_out.set()
                self._kill_tree(proc)
            timer = threading.Timer(self.timeout, on_timeout)
            timer.start()
        lines: list[str] = []
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.append(line)
                self._log(line)
                print(line, end="", flush=True)
            proc.wait()
        finally:
            if timer:
                timer.cancel()
        output = "".join(lines)
        if timed_out.is_set():
            self._log("\n[timeout]\n")
            print(f"阶段 {stage} 超时（{self.timeout} 秒），已结束它和它启动的子进程；详情见 {self.log_path}", file=sys.stderr)
            return 124, output
        return proc.returncode, output

    def _run_stage(self, name: str, steps: list[tuple[str, list[str]]],
                   on_result=None) -> None:
        """steps：[(步骤键, 命令)]。已完成的步骤键跳过；on_result(键, 退出码, 输出) 返回 True 表示可以接受非 0 退出码。"""
        rec = self.state["stages"].setdefault(name, {})
        if not steps:
            rec.update(status="skipped", reason="没有待处理候选", finished_at=_now())
            self._save()
            print(f"跳过阶段 {name}：没有待处理候选")
            return
        todo = [(k, c) for k, c in steps if self.state["steps"].get(k, {}).get("status") != "done"]
        if not todo:
            rec.update(status="done", finished_at=rec.get("finished_at") or _now())
            self._save()
            print(f"跳过已完成阶段：{name}")
            return
        rec.update(status="running", started_at=_now())
        rec.pop("returncode", None)
        self.state["status"] = "running"
        self._save()
        for key, command in todo:
            step = self.state["steps"].setdefault(key, {"attempts": 0})
            step.update(status="running", command=command, attempts=step.get("attempts", 0) + 1, started_at=_now())
            self._save()
            code, output = self._run_command(name, command, step["attempts"])
            accepted = bool(on_result(key, code, output)) if on_result else False
            accepted = accepted or code == 0
            step.update(status="done" if accepted else "failed", returncode=code, finished_at=_now())
            if not accepted:
                rec.update(status="failed", returncode=code, failed_step=key, finished_at=_now())
                self.state["status"] = "failed"
                self._save()
                raise AgentRunError(f"阶段 {name}（{key}）失败，退出码 {code}；详情见 {self.log_path}")
            self._save()
        rec.update(status="done", finished_at=_now())
        rec.pop("failed_step", None)
        self._save()

    # ------------------------------------------------------------------ 选候选
    def _rerank_rows(self) -> list[dict]:
        try:
            return json.loads((self.session / "rerank.json").read_text(encoding="utf-8")).get("candidates") or []
        except (OSError, ValueError):
            return []

    def _reranked(self) -> set[int]:
        """rerank.json 里已经有大模型结论的候选。"""
        return {int(x["rank"]) for x in self._rerank_rows()
                if x.get("llm") and str(x.get("rank", "")).isdigit()}

    def _rerank_steps(self) -> list[tuple[str, list[str]]]:
        """已有精排结果时默认复用（精排要调大模型、花钱，还会覆盖 rerank.json）。--force / --rerank 才重做。"""
        done = self._reranked()
        fresh = self.force or self.rerank
        if self.requested_ranks:
            need = self.requested_ranks if fresh else [r for r in self.requested_ranks if r not in done]
            if not need:
                return []
            return [("rerank:" + ",".join(map(str, need)),
                     self._command("rerank.py", "--ranks", *map(str, need), *self._base_args()))]
        if done and not fresh:
            return []
        return [("rerank:all", self._command("rerank.py", *self._base_args()))]

    def _select_ranks(self) -> list[int]:
        if self.requested_ranks:
            self.state["selection"] = {"source": "cli", "ranks": self.requested_ranks}
            return self.requested_ranks
        rows = self._rerank_rows()
        # 显式按精排后的名次排序，不依赖 rerank.json 里的行顺序
        rows = sorted(rows, key=lambda x: (x.get("new_rank") is None, x.get("new_rank") or 0))
        picks = [int(x["rank"]) for x in rows if x.get("pick") and str(x.get("rank", "")).isdigit()]
        selected = picks[: self.max_ranks]
        self.state["selection"] = {"source": "rerank.pick" if selected else "rerank.none", "ranks": selected}
        return selected

    def _candidate_lengths(self) -> dict[int, float]:
        try:
            rows = json.loads(self.candidates.read_text(encoding="utf-8")).get("candidates") or []
        except (OSError, ValueError) as e:
            raise AgentRunError(f"候选文件无法读取：{e}") from e
        return {int(x["rank"]): max(0.0, float(x["clip_end"]) - float(x["clip_start"])) for x in rows}

    # ------------------------------------------------------------------ 各阶段的步骤
    def _cut_steps(self, ranks: list[int]) -> list[tuple[str, list[str]]]:
        return [(f"cut:{r}", self._command("cut.py", "--ranks", str(r), "--height", str(self.height), *self._base_args()))
                for r in ranks]

    def _autoedit_commands(self, ranks: list[int] | None = None) -> list[list[str]]:
        """只为需要的候选生成 AI 初稿命令：
        - 人工在后台保存过（human_saved_at）→ 永远不覆盖，直接用人工终稿往下走；
        - 已有剪辑脚本但不是人工保存的 → 默认沿用，--force 才重新生成；
        - 没有剪辑脚本 → 生成。"""
        commands, reused, protected = [], [], []
        for rank in (self.state.get("ranks", []) if ranks is None else ranks):
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
            args = ["--rank", str(rank), *self._base_args()]
            if self.force:
                args.insert(0, "--force")
            commands.append(self._command("autoedit.py", *args))
        self.state.setdefault("selection", {})
        self.state["selection"]["autoedit_reused"] = reused
        self.state["selection"]["human_protected"] = protected
        if protected:
            print(f"保留人工终稿，不重新生成 AI 初稿：{protected}")
        return commands

    def _autoedit_steps(self, ranks: list[int]) -> list[tuple[str, list[str]]]:
        steps = []
        for c in self._autoedit_commands(ranks):
            steps.append((f"autoedit:{int(c[c.index('--rank') + 1])}", c))
        return steps

    def _validate_steps(self, ranks: list[int]) -> list[tuple[str, list[str]]]:
        lengths = self._candidate_lengths()
        return [(f"validate:{r}", self._script_command("edit_check.py", str(self.session / "edits" / f"{r:02d}.json"),
                                                        "--clip-len", f"{lengths.get(r, 0):g}")) for r in ranks]

    def _render_steps(self, ranks: list[int]) -> list[tuple[str, list[str]]]:
        return [(f"render:{r}", self._command("render.py", "--rank", str(r), "--height", str(self.height), *self._base_args()))
                for r in ranks]

    def _quality_steps(self, ranks: list[int]) -> list[tuple[str, list[str]]]:
        return [(f"quality:{r}", self._command("quality_check.py", "--rank", str(r))) for r in ranks]

    def _on_validate(self, key: str, code: int, output: str) -> bool:
        """把 edit_check 的提醒（比如贴图不在库里、渲染会失败）存进状态文件，不只留在日志里。"""
        rank = key.split(":", 1)[1]
        for line in reversed(output.strip().splitlines()):
            try:
                warnings = json.loads(line).get("warnings") or []
            except (ValueError, AttributeError):
                continue
            if warnings:
                self.state["warnings"][rank] = warnings
                print(f"#{rank} 剪辑脚本提醒：" + "；".join(warnings))
            else:
                self.state["warnings"].pop(rank, None)
            break
        return False

    def _on_quality(self, key: str, code: int, output: str) -> bool:
        """质检没通过（退出码 2）默认只记录、不让整条编排失败；--strict-quality 时才失败。"""
        rank = key.split(":", 1)[1]
        self.state["quality"][rank] = {"ok": code == 0, "returncode": code}
        if code == QUALITY_FAILED and not self.strict_quality:
            print(f"#{rank} 质检没通过，已记录，继续（加 --strict-quality 时会让编排失败）")
            return True
        return False

    # ------------------------------------------------------------------ 对外
    def plan(self) -> list[list[str]]:
        """返回将要执行的白名单命令，不写状态、不调用外部服务。和 run() 用同一套选候选规则。"""
        saved = json.loads(json.dumps(self.state))
        try:
            commands = [c for _, c in self._rerank_steps()]
            if self.requested_ranks:
                ranks = self.requested_ranks
            elif commands:
                self.plan_note = (f"要先精排，精排完成后按 pick 选前 {self.max_ranks} 条，"
                                  "所以后面的命令现在还列不出来")
                return commands
            else:
                ranks = self._select_ranks()
            if not ranks:
                self.plan_note = "精排没有 pick，不会剪片和渲染"
                return commands
            self.plan_note = f"处理候选：{ranks}"
            for steps in (self._cut_steps(ranks), self._autoedit_steps(ranks), self._validate_steps(ranks),
                          self._render_steps(ranks), self._quality_steps(ranks)):
                commands += [c for _, c in steps]
            return commands
        finally:
            self.state = saved

    def _stop(self, stage: str, stop_after: str | None) -> bool:
        if stop_after == stage:
            self.state["status"] = "stopped"
            self._save()
            return True
        return False

    def run(self, *, stop_after: str | None = None) -> int:
        if not self.candidates.is_file():
            raise AgentRunError(f"候选文件不存在：{self.candidates}")
        steps = self._rerank_steps()
        if steps:
            if self.rerank:
                for key, _ in steps:
                    self.state["steps"].pop(key, None)
            self._run_stage("rerank", steps)
        else:
            self.state["stages"]["rerank"] = {"status": "reused", "reason": "沿用已有的 rerank.json（--rerank 可重新精排）",
                                              "finished_at": _now()}
            print("沿用已有的精排结果 rerank.json（要重新精排加 --rerank）")
        ranks = self._select_ranks()
        self.state["ranks"] = ranks
        self._save()
        if self._stop("rerank", stop_after):
            return 0
        if not ranks:
            for name in STAGES[1:]:
                self._run_stage(name, [])
            self.state["status"] = "no_candidates"
            self._save()
            return 0
        self._run_stage("cut", self._cut_steps(ranks))
        if self._stop("cut", stop_after):
            return 0
        self._run_stage("autoedit", self._autoedit_steps(ranks))
        if self._stop("autoedit", stop_after):
            return 0
        self._run_stage("validate", self._validate_steps(ranks), self._on_validate)
        if self._stop("validate", stop_after):
            return 0
        self._run_stage("render", self._render_steps(ranks))
        if self._stop("render", stop_after):
            return 0
        if self.strict_quality:
            for rank in ranks:
                if not self.state["quality"].get(str(rank), {}).get("ok", True):
                    self.state["steps"].pop(f"quality:{rank}", None)
        self._run_stage("quality", self._quality_steps(ranks), self._on_quality)
        failed = [r for r, q in self.state["quality"].items() if not q.get("ok") and int(r) in ranks]
        self.state["status"] = "done_with_quality_issues" if failed else "done"
        self._save()
        tail = f"；质检没通过：{failed}" if failed else ""
        print(f"Agent 编排完成：候选 {ranks}{tail}；状态 {self.state_path}")
        return 0
