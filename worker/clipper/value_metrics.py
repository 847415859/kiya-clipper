"""Age-aligned, auditable observations; never infer follower attribution from public stats."""
from __future__ import annotations

import statistics
import time
import json
import sqlite3
from pathlib import Path

from .value_strategy import finite

METRICS = ("view", "like", "coin", "favorite", "share", "reply", "danmaku")
WINDOWS = {"24h": (86400, 3600), "7d": (604800, 21600)}


def snapshot_store(path: Path, incoming: dict | None = None) -> dict:
    """Immutable, transactional observations shared by learning and the collection timer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=15) as db:
        db.execute("CREATE TABLE IF NOT EXISTS value_snapshot (bvid TEXT, window TEXT, data TEXT NOT NULL, PRIMARY KEY(bvid, window))")
        for bv, slots in (incoming or {}).items():
            for window, row in slots.items():
                if window in WINDOWS:
                    db.execute("INSERT OR IGNORE INTO value_snapshot VALUES (?, ?, ?)",
                               (bv, window, json.dumps(row, ensure_ascii=False, allow_nan=False)))
        result = {}
        for bv, window, raw in db.execute("SELECT bvid, window, data FROM value_snapshot"):
            result.setdefault(bv, {})[window] = json.loads(raw)
    return result


def upload_cache_store(path: Path, incoming: dict | None = None) -> dict:
    """Retain publication times discovered outside a due window, avoiding repeated API probes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=15) as db:
        db.execute("CREATE TABLE IF NOT EXISTS value_upload_cache (bvid TEXT PRIMARY KEY, data TEXT NOT NULL)")
        for bv, row in (incoming or {}).items():
            db.execute("INSERT OR REPLACE INTO value_upload_cache VALUES (?, ?)",
                       (bv, json.dumps(row, ensure_ascii=False, allow_nan=False)))
        return {bv: json.loads(raw) for bv, raw in db.execute("SELECT bvid, data FROM value_upload_cache")}


def collect_due(rows: list[dict], history: dict, fetch, now: float, budget: int = 30, clock=None) -> tuple[dict, list[str], int]:
    """Only query due or unknown publication ages; never query a mature missed window to backfill it."""
    errors, count, seen = [], 0, set()
    result = dict(history)
    for row in rows:
        bv, published = row.get("bvid"), row.get("pubdate")
        if not bv or bv in seen:
            continue
        seen.add(bv)
        slots = result.get(bv) or {}
        if finite(published) and not any(label not in slots and age <= now - published <= age + tolerance
                                         for label, (age, tolerance) in WINDOWS.items()):
            continue
        if count >= max(0, min(30, budget)):
            break
        count += 1
        try:
            stat = fetch(bv)
            if row.get("owner_mid") and str(row["owner_mid"]) != str(stat.get("owner_mid")):
                errors.append(f"{bv} 投稿归属不一致，未记录")
                continue
            collected = clock() if clock else now
            result = observe([{**row, **stat, "stats_stale": False, "stats_fetched_at": collected}], result, collected)
        except Exception as error:
            # Do not echo exception strings that may contain HTTP headers/cookies.
            errors.append(f"{bv} 快照采集失败：{type(error).__name__}")
    return result, errors, count


def valid_metric(v):
    return finite(v) and v >= 0


def attributed_followers(row: dict, target: str) -> float | None:
    # A public follow count, an account total, or an unproven manual field is insufficient.
    field = row.get("attribution", {}).get(target) if isinstance(row.get("attribution"), dict) else None
    if isinstance(field, dict) and isinstance(field.get(row.get("snapshot_label")), dict):
        field = field[row["snapshot_label"]]
    if not isinstance(field, dict) or not isinstance(field.get("source"), str) or not field["source"].strip() or field.get("verified") is not True:
        return None
    n, views = field.get("count"), row.get("view")
    if (field.get("window") != row.get("snapshot_label") or row.get("snapshot_label") not in WINDOWS
            or not valid_metric(field.get("view")) or field["view"] != views):
        return None
    if not valid_metric(n) or not valid_metric(views) or views <= 0:
        return None
    return 1000 * n / views


def observe(rows: list[dict], history: dict | None = None, now: float | None = None) -> dict:
    """Only fresh observations inside a fixed age tolerance count; never backfill old data."""
    now = time.time() if now is None else now
    result = dict(history or {})
    for row in rows:
        bv = row.get("bvid")
        published, collected = row.get("pubdate"), row.get("stats_fetched_at")
        if (not bv or row.get("stats_stale", True) or not finite(published) or not finite(collected)
                or not published <= collected <= now or now - collected > 300 or not valid_metric(row.get("view"))):
            continue
        age = collected - published
        slots = dict(result.get(bv) or {})
        for label, (target, tolerance) in WINDOWS.items():
            if label in slots or not target <= age <= target + tolerance:
                continue
            slots[label] = {**row, "age_seconds": age, "target_age": target,
                            "snapshot_label": label, "collected_at": collected,
                            "clip_follows_per_1000": attributed_followers({**row, "snapshot_label": label}, "clip_account"),
                            "streamer_follows_per_1000": attributed_followers({**row, "snapshot_label": label}, "streamer"),
                            "streamer_returns_per_1000": attributed_followers({**row, "snapshot_label": label}, "streamer_return")}
        if slots:
            result[bv] = slots
    return result


def attach_attribution(history: dict, links: dict) -> dict:
    """Manual exports may arrive later; keep public observations immutable and audit the source."""
    for bv, slots in history.items():
        fields = (links.get(bv) or {}).get("attribution") or {}
        for label, row in slots.items():
            link = links.get(bv) or {}
            row["event_id"] = link.get("event_id") or row.get("event_id")
            row["attribution"] = fields
            for target, name in (("clip_account", "clip_follows"), ("streamer", "streamer_follows"), ("streamer_return", "streamer_returns")):
                row[name + "_per_1000"] = attributed_followers({**row, "snapshot_label": label}, target)
    return history


def summarize(history: dict, min_per_type: int = 5) -> dict:
    out = {}
    for window in WINDOWS:
        seen, groups = set(), {}
        for bv, slots in sorted(history.items()):
            row = slots.get(window)
            if not row or not row.get("session") or not row.get("rank"):
                continue
            identity = row.get("event_id") or f"{row['session']}:{row['rank']}"
            if identity in seen:  # Reuploads and multiple BV bindings are one event, not independent samples.
                continue
            seen.add(identity)
            kind = row.get("main_value") or row.get("type") or "未知"
            groups.setdefault(kind, []).append(row)
        out[window] = {}
        for kind, rows in groups.items():
            views = [r["view"] for r in rows if valid_metric(r.get("view"))]
            out[window][kind] = {"n": len(rows), "sufficient": len(rows) >= min_per_type,
                                 "median_view": statistics.median(views) if views else None,
                                 "view_min": min(views) if views else None, "view_max": max(views) if views else None,
                                 "view_stdev": statistics.pstdev(views) if views else None,
                                 "causal_claim": False}
            for key in ("clip_follows_per_1000", "streamer_follows_per_1000", "streamer_returns_per_1000"):
                values = [r[key] for r in rows if valid_metric(r.get(key))]
                out[window][kind][key] = {"n": len(values), "median": statistics.median(values) if values else None}
    return out
