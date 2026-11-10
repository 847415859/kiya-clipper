"""读 config/<主播>.yaml，并把后台“精排记忆”页里做的决定合并进去：

  - promoted_lore（梗转正）：确认过的梗 / 口头禅 / 话题并入主播档案 lore，热词、字幕关键词、标签、提示词都会用到
  - taboo（禁区话题）：cfg["taboo"] = 词列表，精排时命中就判不切，审核页标红

决定按主播隔离在 output/<主播>/memory_decisions.json（环境变量 KIYA_MEMORY_DECISIONS 可改路径，测试用）；
首位主播 kiya 兼容旧的 config/memory_decisions.json。yaml 本身不会被改写（里面的注释都保留）。
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import yaml

from .streamer import decisions_path as _streamer_decisions_path

REPO = Path(__file__).resolve().parents[2]
KIND_KEY = {"catchphrase": "catchphrases", "topic": "recurring_topics", "meme": "memes"}
KIND_LABEL = {"catchphrase": "口头禅", "topic": "常聊话题", "meme": "梗"}


def decisions_path() -> Path:
    return Path(os.environ.get("KIYA_MEMORY_DECISIONS") or _streamer_decisions_path())


def read_decisions(path: Path | None = None) -> dict:
    p = path or decisions_path()
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        return {}


def apply_decisions(cfg: dict, dec: dict | None) -> dict:
    """返回合并后的新配置（不改传入的 cfg）。"""
    cfg = copy.deepcopy(cfg or {})
    dec = dec or {}
    lore = cfg.setdefault("lore", {})
    for item in dec.get("promoted_lore") or []:
        text = str((item or {}).get("text", "")).strip()
        key = KIND_KEY.get((item or {}).get("kind"), "memes")
        if not text:
            continue
        cur = lore.setdefault(key, [])
        if text.lower() not in {str(x).lower() for x in cur}:
            cur.append(text)
    words = [str(w).strip() for w in cfg.get("taboo") or [] if str(w).strip()]
    for item in dec.get("taboo") or []:
        w = str(item.get("word", "") if isinstance(item, dict) else item).strip()
        if w and w.lower() not in {x.lower() for x in words}:
            words.append(w)
    cfg["taboo"] = words
    return cfg


def load_cfg(path: Path | str, decisions: dict | None = None) -> dict:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return apply_decisions(cfg, read_decisions() if decisions is None else decisions)


def find_taboo(text: str, words: list[str]) -> list[str]:
    low = (text or "").lower()
    return [w for w in words if w and w.lower() in low]
