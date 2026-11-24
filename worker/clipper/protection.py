"""Source-time evidence constraints shared by cuts, drafts and render receipts."""
from __future__ import annotations

import hashlib
import json
import math


def revision(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def validate_required(rows):
    if not isinstance(rows, list) or len(rows) > 200:
        raise ValueError("必保证据须为列表，最多200项")
    out = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("必保证据格式不对")
        a, b = row.get("start"), row.get("end")
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in (a, b)) or not 0 <= a < b:
            raise ValueError("必保证据须使用有效整场起止秒数")
        if not isinstance(row.get("id"), str) or not row["id"].strip():
            raise ValueError("必保证据缺少ID")
        if "continuous" in row and not isinstance(row["continuous"], bool):
            raise ValueError("必保证据continuous须为布尔值")
        out.append(dict(row, start=float(a), end=float(b), continuous=row.get("continuous", True)))
    return out


def required(edit):
    rows = list(edit.get("required_evidence") or [])
    reaction = edit.get("required_reaction")
    if reaction:
        rows.append(dict(reaction, id="legacy-required-reaction", modality="audio", role="effective_response", basis="manual", continuous=True))
    return validate_required(rows)


def resolve(reranked, candidate, session):
    report = (reranked or {}).get("video_evidence") or {}
    rev = revision(report)
    if report and any(key in (reranked or {}) and abs(float(reranked[key])-float(candidate[key])) > 1e-6 for key in ('clip_start', 'clip_end') if key in candidate):
        return [], {"evidence_revision": rev, "issues": [{"status": "stale_candidate_source"}], "gaps": report.get("gaps", [])}
    observations = {r["id"]: r for r in report.get("observations", [])}
    rows = []
    issues = [{"id": key, "status": "unknown_id"} for key in report.get("unknown_ids", [])]
    for key in report.get("must_keep_ids", []):
        obs = observations.get(key)
        if not obs:
            issues.append({"id": key, "status": "unknown_id"})
            continue
        rows.append({"id": key, "start": obs["start"], "end": obs["end"], "modality": obs.get("modality", "unknown"),
                     "role": obs.get("kind", "unknown"), "reason": obs.get("description", ""), "basis": "model_observation",
                     "continuous": True, "candidate": {"session": session, "rank": candidate["rank"], "clip_start": candidate["clip_start"]},
                     "evidence_revision": rev})
    return validate_required(rows), {"evidence_revision": rev, "issues": issues, "gaps": report.get("gaps", [])}


def protect_automatic(cuts, rows):
    kept, conflicts = [], []
    for cut in cuts:
        hits = [r["id"] for r in rows if cut["start"] < r["end"] and cut["end"] > r["start"]]
        if hits:
            conflicts.append({**cut, "evidence_ids": hits, "reason": "automatic_removal_conflicts_with_required_evidence"})
        else:
            kept.append(cut)
    return kept, conflicts


def coverage(rows, spans, origin=0):
    mappings, offset = [], 0.0
    for a, b in spans:
        mappings.append({"source_start": a, "source_end": b, "output_start": offset, "output_end": offset+b-a})
        offset += b-a
    checks = []
    for row in rows:
        intersections = []
        for m in mappings:
            a, b = max(row["start"], m["source_start"]), min(row["end"], m["source_end"])
            if b > a:
                intersections.append({"source_start": a, "source_end": b, "output_start": m["output_start"]+a-m["source_start"], "output_end": m["output_start"]+b-m["source_start"]})
        cursor, gaps = row["start"], []
        for p in sorted(intersections, key=lambda p: p["source_start"]):
            if p["source_start"] > cursor + 1e-6:
                gaps.append([cursor, p["source_start"]])
            cursor = max(cursor, p["source_end"])
        if cursor < row["end"]-1e-6:
            gaps.append([cursor, row["end"]])
        discontinuous = row.get("continuous", True) and any(abs(right["source_start"]-left["source_end"]) > 1e-6 or abs(right["output_start"]-left["output_end"]) > 1e-6 for left,right in zip(intersections, intersections[1:]))
        checks.append({"evidence": row, "status": "missing" if gaps or discontinuous else "time_covered", "discontinuous": discontinuous, "missing_source_ranges": gaps, "output_ranges": intersections,
                       "semantic_confirmation": "not_evaluated", "action": "回看对应原片和成片区间，核对声尾、画面与阅读时间"})
    return {"status": "not_evaluable" if not rows else "missing" if any(c["status"] == "missing" for c in checks) else "time_covered",
            "checks": checks, "source_to_output": mappings, "output_duration": offset}
