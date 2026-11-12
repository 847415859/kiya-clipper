"""AI 初稿 vs 人工终稿：剪辑师改了什么，就是剪辑口味。

autoedit.py 生成初稿时另存一份原稿 edits/<序号>.ai.json；后台保存时在 edits/<序号>.json 里记 human_saved_at。
两份都在才算一条“人工修改”。这里把每条的差别算出来（删了哪些效果、改了哪些花字、裁剪范围挪了多少），
汇总成 edit_taste；攒够条数（且来自至少 2 场）才会写进初稿提示词（prompts/edit_v2.md 的 {{taste}}），
不会被一两条数据带偏。
"""
from __future__ import annotations

import json
from pathlib import Path

MATCH_WITHIN = 2.0          # 同类型的效果，开始时间差在这个范围内算“同一个效果”
RETIME = 0.5                # 开始时间挪动超过这个算“被调过时间”
TYPE_NAME = {"text": "花字", "zoom": "推镜", "sticker": "贴图", "narration": "吐槽旁白", "fakeui": "伪UI"}


def _read(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except (OSError, ValueError):
        return None


def _abs(edit: dict) -> tuple[float, list[dict], dict]:
    """(窗口起点, 效果列表（start 换成整场秒数）, 裁剪（整场秒数）)。"""
    ws = float((edit.get("window") or {}).get("start", 0))
    items = [dict(i, start=ws + float(i["start"])) for i in edit.get("items") or []]
    tr = edit.get("trim") or {}
    return ws, items, {"start": ws + float(tr.get("start", 0)), "end": ws + float(tr.get("end", 0))}


def diff_edit(ai: dict, human: dict) -> dict:
    _, a_items, a_trim = _abs(ai)
    _, h_items, h_trim = _abs(human)
    free = list(range(len(h_items)))
    kept, removed, retimed, text_changed, asset_changed = 0, [], 0, [], []
    for a in sorted(a_items, key=lambda i: i["start"]):
        best = min((j for j in free if h_items[j]["type"] == a["type"] and abs(h_items[j]["start"] - a["start"]) <= MATCH_WITHIN),
                   key=lambda j: abs(h_items[j]["start"] - a["start"]), default=None)
        if best is None:
            removed.append({"type": a["type"], "text": a.get("text") or a.get("asset") or "", "start": round(a["start"], 1)})
            continue
        free.remove(best)
        h = h_items[best]
        kept += 1
        if abs(h["start"] - a["start"]) > RETIME:
            retimed += 1
        if a["type"] == "text" and h.get("text") != a.get("text"):
            text_changed.append({"from": a.get("text", ""), "to": h.get("text", "")})
        if a["type"] == "sticker" and h.get("asset") != a.get("asset"):
            asset_changed.append({"from": a.get("asset", ""), "to": h.get("asset", "")})
    added = [{"type": h_items[j]["type"], "text": h_items[j].get("text") or h_items[j].get("asset") or "",
              "start": round(h_items[j]["start"], 1)} for j in free]
    count = lambda items: {t: sum(1 for i in items if i["type"] == t) for t in TYPE_NAME}
    return {"kept": kept, "removed": removed, "added": added, "retimed": retimed,
            "text_changed": text_changed, "asset_changed": asset_changed,
            "trim_shift": {"start": round(h_trim["start"] - a_trim["start"], 1), "end": round(h_trim["end"] - a_trim["end"], 1)},
            "ai_counts": count(a_items), "human_counts": count(h_items),
            "changed": bool(removed or added or retimed or text_changed or asset_changed
                            or abs(h_trim["start"] - a_trim["start"]) > 0.5 or abs(h_trim["end"] - a_trim["end"]) > 0.5)}


def collect_diffs(out_dir: Path) -> list[dict]:
    rows = []
    for d in sorted(p for p in out_dir.iterdir() if p.is_dir() and not p.name.startswith("_")) if out_dir.exists() else []:
        for f in sorted((d / "edits").glob("*.ai.json")) if (d / "edits").exists() else []:
            rank = f.name.split(".")[0]
            ai, human = _read(f), _read(d / "edits" / f"{rank}.json")
            if not ai or not human or not human.get("human_saved_at") or not rank.isdigit():
                continue
            try:
                dif = diff_edit(ai.get("edit") or ai, human)
            except (KeyError, TypeError, ValueError):
                continue
            rows.append({"session": d.name, "rank": int(rank), "at": human["human_saved_at"], **dif})
    return rows


def edit_taste(out_dir: Path, min_edits: int = 5, min_sessions: int = 2) -> dict:
    rows = collect_diffs(out_dir)
    sessions = len({r["session"] for r in rows})
    by = {t: {"ai": 0, "kept": 0, "removed": 0, "added": 0} for t in TYPE_NAME}
    for r in rows:
        for t in TYPE_NAME:
            by[t]["ai"] += r["ai_counts"][t]
            by[t]["added"] += sum(1 for x in r["added"] if x["type"] == t)
            by[t]["removed"] += sum(1 for x in r["removed"] if x["type"] == t)
        for t in TYPE_NAME:
            by[t]["kept"] = by[t]["ai"] - by[t]["removed"]
    latest = sorted(rows, key=lambda r: r["at"], reverse=True)
    shifts = [r["trim_shift"] for r in rows]
    taste = {"n": len(rows), "sessions": sessions, "needed": min_edits, "needed_sessions": min_sessions,
             "active": len(rows) >= min_edits and sessions >= min_sessions,
             "unchanged": sum(1 for r in rows if not r["changed"]), "by_type": by,
             "avg_ai_items": round(sum(sum(r["ai_counts"].values()) for r in rows) / len(rows), 1) if rows else 0,
             "avg_human_items": round(sum(sum(r["human_counts"].values()) for r in rows) / len(rows), 1) if rows else 0,
             "text_changes": [dict(c, session=r["session"], rank=r["rank"]) for r in latest for c in r["text_changed"]][:8],
             "removed_examples": [dict(x, session=r["session"], rank=r["rank"]) for r in latest for x in r["removed"]
                                  if x["type"] in ("text", "sticker", "narration", "fakeui") and x["text"]][:8],
             "trim_shift": {"start": round(sum(s["start"] for s in shifts) / len(shifts), 1),
                            "end": round(sum(s["end"] for s in shifts) / len(shifts), 1)} if shifts else None,
             "recent": [{k: r[k] for k in ("session", "rank", "at", "kept", "retimed", "changed", "trim_shift")}
                        | {"removed": len(r["removed"]), "added": len(r["added"]), "text_changed": len(r["text_changed"])}
                        for r in latest[:20]]}
    if not taste["active"]:
        need = []
        if len(rows) < min_edits:
            need.append(f"还差 {min_edits - len(rows)} 条人工修改")
        if sessions < min_sessions:
            need.append(f"还需要来自 {min_sessions - sessions} 场以上的不同场次")
        taste["status"] = "，".join(need) + "，才会影响 AI 初稿"
    else:
        taste["status"] = f"已生效：{len(rows)} 条人工修改（{sessions} 场）"
    return taste


def taste_text(t: dict | None) -> str:
    """填进 AI 初稿提示词的文字。没攒够返回空串（提示词里写“还没有积累”）。"""
    if not t or not t.get("active"):
        return ""
    lines = []
    for k, name in TYPE_NAME.items():
        b = t["by_type"][k]
        if b["ai"] >= 3:
            rate = b["removed"] / b["ai"]
            if rate >= 0.4:
                lines.append(f"- {name}：AI 排的有 {rate:.0%} 被剪辑师删掉了，少排、只留最有把握的")
            elif rate <= 0.15:
                lines.append(f"- {name}：基本都被保留（只删了 {rate:.0%}）")
        if b["added"] >= 3:
            lines.append(f"- {name}：剪辑师还自己另外加了 {b['added']} 个，可以适当多排一点")
    if t["avg_human_items"] or t["avg_ai_items"]:
        lines.append(f"- 数量：AI 平均排 {t['avg_ai_items']:g} 个效果，改完剩 {t['avg_human_items']:g} 个")
    ex = [x for x in t.get("removed_examples") or [] if x["type"] in ("text", "narration")][:4]
    if ex:
        lines.append("- 被删掉的花字：" + "；".join(f"“{x['text']}”" for x in ex))
    tc = t.get("text_changes") or []
    if tc:
        lines.append("- 花字被改过：" + "；".join(f"“{c['from']}”→“{c['to']}”" for c in tc[:4]))
    ts = t.get("trim_shift")
    if ts and (abs(ts["start"]) >= 1 or abs(ts["end"]) >= 1):
        parts = []
        if abs(ts["start"]) >= 1:
            parts.append(f"开头平均{'往后挪' if ts['start'] > 0 else '往前挪'} {abs(ts['start']):g} 秒")
        if abs(ts["end"]) >= 1:
            parts.append(f"结尾平均{'往后挪' if ts['end'] > 0 else '往前挪'} {abs(ts['end']):g} 秒")
        lines.append("- 裁剪范围：剪辑师通常把" + "，".join(parts) + "（相对 AI 建议的范围）")
    if not lines:
        return f"（已有 {t['n']} 条人工修改，剪辑师基本没改动 AI 的编排，保持现在的风格即可）"
    return f"（根据 {t['n']} 条人工修改总结，供参考，不要照抄）\n" + "\n".join(lines)
