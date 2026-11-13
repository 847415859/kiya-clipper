#!/usr/bin/env python3
"""运行固定的 Agent 判断与程序化校验链路。

用法：
  python scripts/agent_run.py output/<场次>/candidates.json --max-ranks 3 --height 480
  python scripts/agent_run.py output/<场次>/candidates.json --ranks 2 5 --stop-after autoedit
  python scripts/agent_run.py output/<场次>/candidates.json --dry-run

状态和日志写在场次目录的 agent_run.json / agent_run.log。重复运行会从未完成阶段继续；
需要重新生成 AI 初稿时使用 --force，避免意外覆盖人工脚本。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper.agent import AgentOrchestrator, AgentRunError, STAGES  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("candidates", type=Path, help="screen.py 输出的 candidates.json")
    ap.add_argument("--ranks", type=int, nargs="+", help="只处理指定原始序号；不填时使用精排 pick")
    ap.add_argument("--max-ranks", type=int, default=3, help="没有 --ranks 时最多处理几条精排 pick，默认 3")
    ap.add_argument("--height", type=int, choices=[360, 480, 720, 1080], default=480)
    ap.add_argument("--config", type=Path, help="主播配置；不填时按场次目录推导")
    ap.add_argument("--force", action="store_true", help="重新执行阶段，并允许覆盖 AI 初稿；不会删除人工终稿")
    ap.add_argument("--timeout", type=int, help="单个工具的超时时间（秒）")
    ap.add_argument("--stop-after", choices=STAGES, help="完成指定阶段后停止，方便分步检查")
    ap.add_argument("--dry-run", action="store_true", help="只打印白名单命令，不调用模型、渲染器或写状态")
    args = ap.parse_args()

    try:
        runner = AgentOrchestrator(args.candidates, ranks=args.ranks, max_ranks=args.max_ranks,
                                   height=args.height, config=args.config, force=args.force,
                                   timeout=args.timeout)
        if args.dry_run:
            for i, command in enumerate(runner.plan(), 1):
                print(f"{i:02d}. {' '.join(command)}")
            return 0
        return runner.run(stop_after=args.stop_after)
    except AgentRunError as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
