"""Source-time cuts shared by editor previews, renders and quality checks."""
from __future__ import annotations

from .boiler import business_cuts, compact_time, keep_ranges


def merge_cuts(cuts, start, end):
    merged = []
    for q in sorted(cuts, key=lambda q: float(q["start"])):
        a, b = max(start, float(q["start"])), min(end, float(q["end"]))
        if b <= a:
            continue
        if merged and a <= merged[-1]["end"]:
            merged[-1]["end"] = max(b, merged[-1]["end"])
        else:
            merged.append({"start": a, "end": b})
    return merged


def edit_cuts(edit, sentences, window_start, start, end):
    cuts = [{"start": window_start + q["start"], "end": window_start + q["end"]}
            for q in edit.get("cuts", [])]
    if edit.get("remove_business", True):
        from .protection import required, protect_automatic
        automatic, _ = protect_automatic(business_cuts(sentences, start, end), required(edit))
        cuts += automatic
    return merge_cuts(cuts, start, end)


def compact_events(events, origin, removed):
    """Keep surviving words; manual captions have no word timestamps and must survive too."""
    out = []
    def mapped(t):
        return round(compact_time(t, removed) - compact_time(origin, removed), 3)
    for event in events:
        a, b = origin + event["t0"], origin + event["t1"]
        spans = keep_ranges(a, b, removed)
        if not spans:
            continue
        e = dict(event, t0=mapped(spans[0][0]), t1=mapped(spans[-1][1]))
        words = []
        for wa, wb, text in event.get("words", []):
            # A word straddling a splice cannot be reliably re-timed; omit that word.
            if any(origin + wa < q["end"] and origin + wb > q["start"] for q in removed):
                continue
            words.append([mapped(origin + wa), mapped(origin + wb), text])
        e["words"] = words
        overlaps = any(a < q["end"] and b > q["start"] for q in removed)
        if overlaps:
            e["cut_adjusted"] = True
            if event.get("words") and not event.get("manual"):
                if not words:
                    continue
                e["text"] = "".join(w[2] for w in words)
        if e["t1"] > e["t0"]:
            out.append(e)
    return out
