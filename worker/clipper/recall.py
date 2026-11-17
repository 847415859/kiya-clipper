"""Bounded, reproducible non-peak recall. Writes a separate review-only queue."""
from __future__ import annotations

import hashlib
import json
import random
import re

from .value_strategy import finite

SEMANTIC = re.compile(r"没想到|原来|其实|但是|结果|我觉得|我想|谢谢|喜欢|记得|第一次|为什么|对不起|不敢|答应")


def explore(data: dict, sentences: list[dict], budget: int = 5, marks: list[dict] | None = None,
            proposals: list[dict] | None = None) -> dict:
    """Half transcript-driven, half uniform temporal exploration; manual marks are explicit."""
    budget = min(10, max(0, int(budget)))
    duration = data.get("duration_seconds") or data.get("covered_seconds") or max(
        [s.get("t1", 0) for s in sentences] + [c.get("clip_end", 0) for c in data.get("candidates", [])] + [0])
    if not finite(duration) or duration <= 0:
        raise ValueError("无法确定素材范围，先转写或填写 duration_seconds")
    rng = random.Random(hashlib.sha256(str(data.get("session", "")).encode()).hexdigest())
    occupied = [(c["clip_start"], c["clip_end"]) for c in data.get("candidates", [])]
    selected = []
    skipped = []

    def add(a, b, source, note="", manual=False):
        if not finite(a) or not finite(b) or not 0 <= a < b <= duration:
            skipped.append({"source": source, "reason": "时间越界或无效"})
            return
        overlaps = any(max(0, min(b, y) - max(a, x)) / min(b - a, y - x) > .5 for x, y in occupied if y > x)
        if overlaps:
            skipped.append({"source": source, "start": a, "end": b, "reason": "与已召回事件重叠"})
            return
        if not manual and sum(c["recall_source"] != "manual" for c in selected) >= budget:
            return
        occupied.append((a, b))
        t = (a + b) / 2
        segs = data.get("segments") or []
        fi = max((i for i, s in enumerate(segs) if s.get("offset", 0) <= a), default=0)
        selected.append({"rank": len(selected) + 1, "t": t, "clip_start": a, "clip_end": b, "score": 0,
                         "file_index": fi, "local_start": a - (segs[fi].get("offset", 0) if segs else 0),
                         "speech": [s for s in sentences if s["t0"] < b and s["t1"] > a],
                         "recall_source": source, "recall_note": note, "review_only": True,
                         "candidate_id": f"{data.get('session')}:{a:.2f}:{b:.2f}"})

    for mark in (marks or [])[:50]:
        add(mark.get("start"), mark.get("end"), "manual", str(mark.get("note") or ""), manual=True)
    for proposal in proposals or []:
        a, b, quote = proposal.get("start"), proposal.get("end"), proposal.get("quote")
        if not finite(a) or not finite(b) or not isinstance(quote, str) or not quote.strip():
            skipped.append({"source": "semantic_model", "reason": "语义候选证据缺失"})
            continue
        speech = "".join(s.get("text", "") for s in sentences if s["t0"] < b and s["t1"] > a)
        if quote not in speech:
            skipped.append({"source": "semantic_model", "reason": "语义候选引用无法核对"})
            continue
        add(a, b, "semantic_model", quote + "；" + str(proposal.get("reason") or ""))
    semantic = [s for s in sentences if SEMANTIC.search(s.get("text", ""))]
    rng.shuffle(semantic)
    for s in semantic:
        if sum(c["recall_source"] == "transcript_semantic" for c in selected) >= (budget + 1) // 2:
            break
        add(max(0, s["t0"] - 8), min(duration, s["t1"] + 12), "transcript_semantic", s.get("text", ""))
    # Fixed attempt count is an explicit cost bound, including crowded sessions.
    for _ in range(budget * 30):
        if sum(c["recall_source"] != "manual" for c in selected) >= budget:
            break
        a = rng.uniform(0, max(0, duration - 30))
        add(a, min(duration, a + 30), "non_peak_sample")
    recall_id = hashlib.sha256(json.dumps(selected, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    return {**{k: v for k, v in data.items() if k != "candidates"}, "candidates": selected, "recall_id": recall_id,
            "review_only": True, "recall_version": "recall-v1", "recall_budget": budget,
            "recall_skipped": skipped, "semantic_method": "grounded_model" if proposals else "local_phrase_heuristic", "llm_calls": 0}
