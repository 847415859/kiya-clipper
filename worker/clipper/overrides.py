"""后台管理写入的覆盖配置（config/admin_overrides.json，不进 git）。

后台改的东西都落在这个文件里，worker 读取时和 config/*.yaml 合并，所以命令行、监控自动剪切和后台用的是同一套设置：
  {"hotwords": ["额外热词", ...],                 # 合并进热词表（hotwords.py sync 后生效）
   "corrections": {"错字": "正确", ...},           # 合并进字幕纠错
   "subtitle_style": {...}, "subtitle_style_name": "后台默认样式名"}   # cut.py 不指定样式时用它
文件不存在或格式不对时当作空，不影响 worker 运行。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def overrides_path() -> Path:
    return Path(os.environ.get("KIYA_OVERRIDES") or REPO / "config" / "admin_overrides.json")


def load_overrides() -> dict:
    try:
        data = json.loads(overrides_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}
