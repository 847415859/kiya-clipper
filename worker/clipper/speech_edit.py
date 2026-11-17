"""按整场时间和原文绑定的切片字幕修订；不改写场次转写。"""
from __future__ import annotations

import math


def sentence_text(sentence: dict) -> str:
    words = sentence.get("words") or []
    return "".join(str(w[2]) for w in words if len(w) >= 3) or str(sentence.get("text") or "")


def validate_overrides(rows) -> list[dict]:
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError("逐句字幕修订必须是列表，最多 1000 条")
    out, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("逐句字幕修订格式不对")
        a, b = row.get("t0"), row.get("t1")
        if any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) for t in (a, b)) or not 0 <= a < b <= 86400:
            raise ValueError("逐句字幕修订需要有效的整场起止秒数")
        original, text = row.get("original"), row.get("text")
        if any(not isinstance(s, str) or not s.strip() or len(s) > 1000 for s in (original, text)):
            raise ValueError("字幕原文与修订内容须为 1–1000 字")
        key = (round(a, 2), round(b, 2))
        if key in seen:
            raise ValueError("同一句字幕不能重复修订")
        seen.add(key)
        out.append({"t0": float(a), "t1": float(b), "original": original, "text": text.strip()})
    return out


def apply_overrides(sentences: list[dict], rows: list[dict]) -> tuple[list[dict], list[dict]]:
    pending = {(round(r["t0"], 2), round(r["t1"], 2)): r for r in validate_overrides(rows)}
    out = []
    for sentence in sentences:
        key = (round(sentence["t0"], 2), round(sentence["t1"], 2))
        row = pending.get(key)
        if row and row["original"] == sentence_text(sentence):
            # 改写文字后，旧逐词时间不再可信；按原句时段重新分条，不伪造逐词对齐。
            out.append(dict(sentence, text=row["text"], words=[], subtitle_manual=True))
            pending.pop(key)
        else:
            out.append(sentence)
    return out, list(pending.values())
