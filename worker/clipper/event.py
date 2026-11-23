"""事件结构、候选合并和连续片段多边界方案。

输入输出均使用整场时间（秒）。该模块不调用模型，保证旧精排结果可以安全升级。
"""
from __future__ import annotations

import hashlib
import re
from typing import Iterable


BOUNDARY_KEYS = ("concise", "context", "afterglow")
BOUNDARY_LABELS = {
    "concise": "精简版",
    "context": "完整语境版",
    "afterglow": "保留余韵版",
}


def _num(value, default=None):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if value == value and abs(value) != float("inf") else default


def _text(value, limit=240):
    return str(value or "").strip()[:limit]


def _words(value):
    text = _text(value, 120).lower()
    parts = []
    for token in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text):
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            parts.extend(token[i:i + 2] for i in range(max(1, len(token) - 1)))
        else:
            parts.append(token)
    return set(parts)


def stable_event_id(candidate: dict, event: dict | None = None) -> str:
    """根据候选来源和核心描述生成稳定事件编号，不依赖排序名次。"""
    event = event or {}
    source = "|".join(str(x) for x in sorted(candidate.get("source_ids") or []))
    start = _num(candidate.get("clip_start"), 0.0)
    end = _num(candidate.get("clip_end"), 0.0)
    core = _text(event.get("core") or event.get("what_happened") or candidate.get("what_happened"))
    raw = f"{source}|{start:.2f}|{end:.2f}|{core.lower()}"
    return "evt_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _core(candidate: dict) -> str:
    event = candidate.get("event") or {}
    llm = candidate.get("llm") or {}
    return _text(event.get("core") or event.get("what_happened") or llm.get("what_happened") or candidate.get("what_happened"))


def _similar_core(left: dict, right: dict) -> bool:
    a, b = _words(_core(left)), _words(_core(right))
    if not a or not b:
        return False
    return len(a & b) >= 1 and len(a & b) / min(len(a), len(b)) >= 0.65


def merge_candidates(candidates: Iterable[dict], proximity: float = 12.0) -> list[dict]:
    """合并重复候选，保留所有来源；相邻但核心不同的事件不会合并。"""
    groups: list[dict] = []
    for original in sorted(candidates, key=lambda x: (_num(x.get("clip_start"), 0), _num(x.get("clip_end"), 0))):
        item = dict(original)
        start, end = _num(item.get("clip_start")), _num(item.get("clip_end"))
        if start is None or end is None or end <= start:
            continue
        match = None
        for group in groups:
            gap = max(0.0, start - group["end"], group["start"] - end)
            if gap <= proximity and _similar_core(group, item):
                match = group
                break
        source = item.get("source_ids") or [item.get("source") or item.get("rank")]
        if match is None:
            item["start"], item["end"] = start, end
            item["source_ids"] = list(dict.fromkeys(str(x) for x in source if x is not None))
            item["event_id"] = stable_event_id(item)
            groups.append(item)
        else:
            match["start"] = min(match["start"], start)
            match["end"] = max(match["end"], end)
            match["source_ids"] = list(dict.fromkeys(match["source_ids"] + [str(x) for x in source if x is not None]))
            match["event_id"] = stable_event_id(match)
    return groups


def normalize_event(candidate: dict, event: dict | None = None) -> dict:
    """把精排旧字段归一化为事件结构，缺失信息明确标记为待复核。"""
    event = event or candidate.get("event") or {}
    llm = candidate.get("llm") or {}
    core = _text(event.get("core") or event.get("what_happened") or llm.get("what_happened"))
    evidence = event.get("evidence") or llm.get("evidence") or []
    issues = list(event.get("understanding_issues") or [])
    if not core:
        issues.append("缺少事件概述")
    if not evidence:
        issues.append("缺少可核对证据")
    out = {
        "event_id": event.get("event_id") or stable_event_id(candidate, {"core": core}),
        "version": int(event.get("version") or 1),
        "summary": _text(event.get("summary") or core, 120),
        "objects": _text(event.get("objects"), 120),
        "context": _text(event.get("context"), 240),
        "core": core,
        "reaction": _text(event.get("reaction"), 240),
        "end_basis": _text(event.get("end_basis"), 240),
        "evidence": evidence[:8],
        "understanding_issues": list(dict.fromkeys(issues))[:8],
        "source_ids": list(dict.fromkeys(str(x) for x in (event.get("source_ids") or [candidate.get("rank")]) if x is not None)),
        "status": "needs_review" if issues else "ready",
    }
    return out


def validate_boundary(option: dict, event: dict, material_start: float = 0.0, material_end: float | None = None) -> list[str]:
    errors = []
    start, end = _num(option.get("start")), _num(option.get("end"))
    if start is None or end is None or end <= start:
        errors.append("起止无效")
        return errors
    if start < material_start or material_end is not None and end > material_end:
        errors.append("超出可用素材")
    core_start, core_end = _num(event.get("core_start")), _num(event.get("core_end"))
    if core_start is not None and core_end is not None and not (start <= core_start and end >= core_end):
        errors.append("未覆盖核心表达")
    return errors


def generate_boundary_options(candidate: dict, event: dict | None = None, refined: dict | None = None,
 900material_end: float | None = None) -> list[dict]:
    """生成有真实取舍的连续边界方案；无足够信息时返回旧 refined 的单方案。"""
    event = normalize_event(candidate, event)
    refined = refined or candidate.get("refined") or {}
    base_start = _num(refined.get("start"), _num(candidate.get("clip_start"), 0.0))
    base_end = _num(refined.get("end"), _num(candidate.get("clip_end"), base_start + 1))
    peak = _num(candidate.get("t"), (base_start + base_end) / 2)
    core_start = _num(event.get("core_start"), peak)
    core_end = _num(event.get("core_end"), peak)
    event["core_start"], event["core_end"] = core_start, core_end
    context_start = _num(event.get("context_start"), _num(candidate.get("clip_start"), base_start))
    reaction_end = _num(event.get("reaction_end"), base_end)
    raw = {
        "concise": (max(base_start, core_start - 2), min(base_end, core_end + 3)),
        "context": (min(base_start, context_start), max(base_end, core_end + 3)),
        "afterglow": (min(base_start, core_start - 2), max(base_end, reaction_end)),
    }
    options = []
    seen = set()
    for key in BOUNDARY_KEYS:
        start, end = raw[key]
        option = {"key": key, "label": BOUNDARY_LABELS[key], "start": round(start, 2), "end": round(end, 2),
                  "duration": round(end - start, 2), "reason": "", "issues": [], "event_version": event["version"]}
        option["issues"] = validate_boundary(option, event, material_end=material_end)
        option["reason"] = {"concise": "省略非必要前情，尽快进入核心表达", "context": "保留必要前情，降低新观众理解门槛", "afterglow": "保留核心后的反应、停顿或找补"}[key]
        signature = (option["start"], option["end"])
        if signature not in seen and not option["issues"]:
            options.append(option)
            seen.add(signature)
    return options
