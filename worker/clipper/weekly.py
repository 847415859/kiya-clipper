"""主播周报：把一周的场次整理成主播看得懂的内容报告。

三块内容（都是本地统计，不调大模型、不花钱）：
  1. 本周最火的梗 / 说法 TOP5：主播说了多少次、观众刷了多少条、较上周涨跌、哪一幕里出现过
  2. 本周高光场面 TOP5 + 哪类内容最容易出效果（破防 / 口胡 / 高能…）；攒够 min_type_items 个才出类型占比，
     切片号的播放表现要绑定满 min_published 条才有（数据来自 learn.py 的 memory.json）
  3. 观众弹幕高频词（词云）

报告分两部分：
  public   给主播看的：只有梗、场面、类型、词云。导出的文档只渲染这一部分
  internal 给运营看的：数据够不够、排除了多少禁区 / 标了不有趣的片段。不进导出的文档

禁区话题（taboo）命中的片段、审核时标了 N 的片段不会出现在给主播的内容里。
"""
from __future__ import annotations

import html
import json
import math
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from .kcfg import find_taboo
from .memory import STOP, _boring, load_marks, mine_lore, ngrams_of_session

DEFAULTS = {"days": 7, "memes_top": 5, "highlights_top": 5, "words_top": 40, "min_mentions": 5, "min_type_items": 5}
TYPE_HINT = {
    "破防": "主播绷不住、情绪突然崩掉", "口胡": "嘴快说错话 / 离谱发言", "互动": "和观众的来回",
    "金句": "一句话被反复引用", "高能": "声音和节奏一起拉满", "整活": "主动搞的节目效果", "反差": "前后落差大",
    "其他": "",
}


def read_json(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
    except (OSError, ValueError):
        return default


def session_start(name: str) -> datetime | None:
    try:
        return datetime.strptime(name[:13], "%Y%m%d-%H%M")
    except ValueError:
        return None


def window(end: date, days: int) -> tuple[date, date]:
    return end - timedelta(days=days - 1), end


def sessions_in(out_dir: Path, start: date, end: date) -> list[str]:
    names = []
    for d in sorted(p for p in out_dir.iterdir() if p.is_dir() and not p.name.startswith("_")):
        st = session_start(d.name)
        if st and start <= st.date() <= end and ((d / "candidates.json").exists() or (d / "transcript.json").exists()):
            names.append(d.name)
    return names


def _merge(per: dict[str, dict]) -> dict:
    """几场的 n-gram 计数加在一起：{gram: {speech, danmaku, hot, sessions}}。"""
    out: dict[str, dict] = defaultdict(lambda: {"speech": 0, "danmaku": 0, "hot": 0, "sessions": 0})
    for d in per.values():
        seen = set()
        for key in ("speech", "danmaku", "hot"):
            for g, n in (d.get(key) or {}).items():
                out[g][key] += n
                seen.add(g)
        for g in seen:
            out[g]["sessions"] += 1
    return out


def _lore_words(cfg: dict) -> set[str]:
    lore = cfg.get("lore") or {}
    words = set()
    for k in ("catchphrases", "recurring_topics", "memes", "nicknames", "world"):
        words.update(str(x).lower() for x in lore.get(k) or [])
    words.add(str((cfg.get("streamer") or {}).get("name", "")).lower())
    return {w for w in words if w}


def _bad_word(g: str, taboo: list[str], name: str) -> bool:
    return _boring(g) or bool(find_taboo(g, taboo)) or (name and g in name.lower())


# ---------------------------------------------------------------- 梗
def top_memes(per: dict[str, dict], prev: dict[str, dict], cfg: dict, rejected: set[str], moments: list[dict],
              top: int, min_mentions: int) -> list[dict]:
    taboo = cfg.get("taboo") or []
    known = _lore_words(cfg)
    rows = mine_lore(per, set(), rejected, [], min_sessions=1, limit=80)
    prevm = _merge(prev)
    out = []
    for r in rows:
        g = r["text"]
        mentions = r["speech"] + r["danmaku"]
        if mentions < min_mentions or find_taboo(g, taboo):
            continue
        before = prevm[g]["speech"] + prevm[g]["danmaku"] if g in prevm else 0
        mom = next((m for m in moments if g in m["_speech"].lower()), None)
        out.append({"text": g, "mentions": mentions, "speech": r["speech"], "danmaku": r["danmaku"], "sessions": r["sessions"],
                    "prev": before, "is_new": before == 0, "known": g in known,
                    "trend": None if before == 0 else round((mentions - before) / before, 2),
                    "moment": ({k: v for k, v in mom.items() if not k.startswith("_")} if mom else None)})
    return out[:top]


# ---------------------------------------------------------------- 高光场面 / 类型
def collect_moments(out_dir: Path, names: list[str], reviews: Path | None, taboo: list[str]) -> tuple[list[dict], dict]:
    """本周被精排认为好笑的片段（去掉禁区和人工标了不有趣的），按精排总分从高到低。"""
    marks = load_marks(reviews) if reviews and reviews.exists() else {}
    moments, info = [], {"reranked": 0, "plain": 0, "taboo": 0, "marked_n": 0}
    for n in names:
        d = out_dir / n
        rr = read_json(d / "rerank.json")
        cj = read_json(d / "candidates.json") or {}
        speech_by = {c["rank"]: " ".join(s["text"] for s in c.get("speech") or []) for c in cj.get("candidates", [])}
        st = session_start(n)
        cands = (rr or {}).get("candidates") or []
        if cands:
            info["reranked"] += 1
        for c in cands:
            llm = c.get("llm") or {}
            if not llm.get("funny") or c.get("error"):
                continue
            if c.get("taboo") or find_taboo(llm.get("what_happened", "") + speech_by.get(c["rank"], ""), taboo):
                info["taboo"] += 1
                continue
            if (marks.get(n, {}).get(c["rank"]) or {}).get("mark") == "N":
                info["marked_n"] += 1
                continue
            t = c.get("t", 0) + 0.0
            clock = st + timedelta(seconds=t) if st else None
            moments.append({"session": n, "rank": c["rank"], "type": llm.get("type") or "其他",
                            "what": llm.get("what_happened") or "", "title": (llm.get("title_candidates") or [""])[0],
                            "date": clock.strftime("%m月%d日 %H:%M") if clock else n,
                            "at": f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}",
                            "final": float(c.get("final") or llm.get("score") or 0), "ai_score": float(llm.get("score") or 0),
                            "_speech": speech_by.get(c["rank"], "") + " " + (llm.get("what_happened") or "")})
    moments.sort(key=lambda m: -m["final"])
    return moments, info


def type_table(moments: list[dict], uploads_stats: dict, min_items: int) -> dict:
    n = len(moments)
    if n < min_items:
        return {"enough": False, "have": n, "need": min_items, "rows": []}
    by = defaultdict(list)
    for m in moments:
        by[m["type"]].append(m)
    rows = []
    for t, ms in by.items():
        up = (uploads_stats or {}).get(t)
        rows.append({"type": t, "n": len(ms), "share": round(len(ms) / n, 3), "hint": TYPE_HINT.get(t, ""),
                     "avg_ai": round(sum(m["ai_score"] for m in ms) / len(ms), 1),
                     "view_rel": up["relative"] if up else None, "view_n": up["n"] if up else 0})
    rows.sort(key=lambda r: (-r["n"], -r["avg_ai"]))
    return {"enough": True, "have": n, "need": min_items, "rows": rows, "has_views": any(r["view_rel"] is not None for r in rows)}


# ---------------------------------------------------------------- 词云
def word_cloud(per: dict[str, dict], cfg: dict, top: int) -> list[dict]:
    taboo = cfg.get("taboo") or []
    name = str((cfg.get("streamer") or {}).get("name", ""))
    cnt = Counter()
    for d in per.values():
        for g, n in (d.get("danmaku") or {}).items():
            cnt[g] += n
    cand = {g: n for g, n in cnt.items() if g not in STOP and not _bad_word(g, taboo, name)}
    keep = {}
    for g in sorted(cand, key=len):                 # 被更长的说法几乎包含的子串去掉（“律师函”留下，“师函”去掉）
        if any(len(h) > len(g) and g in h and cand[h] >= 0.6 * cand[g] for h in cand):
            continue
        keep[g] = cand[g]
    rows = sorted(keep.items(), key=lambda kv: (-kv[1], -len(kv[0])))[:top]
    if not rows:
        return []
    hi, lo = math.log1p(rows[0][1]), math.log1p(rows[-1][1])
    return [{"text": g, "count": n, "weight": round((math.log1p(n) - lo) / (hi - lo), 3) if hi > lo else 1.0} for g, n in rows]


# ---------------------------------------------------------------- 汇总
def build_report(out_dir: Path, cfg: dict, end: date, reviews: Path | None = None, memory: dict | None = None,
                 rejected: set[str] | None = None, log=print) -> dict:
    wc = {**DEFAULTS, **(cfg.get("weekly") or {})}
    days = int(wc["days"])
    start, end = window(end, days)
    p_start, p_end = window(start - timedelta(days=1), days)
    names, prev_names = sessions_in(out_dir, start, end), sessions_in(out_dir, p_start, p_end)
    taboo = cfg.get("taboo") or []

    def grams(ns):
        per = {}
        for n in ns:
            g = ngrams_of_session(out_dir / n, log)
            if g:
                per[n] = g
        return per

    per, prev = grams(names), grams(prev_names)
    moments, minfo = collect_moments(out_dir, names, reviews, taboo)
    ts = (memory or {}).get("type_stats") or {}
    memes = top_memes(per, prev, cfg, {x.lower() for x in rejected or set()}, moments, int(wc["memes_top"]), int(wc["min_mentions"]))
    types = type_table(moments, ts, int(wc["min_type_items"]))
    words = word_cloud(per, cfg, int(wc["words_top"]))
    highlights = [{k: v for k, v in m.items() if not k.startswith("_") and k not in ("final", "ai_score")}
                  for m in moments[:int(wc["highlights_top"])]]
    plain_note = None
    if not minfo["reranked"] and names:
        plain_note = "这周的场次还没整理出高光场面，所以先只有梗和词云。"
    public = {
        "overview": {"sessions": len(names), "days": [n[:8] for n in names], "danmaku": sum(g.get("danmaku_count", 0) for g in per.values()),
                     "moments": len(moments), "prev_sessions": len(prev_names)},
        "memes": memes, "highlights": highlights, "types": types, "words": words, "note": plain_note,
    }
    internal = {"sessions": names, "prev_sessions": prev_names, "with_ngrams": sorted(per), "reranked": minfo["reranked"],
                "excluded_taboo": minfo["taboo"], "excluded_marked_n": minfo["marked_n"],
                "type_gate": f"{types['have']}/{types['need']}", "uploads_types": sorted(ts),
                "notes": _notes(names, per, minfo, types, ts, wc, int((cfg.get('memory') or {}).get('min_published', 5)))}
    return {"streamer": (cfg.get("streamer") or {}).get("name", ""), "generated_at": time.strftime("%F %T"),
            "period": {"start": start.isoformat(), "end": end.isoformat(), "days": days,
                       "label": f"{start.month}月{start.day}日 – {end.month}月{end.day}日"},
            "public": public, "internal": internal}


def _notes(names, per, minfo, types, ts, wc, min_published: int = 5) -> list[str]:
    out = []
    if not names:
        out.append("这个时间范围里没有场次（需要 output/<场次>/ 下有 candidates.json 或 transcript.json）。")
    miss = [n for n in names if n not in per]
    if miss:
        out.append(f"{len(miss)} 场没法统计梗和词云（没有转写也没有弹幕记录）：{'、'.join(miss[:3])}")
    if names and not minfo["reranked"]:
        out.append("本周没有一场做过精排：先在“场次”页对这几场点“大模型精排”。")
    if not types["enough"] and minfo["reranked"]:
        out.append(f"高光片段 {types['have']} 个，不到 {types['need']} 个，类型占比先不出（避免凭几个片段下结论）。")
    if types["enough"] and not ts:
        out.append(f"切片号的播放表现还没有：在“精排记忆”页填切片号 UID 并绑定满 {min_published} 条成片后才会出现。")
    if minfo["taboo"] or minfo["marked_n"]:
        out.append(f"已排除：禁区话题 {minfo['taboo']} 个、审核标了不有趣的 {minfo['marked_n']} 个（不会出现在给主播的文档里）。")
    return out


# ---------------------------------------------------------------- 导出文档（只渲染 public）
PALETTE = ["#d6336c", "#7048e8", "#1c7ed6", "#0ca678", "#f08c00", "#e8590c"]


def _e(x) -> str:
    return html.escape(str(x), quote=True)


def render_html(rep: dict) -> str:
    p, per = rep["public"], rep["period"]
    ov = p["overview"]
    who = rep.get("streamer") or "主播"
    parts = [f"<h1>{_e(who)} 内容周报</h1>", f'<p class="sub">{_e(per["label"])} · 共 {ov["sessions"]} 场直播'
             + (f"，观众弹幕 {ov['danmaku']:,} 条" if ov["danmaku"] else "") + (f"，精选高光 {ov['moments']} 个" if ov["moments"] else "") + "</p>"]
    if p.get("note"):
        parts.append(f'<p class="note">{_e(p["note"])}</p>')
    # 梗
    parts.append("<h2>本周最火的梗 / 说法</h2>")
    if p["memes"]:
        parts.append('<ol class="memes">')
        for m in p["memes"]:
            tr = "本周新冒出来的" if m["is_new"] else (f"较上周 {'+' if m['trend'] >= 0 else ''}{round(m['trend'] * 100)}%")
            line = f'你说了 {m["speech"]} 次，观众刷了 {m["danmaku"]} 条 · {tr}' + ("" if m["known"] else " · 新发现")
            mom = f'<div class="mom">出现在 {_e(m["moment"]["date"])}：{_e(m["moment"]["what"])}</div>' if m.get("moment") and m["moment"]["what"] else ""
            parts.append(f'<li><b>{_e(m["text"])}</b><span>{_e(line)}</span>{mom}</li>')
        parts.append("</ol>")
    else:
        parts.append('<p class="muted">这周还没有反复出现的说法（需要有转写或弹幕记录，且至少出现几次）。</p>')
    # 高光
    parts.append("<h2>本周高光场面</h2>")
    if p["highlights"]:
        parts.append('<ol class="hl">')
        for h in p["highlights"]:
            title = f'<div class="t">{_e(h["title"])}</div>' if h.get("title") else ""
            parts.append(f'<li><span class="tag">{_e(h["type"])}</span><b>{_e(h["date"])}</b>（开播后 {_e(h["at"])}）{title}'
                         f'<div>{_e(h["what"])}</div></li>')
        parts.append("</ol>")
    else:
        parts.append('<p class="muted">本周还没有精选出的高光场面。</p>')
    # 类型
    parts.append("<h2>哪类内容最容易出效果</h2>")
    ty = p["types"]
    if ty["enough"]:
        parts.append("<table><tr><th>类型</th><th>本周高光数</th><th>占比</th>" + ("<th>切片播放表现</th>" if ty.get("has_views") else "") + "</tr>")
        for r in ty["rows"]:
            vr = ("—" if r["view_rel"] is None else f"{r['view_rel']}× 中位数（{r['view_n']} 条）")
            parts.append(f'<tr><td><b>{_e(r["type"])}</b><small>{_e(r["hint"])}</small></td><td>{r["n"]}</td>'
                         f'<td>{round(r["share"] * 100)}%</td>' + (f"<td>{_e(vr)}</td>" if ty.get("has_views") else "") + "</tr>")
        parts.append("</table>")
    else:
        parts.append(f'<p class="muted">高光片段还不够多（{ty["have"]}/{ty["need"]}），等再多播几场再看哪类内容更出效果。</p>')
    # 词云
    parts.append("<h2>观众最常刷的词</h2>")
    if p["words"]:
        parts.append('<div class="cloud">' + "".join(
            f'<span style="font-size:{round(15 + w["weight"] * 30)}px;color:{PALETTE[i % len(PALETTE)]};'
            f'opacity:{round(0.65 + w["weight"] * 0.35, 2)}" title="{w["count"]} 条">{_e(w["text"])}</span>'
            for i, w in enumerate(p["words"])) + "</div>")
    else:
        parts.append('<p class="muted">本周没有弹幕记录。</p>')
    parts.append(f'<p class="foot">生成于 {_e(rep["generated_at"])}</p>')
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(who)} 内容周报 {_e(per["label"])}</title>
<style>
:root {{ --bg:#fff; --fg:#1f2328; --mut:#6b7280; --line:#e5e7eb; --card:#f8f9fb; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#14161a; --fg:#e6e8eb; --mut:#9aa1ab; --line:#2a2e35; --card:#1b1e23; }} }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:16px/1.65 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif; }}
main {{ max-width:760px; margin:0 auto; padding:28px 16px 48px; }}
h1 {{ margin:0 0 4px; font-size:26px; }} h2 {{ margin:32px 0 10px; font-size:19px; padding-bottom:6px; border-bottom:1px solid var(--line); }}
.sub,.muted,.foot,small {{ color:var(--mut); }} .sub {{ margin:0 0 8px; }} .foot {{ margin-top:36px; font-size:13px; }}
.note {{ background:var(--card); padding:8px 12px; border-radius:8px; }}
ol {{ padding-left:22px; }} li {{ margin:0 0 12px; }}
.memes b {{ font-size:19px; margin-right:10px; }} .memes span {{ color:var(--mut); font-size:14px; }}
.mom {{ margin-top:2px; font-size:14px; }}
.hl .tag {{ display:inline-block; margin-right:8px; padding:0 8px; border-radius:10px; background:var(--card); border:1px solid var(--line); font-size:13px; }}
.hl .t {{ font-weight:600; margin-top:2px; }}
table {{ width:100%; border-collapse:collapse; }} th,td {{ text-align:left; padding:8px 6px; border-bottom:1px solid var(--line); vertical-align:top; }}
td small {{ display:block; font-size:12px; }}
.cloud {{ display:flex; flex-wrap:wrap; gap:6px 16px; align-items:baseline; padding:14px; background:var(--card); border-radius:10px; line-height:1.3; }}
@media print {{ main {{ padding:0; }} }}
</style></head><body><main>
{"".join(parts)}
</main></body></html>"""
