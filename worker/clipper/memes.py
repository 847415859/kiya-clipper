"""梗库：流行梗的存储、热度衰减、准入审核、向量检索，以及“用了哪些梗、被保留了多少”的统计。

来源（同一个说法多个来源会合并）：
  主播档案   kiya.yaml 里的专属梗 / 口头禅 / 梗词（含后台“转正”的），默认可用、不衰减
  弹幕 / 转写  learn.py 从各场发现的反复出现的说法（output/_memory/memory.json），要人工确认才可用
  B站热门    clipper/hot.py 每天抓的热门标题 / 热搜里的高频短语，要人工确认才可用
  手动添加   后台“梗库”页加的

准入：新梗（弹幕 / 转写、B站热门来的）默认“待确认”，后台填好含义和尺度、点通过后才会被自动使用；
黑名单词和禁区话题命中的直接拉黑。后台的决定在 config/memory_decisions.json 的 memes / meme_blacklist。

热度：B站热门来的按近 7 天加权日均出现次数算，跌破 min_heat 权重降到 0.3（不会一直推过气的梗）；
使用统计来自剪辑初稿：AI 初稿用了几次、人工终稿里还留着几次，用够 min_uses 次且保留率低于 low_retention 再降权。

检索：先给片段写一句“情境”（精排结论里的“发生了什么 + 铺垫 / 笑点 / 反应”），用文本向量在可用的梗里找 Top K，
相似度不够的不给——宁缺毋滥；没有向量（没 Key / 接口失败）时退回字面相似度。
"""
from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

import numpy as np

from .embed import Embedder, EmbedError, cosine_scores, lexical_scores
from .hot import heat as hot_heat
from .kcfg import find_taboo

STATUSES = ("pending", "approved", "disabled", "blacklisted")
STATUS_LABEL = {"pending": "待确认", "approved": "已启用", "disabled": "已停用", "blacklisted": "黑名单"}
LEVELS = {1: "大众", 2: "圈层", 3: "谨慎"}          # 尺度：谨慎级默认不自动用
FORMS = ("text", "sticker")                         # 目前剪辑能落地的形式：梗字幕（花字）、梗图贴图
DEFAULTS = {"hot_min_titles": 3, "hot_pending_max": 60, "min_heat": 0.5, "max_level": 2, "retrieve_k": 10, "min_sim": 0.35,
            "min_lexical": 0.2, "min_uses": 5, "low_retention": 0.4, "max_per_clip": 2, "min_gap": 15.0, "embed_model": ""}


def settings(cfg: dict) -> dict:
    return {**DEFAULTS, **(cfg.get("memes") or {})}


def _key(t: str) -> str:
    return str(t).strip().lower()


# ---------------------------------------------------------------- 使用统计
def meme_stats(out_dir: Path) -> dict[str, dict]:
    """{梗: {uses, judged, kept, retention}}。uses = AI 初稿里挂了这个梗的效果数；
    judged = 其中人工保存过终稿的场次；kept = 终稿里还留着同一个梗的。"""
    st: dict[str, dict] = {}
    for ai in sorted(out_dir.glob("*/edits/*.ai.json")) if out_dir.exists() else []:
        try:
            draft = json.loads(ai.read_text(encoding="utf-8")).get("edit") or {}
            final = json.loads(ai.with_name(ai.name.replace(".ai.json", ".json")).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            final = None
            try:
                draft = json.loads(ai.read_text(encoding="utf-8")).get("edit") or {}
            except (OSError, ValueError):
                continue
        used = {it["meme"] for it in draft.get("items") or [] if it.get("meme")}
        human = bool(final and final.get("human_saved_at"))
        keep = {it.get("meme") for it in (final or {}).get("items") or []}
        for m in used:
            e = st.setdefault(_key(m), {"uses": 0, "judged": 0, "kept": 0})
            e["uses"] += 1
            if human:
                e["judged"] += 1
                e["kept"] += 1 if m in keep else 0
    for e in st.values():
        e["retention"] = round(e["kept"] / e["judged"], 2) if e["judged"] else None
    return st


# ---------------------------------------------------------------- 建库
def build_library(cfg: dict, decisions: dict, memory: dict | None, history: dict, today: date,
                  stats: dict | None = None, stream_recent: dict | None = None, previous: list[dict] | None = None) -> list[dict]:
    s = settings(cfg)
    dm = {_key(k): v for k, v in (decisions.get("memes") or {}).items() if isinstance(v, dict)}
    black = [str(w).strip() for w in decisions.get("meme_blacklist") or [] if str(w).strip()]
    taboo = list(cfg.get("taboo") or [])
    prev = {_key(e["text"]): e for e in previous or []}
    stats = stats or {}
    lib: dict[str, dict] = {}

    def ent(text: str, src: str) -> dict:
        k = _key(text)
        e = lib.setdefault(k, {"text": str(text).strip(), "sources": [], "status": "pending", "level": 1, "meaning": "",
                               "origin": "", "usage": "", "forms": ["text"], "sticker": "", "tags": [], "evergreen": False,
                               "heat": None, "heat_days": 0})
        if src not in e["sources"]:
            e["sources"].append(src)
        return e

    lore = cfg.get("lore") or {}
    for w in [*(lore.get("memes") or []), *(lore.get("catchphrases") or []), *(cfg.get("lore_words") or [])]:
        if str(w).strip():
            e = ent(w, "主播档案")
            e.update(status="approved", evergreen=True)
    for x in (memory or {}).get("lore") or []:
        if str(x.get("text", "")).strip():
            e = ent(x["text"], "主播档案（手动）" if x.get("status") == "manual" else "弹幕 / 转写")
            if x.get("status") == "manual":
                e.update(status="approved", evergreen=True, meaning=e["meaning"] or str(x.get("note") or ""))
            e["mentions"] = int(x.get("speech", 0)) + int(x.get("danmaku", 0))
            e["_last"] = x.get("last_seen") or x.get("first_added")
    hot_rows = []
    for text in {t for ph in history.values() for t in ph}:
        h, days = hot_heat(text, history, today)
        if h > 0:
            hot_rows.append((h, days, text))
    hot_rows.sort(reverse=True)
    for h, days, text in hot_rows[:int(s["hot_pending_max"])]:
        ent(text, "B站热门")
    for k, d in dm.items():                                    # 手动添加的 / 有决定的，不管有没有热度都留着
        if d.get("manual") and k not in lib:
            ent(d.get("text") or k, "手动添加")

    out = []
    for k, e in lib.items():
        d = dm.get(k) or {}
        if d.get("status") in STATUSES:
            e["status"] = d["status"]
        for f in ("level", "meaning", "origin", "usage", "forms", "sticker", "tags", "note"):
            if f in d and d[f] not in (None, ""):
                e[f] = d[f]
        e["level"] = int(e["level"]) if str(e["level"]).isdigit() else 1
        hit = find_taboo(e["text"], black) or find_taboo(e["text"], taboo)
        if hit:
            e.update(status="blacklisted", blocked_by=hit[0])
        h, days = hot_heat(e["text"], history, today)
        if "B站热门" in e["sources"] or h > 0:
            e["heat"], e["heat_days"] = h, days
        w = 1.0
        if stream_recent is not None and "B站热门" not in e["sources"]:
            e["heat"] = float(stream_recent.get(k, 0))          # 弹幕 / 转写来的：近 14 天提到几次
            if not e["evergreen"] and e["heat"] < 1:
                w, e["cooling"] = 0.3, True
        elif not e["evergreen"] and e["heat"] is not None and e["heat"] < float(s["min_heat"]):
            w, e["cooling"] = 0.3, True
        st = stats.get(k) or {}
        e["uses"], e["kept"], e["judged"], e["retention"] = st.get("uses", 0), st.get("kept", 0), st.get("judged", 0), st.get("retention")
        if st.get("judged", 0) >= int(s["min_uses"]) and st.get("retention") is not None and st["retention"] < float(s["low_retention"]):
            w *= 0.5
            e["low_retention"] = True
        e["weight"] = round(w, 2)
        p = prev.get(k) or {}
        e["first_seen"] = p.get("first_seen") or today.isoformat()
        e["last_seen"] = today.isoformat() if days else (e.pop("_last", None) or p.get("last_seen", ""))
        e.pop("_last", None)
        out.append(e)
    order = {"approved": 0, "pending": 1, "disabled": 2, "blacklisted": 3}
    out.sort(key=lambda e: (order[e["status"]], -(e["heat"] or 0), e["text"]))
    return out


def write_library(path: Path, lib: list[dict], meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({**meta, "generated_at": time.strftime("%F %T"), "memes": lib}, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def load_library(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("memes") or []
    except (OSError, ValueError):
        return []


# ---------------------------------------------------------------- 检索
def usable(lib: list[dict], cfg: dict, taboo: list[str] | None = None) -> list[dict]:
    s = settings(cfg)
    black = [w for w in taboo or []]
    return [e for e in lib if e.get("status") == "approved" and int(e.get("level", 1)) <= int(s["max_level"])
            and e.get("weight", 1) > 0 and not find_taboo(e["text"], black)]


def doc_text(e: dict) -> str:
    return "。".join(x for x in [e["text"], e.get("meaning", ""), e.get("usage", ""), " ".join(e.get("tags") or [])] if x)


def situation_text(llm: dict | None, extra: str = "") -> str:
    """精排结论 → 一句情境描述（检索用）。没有精排结论时返回 extra（比如弹幕样本）。"""
    llm = llm or {}
    st = llm.get("story") or {}
    parts = [llm.get("what_happened") or "", st.get("setup") or "", st.get("punch") or "", st.get("reaction") or ""]
    return "。".join(p for p in parts if p).strip() or extra.strip()


def retrieve(situation: str, lib: list[dict], cfg: dict, embedder: Embedder | None = None, taboo: list[str] | None = None,
             k: int | None = None, log=print) -> tuple[list[dict], str]:
    """返回 (候选 Top K, 方法 vector / lexical / none)。每条带 sim（相似度）和 score（sim × 权重）。"""
    s = settings(cfg)
    pool = usable(lib, cfg, taboo)
    if not pool or not situation.strip():
        return [], "none"
    docs = [doc_text(e) for e in pool]
    method, sims = "lexical", None
    emb = embedder or Embedder(model=s["embed_model"] or None)
    if emb.available:
        try:
            m = emb.embed(docs + [situation])
            sims = cosine_scores(m[-1], m[:-1])
            method = "vector"
        except EmbedError as e:
            log(f"  文本向量不可用，退回字面匹配：{e}")
    if sims is None:
        sims = lexical_scores(situation, docs)
        low = situation.lower()
        sims = np.array([1.0 if e["text"].lower() in low else x for e, x in zip(pool, sims)], dtype=np.float32)   # 梗的原话出现在情境里
    floor = float(s["min_sim"] if method == "vector" else s["min_lexical"])
    rows = []
    for e, sim in zip(pool, sims):
        if sim >= floor:
            rows.append({**e, "sim": round(float(sim), 3), "score": round(float(sim) * float(e.get("weight", 1)), 3)})
    rows.sort(key=lambda r: -r["score"])
    return rows[:int(k or s["retrieve_k"])], method


def offer_text(rows: list[dict]) -> str:
    """填进提示词的“可用的梗”。"""
    if not rows:
        return "（梗库里没有和这段情境相符的梗，不要用梗。）"
    out = []
    for r in rows:
        bits = [f"《{r['text']}》", f"含义：{r['meaning']}" if r.get("meaning") else "", f"用法：{r['usage']}" if r.get("usage") else "",
                f"对应贴图：{r['sticker']}（挂这个梗时就用它，不要另选贴图）" if r.get("sticker") else "",
                f"尺度：{LEVELS.get(int(r.get('level', 1)), '')}"]
        out.append("- " + "；".join(b for b in bits if b))
    return "\n".join(out)


# ---------------------------------------------------------------- 剪辑脚本里的梗
def enforce_memes(edit: dict, offered: set[str], max_per_clip: int = 2, min_gap: float = 15.0) -> tuple[dict, list[str]]:
    """初稿里挂了梗的效果：不在“可用的梗”里的去掉标记（模型自己编的）；一条最多 max_per_clip 个、两个至少隔 min_gap 秒，
    超出的整个效果去掉。返回 (新脚本, 说明)。"""
    notes, keep, last = [], [], None
    offered_l = {_key(x) for x in offered}
    n = 0
    for it in sorted(edit.get("items") or [], key=lambda i: i["start"]):
        m = it.get("meme")
        if m:
            if _key(m) not in offered_l:
                it = {k: v for k, v in it.items() if k not in ("meme", "meme_reason")}
                notes.append(f"“{m}”不在可用的梗里，去掉了梗标记")
            elif n >= max_per_clip or (last is not None and it["start"] - last < min_gap):
                notes.append(f"梗“{m}”太密（每条最多 {max_per_clip} 个、间隔 {min_gap:.0f} 秒），整个效果去掉了")
                continue
            else:
                n += 1
                last = it["start"]
        keep.append(it)
    return {**edit, "items": keep}, notes


def memes_in_edit(edit: dict) -> list[dict]:
    """脚本里用到的梗（给后台展示）：[{meme, reason, item, start, text}]。"""
    return [{"meme": it["meme"], "reason": it.get("meme_reason", ""), "item": it["id"], "start": it["start"],
             "text": it.get("text") or it.get("asset") or ""} for it in edit.get("items") or [] if it.get("meme")]
