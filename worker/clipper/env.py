"""读取仓库根目录 .env（不覆盖已有的环境变量）。各脚本共用。"""
from __future__ import annotations

import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def load_env(path: Path | None = None) -> None:
    p = Path(path or os.environ.get("KIYA_ENV_FILE") or REPO / ".env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
        if m and m.group(1) not in os.environ and m.group(2):
            os.environ[m.group(1)] = m.group(2).strip('"').strip("'")
