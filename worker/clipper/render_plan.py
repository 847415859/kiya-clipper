"""Deterministic preview/render data. No encoding and no model calls."""
from .boiler import compact_time, keep_ranges
from .timeline import edit_cuts, compact_events
from .subtitles import build_events, apply_corrections, STRIP_PUNCT


def plan(edit, sentences, window_start, style, corrections, keywords):
    start = window_start + edit["trim"]["start"]
    end = window_start + edit["trim"]["end"]
    removed = edit_cuts(edit, sentences, window_start, start, end)
    duration = sum(b - a for a, b in keep_ranges(start, end, removed))
    if duration < 1:
        raise ValueError("删除后不足 1 秒，请减少片中删除或关闭自动删除营业话")
    def mapped(t):
        return round(compact_time(t, removed) - compact_time(start, removed), 3)
    events = []
    if edit["subtitles"]["enabled"] and sentences:
        events = build_events(sentences, start, end, style, corrections,
                              edit["subtitles"].get("sentence_overrides"))
        for e in events:
            words = []
            for a, b, word in e["words"]:
                word = apply_corrections(word, None if e.get("manual") else corrections)
                if style["strip_punct"]:
                    word = word.rstrip(STRIP_PUNCT)
                if word:
                    words.append([round(a, 3), round(b, 3), word])
            e["words"] = [] if e.get("manual") else words
            e["t0"], e["t1"] = round(e["t0"], 3), round(e["t1"], 3)
        events = compact_events(events, start, removed) if removed else events
    items = []
    for it in edit["items"]:
        a = max(start, window_start + it["start"])
        b = min(end, window_start + it["start"] + it["duration"])
        if b > a and mapped(b) > mapped(a):
            items.append(dict(it, start=mapped(a), duration=round(mapped(b) - mapped(a), 3)))
    from .protection import coverage, required, protect_automatic
    from .boiler import business_cuts
    rows = required(edit)
    _, conflicts = protect_automatic(business_cuts(sentences, start, end), rows) if edit.get("remove_business", True) else ([], [])
    protection = coverage(rows, keep_ranges(start, end, removed))
    protection["protected_automatic_cuts"] = conflicts
    return {"duration": round(duration, 3), "removed": removed, "items": items, "evidence_coverage": protection,
            "subtitles": {"enabled": bool(edit["subtitles"]["enabled"] and sentences),
                          "style": style, "events": events, "keywords": keywords}}
