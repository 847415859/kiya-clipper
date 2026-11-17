#!/usr/bin/env python3
"""非峰值召回与人工时间标记；不修改正式候选，不调用付费模型。

python scripts/recall.py output/kiya/场次/candidates.json --budget 5
python scripts/recall.py output/kiya/场次/candidates.json --mark 120 138 --note '自然反应'
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clipper.asr import load_transcript
from clipper.recall import explore
from clipper.llm import LLMClient, fill, load_prompt
from pydantic import BaseModel, Field


class SemanticOut(BaseModel):
    candidates: list[dict] = Field(default_factory=list)


def semantic_recall(sentences: list[dict], client, budget: int, max_calls: int = 3) -> tuple[list[dict], list[str]]:
    """Bounded text-only recall, sampling across the session, with verbatim verification downstream."""
    if not sentences or budget <= 0:
        return [], []
    chunks, chunk, length = [], [], 0
    for s in sentences:
        line = f"[{s['t0']:.1f}–{s['t1']:.1f}] {s.get('text', '')[:1000]}"
        if length + len(line) > 6000 and chunk:
            chunks.append("\n".join(chunk))
            chunk, length = [], 0
        chunk.append(line)
        length += len(line)
    if chunk:
        chunks.append("\n".join(chunk))
    n = min(max(0, min(3, max_calls)), len(chunks))
    indices = sorted({round(i * (len(chunks)-1) / max(1,n-1)) for i in range(n)})
    version, system, user = load_prompt("recall_semantic_v1")
    proposals, errors = [], []
    for i in indices:
        response = client.chat_json([{"role":"system","content":system},
                                     {"role":"user","content":fill(user, speech=chunks[i], budget=str(budget))}], SemanticOut, tag=version)
        if response.data:
            proposals.extend(response.data.candidates[:budget])
        else:
            errors.append(response.error or "语义召回失败")
    return proposals, errors


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("candidates")
    ap.add_argument("--budget", type=int, default=5)
    ap.add_argument("--mark", type=float, nargs=2, action="append", default=[])
    ap.add_argument("--note", default="")
    ap.add_argument("--semantic", action="store_true", help="额外语义召回：最多 3 次文本模型调用，每次最多 6000 字")
    args = ap.parse_args()
    path = Path(args.candidates)
    data = json.loads(path.read_text(encoding="utf-8"))
    tr = load_transcript(path.parent / "transcript.json") or {}
    marks_path = path.parent / "manual_marks.json"
    marks = json.loads(marks_path.read_text(encoding="utf-8")) if marks_path.exists() else []
    added = [{"start": a, "end": b, "note": args.note} for a, b in args.mark]
    combined = {(m.get("start"), m.get("end")): m for m in marks + added}
    marks = list(combined.values())
    if len(marks) > 50:
        ap.error("每场最多保存 50 个人工时间标记")
    proposals, errors, client = [], [], None
    if args.semantic and args.budget > 0:
        client = LLMClient(cache_dir=path.parent.parent / "_llm_cache")
        proposals, errors = semantic_recall(tr.get("sentences") or [], client, min(10, args.budget))
    result = explore(data, tr.get("sentences") or [], args.budget, marks, proposals)
    if client:
        result.update(llm_calls=client.total["calls"], llm_usage=client.total, recall_errors=errors,
                      semantic_method="grounded_model", semantic_prompt_version="recall-semantic-v1",
                      semantic_coverage="最多均匀抽取 3 段逐字稿，每段 6000 字，不代表全场语义穷举")
    if any(x["source"] == "manual" and x["reason"] == "时间越界或无效" for x in result["recall_skipped"]):
        ap.error("人工标记时间越界，未保存")
    if added:
        marks_path.write_text(json.dumps(marks, ensure_ascii=False, indent=2), encoding="utf-8")
    dst = path.parent / "exploration_candidates.json"
    dst.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"待复核召回 {len(result['candidates'])} 条 → {dst}；不改变正式推荐")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
