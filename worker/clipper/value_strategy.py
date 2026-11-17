"""Value-v1 policy: explicit unknowns, evidence checks and review-only comparison.

Times in evidence are relative to the original candidate, including negative context.
This module never posts videos or changes the legacy ranking.
"""
from __future__ import annotations

import math

from .memory import VALUE_WEIGHTS, value_score, value_final_score

VERSION = "value-v1"
SOURCES = {"原话", "弹幕", "画面"}


def finite(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def is_value(llm: dict) -> bool:
    return llm.get("value_schema_version") is not None


def evidence_errors(llm: dict, start: float | None = None, end: float | None = None) -> list[str]:
    """Every positive dimension needs a concrete, time-addressable supporting item."""
    errors = []
    if value_score(llm) is None:
        errors.append("五维分数未知、缺失或版本不支持")
    supported = set()
    for i, e in enumerate(llm.get("value_evidence") or []):
        if not isinstance(e, dict):
            errors.append(f"证据 {i} 不是对象")
            continue
        t = e.get("t")
        good = (e.get("dimension") in VALUE_WEIGHTS and e.get("src") in SOURCES
                and finite(t) and isinstance(e.get("quote"), str) and bool(e["quote"].strip())
                and isinstance(e.get("why"), str) and bool(e["why"].strip()))
        if good and start is not None and end is not None:
            good = start <= t <= end
        if not good:
            errors.append(f"证据 {i} 来源、时间、引用或理由不完整")
        else:
            supported.add(e["dimension"])
    for dim in VALUE_WEIGHTS:
        if finite(llm.get(dim)) and llm[dim] > 0 and dim not in supported:
            errors.append(f"{dim} 缺少证据")
    errors.extend(llm.get("evidence_errors") or [])
    return list(dict.fromkeys(errors))


def verify_evidence(llm: dict, candidate: dict, sents: list[dict], events, frames: list[float],
                    before: float = 60, after: float = 45, delay: float = 0) -> list[str]:
    """Verify text against inputs; visual interpretation remains a human review task."""
    a, b = candidate["clip_start"], candidate["clip_end"]
    errors = evidence_errors(llm, -min(a, before), b - a + after)
    for i, e in enumerate(llm.get("value_evidence") or []):
        if not isinstance(e, dict) or not finite(e.get("t")) or not isinstance(e.get("quote"), str):
            continue
        t, quote = a + e["t"], e["quote"].strip()
        if e.get("src") == "原话":
            near = "".join(s.get("text", "") for s in sents if s["t0"] - 2 <= t <= s["t1"] + 2)
            if not quote or quote not in near:
                errors.append(f"证据 {i} 无对应原话")
        elif e.get("src") == "弹幕":
            if not any(quote and quote in str(getattr(x, "text", ""))
                       and abs(getattr(x, "t", -999999) - delay - t) <= 5 for x in events):
                errors.append(f"证据 {i} 无对应弹幕")
        elif e.get("src") == "画面" and not any(abs(e["t"] - f) <= 1 for f in frames):
            errors.append(f"证据 {i} 无对应输入截图")
    return list(dict.fromkeys(errors))


def route(llm: dict | None, taboo: bool = False, threshold: float = .65,
          min_conf: float = .6) -> tuple[str, list[str]]:
    if not llm:
        return "quarantine", ["评审失败，待复核"]
    if taboo or llm.get("risk_level") == "high" or llm.get("in_character") is False:
        return "quarantine", ["禁区、风险或形象误读"]
    errors = evidence_errors(llm)
    if errors or llm.get("asr_doubt") or not finite(llm.get("confidence")) or llm["confidence"] < min_conf:
        return "quarantine", errors or ["原话或判断把握不足"]
    reasons = []
    if llm.get("needs_context") or not llm.get("standalone"):
        reasons.append("need_context")
    if not llm.get("complete", True):
        reasons.append("incomplete")
    if llm.get("hook_ok") is False:
        reasons.append("weak_hook")
    if reasons:
        return "repair", reasons
    if value_score(llm) < threshold:
        return "low_value", ["五维价值未过线"]
    return "normal", []


def annotate_shadow(recs: list[dict], n_total: int, threshold: float = .65,
                    screen_weight: float = .2, min_conf: float = .6) -> None:
    for r in recs:
        llm = r.get("value_llm")
        r["shadow_value_score"] = value_final_score(llm, r["rank"], n_total,
                                                    screen_weight=screen_weight, taboo=bool(r.get("taboo")))
        group, reasons = route(llm, bool(r.get("taboo")), threshold, min_conf)
        r.update(shadow_route=group, shadow_reasons=reasons,
                 shadow_pick=group == "normal", strategy_version=VERSION)
        r["shadow_rank"] = None
    known = sorted((r for r in recs if r.get("shadow_value_score") is not None),
                   key=lambda r: (-r["shadow_value_score"], r["rank"]))
    for i, r in enumerate(known, 1):
        r["shadow_rank"] = i
    selected = []
    for r in known:
        if not r["shadow_pick"]:
            continue
        a, b = r.get("clip_start", 0), r.get("clip_end", 0)
        duplicate = next((x for x in selected if min(b, x.get("clip_end", 0)) > max(a, x.get("clip_start", 0))
                          and min(b, x.get("clip_end", 0)) - max(a, x.get("clip_start", 0)) > .5 * min(b - a, x["clip_end"] - x["clip_start"])), None)
        r["shadow_comparison"] = {"method": "value_evidence_then_score", "dup_of": duplicate["rank"] if duplicate else None}
        if duplicate or len(selected) >= 3:
            r["shadow_pick"] = False
            r["shadow_reasons"] = ["同一事件重复" if duplicate else "超过本场探索推荐上限（3 条）"]
        else:
            selected.append(r)


def constrain_cute(edit: dict) -> dict:
    """Apply after all decoration/callback steps, so later edits cannot evade limits."""
    items = []
    for item in edit.get("items", []):
        if item.get("type") not in {"text", "zoom"}:
            continue
        item = dict(item)
        if item["type"] == "zoom":
            item["scale"] = min(1.15, max(1.05, item.get("scale", 1.05)))
        items.append(item)
    edit["items"] = items[:3]
    edit.pop("music", None)
    edit.pop("callback", None)
    edit["template"] = "cute-v1"
    edit["original_audio"] = True
    edit["remove_business"] = False
    return edit
