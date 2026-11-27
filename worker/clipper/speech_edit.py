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
        entry = {"t0": float(a), "t1": float(b), "original": original, "text": text.strip()}
        if "display_t0" in row or "display_t1" in row:
            da, db = row.get("display_t0"), row.get("display_t1")
            if any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) for t in (da, db)) or not 0 <= da < db <= 86400:
                raise ValueError("字幕显示时间须为有效整场区间")
            entry.update(display_t0=float(da), display_t1=float(db))
        for axis in ("display_x", "display_y"):
            if axis in row:
                value = row[axis]
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value) or not 0.05 <= value <= 0.95):
                    raise ValueError("字幕位置须为 0.05–0.95 的归一化坐标")
                entry[axis] = float(value)
        if "parts" in row:
            parts = row["parts"]
            if not isinstance(parts, list) or not 2 <= len(parts) <= 100:
                raise ValueError("字幕拆分须有 2–100 个子句")
            lower, upper = entry.get("display_t0", a), entry.get("display_t1", b)
            previous, validated = lower, []
            for part in parts:
                if not isinstance(part, dict):
                    raise ValueError("字幕子句格式不对")
                pa, pb, text = part.get("t0"), part.get("t1"), part.get("text")
                if (any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t)
                        for t in (pa, pb)) or pa < previous or pb > upper
                        or pb - pa < 0.3 - 1e-9):
                    raise ValueError("字幕子句须在显示区间内顺序排列、互不重叠且至少 0.3 秒")
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("字幕子句文字须非空")
                validated.append({"text": text, "t0": float(pa), "t1": float(pb)})
                previous = pb
            if "".join(p["text"] for p in validated) != entry["text"]:
                raise ValueError("字幕子句拼接须与当前字幕文字完全一致")
            entry["parts"] = validated
        out.append(entry)
    return out


def apply_overrides(sentences: list[dict], rows: list[dict]) -> tuple[list[dict], list[dict]]:
    pending = {(round(r["t0"], 2), round(r["t1"], 2)): r for r in validate_overrides(rows)}
    out = []
    for sentence in sentences:
        key = (round(sentence["t0"], 2), round(sentence["t1"], 2))
        row = pending.get(key)
        if (row and abs(row["t0"] - sentence["t0"]) < 1e-6
                and abs(row["t1"] - sentence["t1"]) < 1e-6
                and row["original"] == sentence_text(sentence)):
            # 改写文字后，旧逐词时间不再可信；按原句时段重新分条，不伪造逐词对齐。
            revised = dict(sentence, text=row["text"], words=[], subtitle_manual=True,
                           t0=row.get("display_t0", sentence["t0"]), t1=row.get("display_t1", sentence["t1"]))
            for field in ("display_x", "display_y", "parts"):
                if field in row:
                    revised[field] = row[field]
            out.append(revised)
            pending.pop(key)
        else:
            out.append(sentence)
    return out, list(pending.values())
