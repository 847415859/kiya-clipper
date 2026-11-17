"""Blind review and conservative offline calibration; proposals never change live config."""
from __future__ import annotations

import hashlib
import itertools
import random

from .memory import VALUE_WEIGHTS, value_score
from .value_strategy import evidence_errors


def review_batch(recs: list[dict], session: str) -> tuple[list[dict], dict]:
    rows, key = [], {}
    for rec in recs:
        token = hashlib.sha256(f"value-v1:{session}:{rec['rank']}".encode()).hexdigest()[:12]
        rows.append({"review_id": token, "session": session, "start": rec.get("clip_start"),
                     "end": rec.get("clip_end"), "rubric_version": "value-v1", "mode": "blind",
                     "reviewer": "", "valuable": "", "newcomer": "", "reason": ""})
        key[token] = {"rank": rec["rank"], "legacy_rank": rec.get("new_rank"),
                      "shadow_rank": rec.get("shadow_rank"), "value_llm": rec.get("value_llm")}
    random.Random(session).shuffle(rows)
    return rows, key


def calibrate(reviews: list[dict], keys: dict) -> dict:
    """Leave the last session out; optimize pairwise agreement, report the held-out result."""
    samples, seen = [], set()
    for row in reviews:
        identity = row.get("review_id")
        llm = (keys.get(identity) or {}).get("value_llm")
        if (identity in seen or row.get("rubric_version") != "value-v1" or row.get("mode") != "blind"
                or not row.get("reviewer") or row.get("valuable") not in ("Y", "N")
                or not llm or evidence_errors(llm)):
            continue
        seen.add(identity)
        samples.append((row["session"], row["valuable"] == "Y", llm))
    sessions = sorted({s for s, _, _ in samples})
    if len(sessions) < 3 or len(samples) < 10:
        return {"status": "insufficient", "n": len(samples), "sessions": len(sessions), "applied": False}
    held_out = sessions[-1]
    train = [x for x in samples if x[0] != held_out]
    test = [x for x in samples if x[0] == held_out]

    def agreement(rows, weights):
        pairs = [(a, b) for a, b in itertools.combinations(rows, 2) if a[0] == b[0] and a[1] != b[1]]
        if not pairs:
            return None, 0
        score = 0
        for a, b in pairs:
            yes, no = (a, b) if a[1] else (b, a)
            gap = value_score(yes[2], weights) - value_score(no[2], weights)
            score += 1 if gap > 0 else (.5 if gap == 0 else 0)
        return score / len(pairs), len(pairs)

    base = dict(VALUE_WEIGHTS)
    candidates = [base]
    for src, dst in itertools.permutations(base, 2):
        for delta in (.05, .1):
            if base[src] < delta:
                continue
            w = dict(base)
            w[src] -= delta
            w[dst] += delta
            candidates.append(w)
    old_train, pairs = agreement(train, base)
    if old_train is None:
        return {"status": "insufficient_pairs", "applied": False}
    proposed = max(candidates, key=lambda w: agreement(train, w)[0])
    old_test, test_pairs = agreement(test, base)
    new_test, _ = agreement(test, proposed)
    improved = old_test is not None and new_test > old_test and agreement(train, proposed)[0] > old_train
    return {"status": "proposal" if improved else "no_validated_gain", "n": len(samples),
            "training_pairs": pairs, "held_out_session": held_out, "held_out_pairs": test_pairs,
            "baseline_agreement": old_test, "proposed_agreement": new_test,
            "weights": proposed if improved else base, "applied": False,
            "note": "仅验证盲审价值判断一致性，不证明涨粉；待人工确认和更多跨场样本"}


def publication_quota(rows: list[dict]) -> dict:
    """Rolling selection goal, never a value-score multiplier."""
    recent = sorted(rows, key=lambda r: r.get("pubdate") or 0, reverse=True)[:10]
    yes = sum(r.get("newcomer") is True for r in recent)
    unknown = sum(type(r.get("newcomer")) is not bool for r in recent)
    return {"n": len(recent), "newcomer": yes, "unknown": unknown, "target_per_10": 6,
            "remaining_to_target": max(0, 6 - yes), "auto_publish": False}
