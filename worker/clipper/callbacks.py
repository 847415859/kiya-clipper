"""跨场次呼应：从历史场次里找“她以前说过的话”，让这条切片能接上以前的梗。

只做两类（口头禅重复第一版不做）：
  flag_payoff  立 flag 与打脸：以前说“再也不 X / 明天一定 Y”，这条切片里她做了相反的事
  reversal     话题前后反转：同一个话题，以前和这次她的态度 / 说法反过来了

流程（每个候选一次，只有检索有命中才会花一次大模型调用）：
  1. 取笑点附近的原话和精排的一句话结论当查询，去全文索引里搜“更早的场次”（立 flag 的句子单独再搜一遍）
  2. 按 2 字组重叠打分过滤：去掉“我们 / 就是”这类通用词和念弹幕 / 谢礼的套话小句，这场常挂嘴边的词只算一点分，
     分数不够的命中直接丢掉（一个够强的都没有就不调大模型）；命中句取前后各几句，合并成历史段落
  3. 大模型判断每个段落是不是真的呼应，并逐字引用历史原话和本片原话
  4. 程序校验：引用必须真的出现在段落 / 本片原文里（否则作废）、置信度够、时长合适、这句原话没被别的片段用过
  5. 检查历史录像还在不在：在就插旧画面（mode=clip），不在就退回文字卡（mode=card）
屏幕上的字（标签）由程序用校验过的原话拼，不让大模型自由发挥。

时间都是整场录像里的秒数；剪辑脚本里的 callback 字段见 clipper/edit.py 的 validate_callback。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from .context import locate
from .memory import STOP
from .transcript_index import bigrams, date_label, norm

RELATIONS = ("flag_payoff", "reversal")
RELATION_NAME = {"flag_payoff": "立 flag 与打脸", "reversal": "话题前后反转"}
STOP_BG = {w for w in STOP if len(w) == 2}            # 通用二字词：重叠里不算数
# 念弹幕 / 谢礼的套话：这类小句不参与匹配（“谢谢X的小花花，祝X天天有钱花”每场都有几十句，互相一撞就是满屏命中）
BOILER = re.compile("谢谢|感谢|多谢|欢迎|小花花|礼物|舰长|提督|醒目留言|有钱花|人气|晚上好|早上好|中午好|大航海|关注|点赞|三连|辛苦")
CLAUSE = re.compile(r"[，,。！？!?；;：:\n]+")

DEFAULTS = {
    "enabled": True,
    "top_n": 8,              # 只给精排前 N 的候选找呼应（每个有命中的候选多一次大模型调用）
    "min_confidence": 0.7,
    "max_per_session": 2,    # 一场最多几条切片带呼应，别条条都“前情提要”
    "min_seconds": 2.0,
    "max_seconds": 8.0,      # 插进来的旧画面最长几秒
    "pad_before": 1.0,       # 引用那句话前后各多留几秒，免得切得太死
    "pad_after": 1.5,
    "min_overlap": 2,        # 查询和命中句至少共有几个“非通用”二字组（三个字的话题词只有 2 个二字组）
    "min_score": 2.0,        # 加权分数的门槛：共有的二字组里，这场少见的（出现不超过 rare_df 句）算 1 分，常见的只算 common_weight 分，立 flag 的句子另加 1.5
    "rare_df": 12,
    "common_weight": 0.4,
    "common_cap": 1.0,       # 常见二字组加起来最多算这么多分（光靠日常词凑，句子再长也过不了门槛）
    "per_query": 8,
    "context": 2,            # 命中句前后各取几句
    "max_passages": 4,       # 送给大模型的历史段落最多几段（宁少勿多：没有强命中就不调大模型）
    "index": "auto",         # auto：有 MySQL 用 MySQL，否则读 transcript.json；files：只读文件
}


def settings(cfg: dict) -> dict:
    return {**DEFAULTS, **(cfg.get("callbacks") or {})}


class CbItem(BaseModel):
    passage: str = ""
    relation: str = "none"
    quote: str = ""
    current_quote: str = ""
    reason: str = ""
    confidence: float = 0.0


class CbOut(BaseModel):
    callbacks: list[CbItem] = Field(default_factory=list)


# ---------------------------------------------------------------- 1. 检索
def content_bigrams(text: str) -> set[str]:
    """内容二字组：按小句切开，念弹幕 / 谢礼的套话小句整个丢掉，再去掉通用词。"""
    out: set[str] = set()
    for c in CLAUSE.split(text or ""):
        if c and not BOILER.search(c):
            out |= bigrams(c)
    return out - STOP_BG


def session_df(sents: list[dict], a: float, b: float) -> dict[str, int]:
    """这一场（片段 [a, b] 之外）每个内容二字组出现在多少句里。常挂在嘴边的词（这场出现很多次）在匹配里只算一点分。"""
    df: dict[str, int] = {}
    for s in sents:
        if s["t1"] > a and s["t0"] < b:
            continue
        for g in content_bigrams(s["text"]):
            df[g] = df.get(g, 0) + 1
    return df


def build_queries(sents: list[dict], a: float, b: float, punch: float | None = None, what_happened: str = "", max_q: int = 5) -> list[str]:
    """查询：笑点附近的几句原话 + 精排的一句话结论。sents 是整场句子，[a, b] 是片段范围，punch 是笑点（整场秒）。"""
    cand = [s for s in sents if s["t1"] > a and s["t0"] < b and len(norm(s["text"])) >= 6]
    if punch is not None:
        near = [s for s in cand if punch - 15 <= s["t0"] <= punch + 8]
        cand = sorted(near or cand, key=lambda s: abs(s["t0"] - punch))
    else:
        cand = sorted(cand, key=lambda s: -len(norm(s["text"])))
    qs = [s["text"].strip() for s in cand[:max_q]]
    if (what_happened or "").strip():
        qs.append(what_happened.strip())
    out: list[str] = []
    for q in qs:
        if q and q not in out:
            out.append(q)
    return out


def retrieve(index, streamer: str, session: str, queries: list[str], cfg: dict, df: dict[str, int] | None = None) -> list:
    """命中句（按分数从高到低）。只看比 session 更早的场次。
    分数 = 共有的少见二字组各 1 分 + 常见二字组各 common_weight 分（合计不超过 common_cap）+ 立 flag 的 1.5；低于 min_score 的丢掉。
    少见 / 常见按 df（这场片段之外出现在多少句里）分；没给 df 就全算少见。所以不带 flag 的句子至少要有一个少见的共有词才可能过门槛。"""
    best: dict[tuple[str, int], object] = {}
    rare, common_w, cap, min_score = int(cfg["rare_df"]), float(cfg["common_weight"]), float(cfg["common_cap"]), float(cfg["min_score"])

    def points(both: set[str]) -> float:
        n_rare = sum(1 for g in both if df is None or df.get(g, 0) <= rare)
        return n_rare + min(cap, (len(both) - n_rare) * common_w)

    def take(hits, q):
        qb = content_bigrams(q)
        for h in hits:
            both = qb & content_bigrams(h.text)
            if len(both) < int(cfg["min_overlap"]):
                continue
            h.score = points(both) + (1.5 if h.flag else 0.0)
            if h.score < min_score:
                continue
            h.shared = tuple(sorted(both))
            k = (h.session, h.idx)
            if k not in best or best[k].score < h.score:
                best[k] = h

    for q in queries:
        take(index.search(streamer, q, before=session, limit=int(cfg["per_query"])), q)
    if queries:
        joined = " ".join(queries)[:300]
        take(index.search(streamer, joined, before=session, limit=int(cfg["per_query"]), flag_only=True), joined)
    return sorted(best.values(), key=lambda h: (-h.score, h.session, h.idx))


def build_passages(index, streamer: str, hits: list, cfg: dict) -> list[dict]:
    """命中句 + 前后文，同一场里挨着的合并成一段。最多 max_passages 段。"""
    ctx = int(cfg["context"])
    spans: list[dict] = []
    for h in hits[: int(cfg["max_passages"]) * 2]:
        sents = index.neighbors(streamer, h.session, h.idx - ctx, h.idx + ctx)
        sents = [s for s in sents if abs(s.t0 - h.t0) <= 15]      # 前后文别跨出太远
        if not sents:
            continue
        lo, hi = min(s.idx for s in sents), max(s.idx for s in sents)
        for sp in spans:
            if sp["session"] == h.session and lo <= sp["hi"] + 1 and hi >= sp["lo"] - 1:
                known = {x["idx"] for x in sp["sentences"]}
                sp["sentences"] += [_row(s) for s in sents if s.idx not in known]
                sp["sentences"].sort(key=lambda x: x["idx"])
                sp["lo"], sp["hi"] = sp["sentences"][0]["idx"], sp["sentences"][-1]["idx"]
                sp["score"] = max(sp["score"], h.score)
                sp["flag"] = sp["flag"] or h.flag
                break
        else:
            spans.append({"session": h.session, "date": date_label(h.session), "sentences": sorted((_row(s) for s in sents), key=lambda x: x["idx"]),
                          "lo": lo, "hi": hi, "score": h.score, "flag": bool(h.flag)})
    spans.sort(key=lambda p: -p["score"])
    spans = spans[: int(cfg["max_passages"])]
    for i, sp in enumerate(spans, 1):
        sp["id"] = f"P{i}"
    return spans


def _row(h) -> dict:
    return {"idx": h.idx, "t0": h.t0, "t1": h.t1, "text": h.text, "flag": bool(h.flag)}


def passages_text(passages: list[dict]) -> str:
    out = []
    for p in passages:
        out.append(f"[{p['id']}] {p['date']}（场次 {p['session']}）" + ("  ※含立 flag 的句子" if p["flag"] else ""))
        for s in p["sentences"]:
            t = int(s["t0"])
            out.append(f"  [{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}] {s['text']}")
    return "\n".join(out) or "（没有）"


# ---------------------------------------------------------------- 3. 判定（大模型）+ 4. 校验
def verify(items: list[CbItem], passages: list[dict], clip_text: str, cfg: dict) -> tuple[list[dict], list[str]]:
    """逐条校验大模型的判断，返回 (通过的, 被丢掉的原因)。"""
    by_id = {p["id"]: p for p in passages}
    clip_n = norm(clip_text)
    ok, why = [], []
    for it in items:
        rel = it.relation
        if rel == "none" or not it.quote:
            continue
        if rel not in RELATIONS:
            why.append(f"关系类型不认识：{rel}")
            continue
        p = by_id.get(it.passage)
        if not p:
            why.append(f"段落 {it.passage} 不存在")
            continue
        if it.confidence < float(cfg["min_confidence"]):
            why.append(f"{it.passage} 置信度 {it.confidence:.2f} 低于 {cfg['min_confidence']}")
            continue
        qn = norm(it.quote)
        sent = next((s for s in p["sentences"] if len(qn) >= 4 and (qn in norm(s["text"]) or (len(norm(s["text"])) >= 4 and norm(s["text"]) in qn))), None)
        if not sent:
            why.append(f"{it.passage} 引用的历史原话「{it.quote[:20]}」在检索到的文字里找不到，作废")
            continue
        cn = norm(it.current_quote)
        if len(cn) < 4 or cn not in clip_n:
            why.append(f"{it.passage} 引用的本片原话「{it.current_quote[:20]}」在这条片段的转写里找不到，作废")
            continue
        start = max(0.0, sent["t0"] - float(cfg["pad_before"]))
        end = sent["t1"] + float(cfg["pad_after"])
        if end - start > float(cfg["max_seconds"]):
            end = start + float(cfg["max_seconds"])                # 句子太长：只取开头一段（引用通常在句首）
        if end - start < float(cfg["min_seconds"]):
            end = start + float(cfg["min_seconds"])
        quote = sent["text"].strip()
        q_short = quote if len(quote) <= 22 else quote[:21] + "…"
        ok.append({"relation": rel, "session": p["session"], "date": p["date"], "start": round(start, 2), "end": round(end, 2),
                   "quote": quote[:120], "current_quote": it.current_quote.strip()[:120], "note": (it.reason or "").strip()[:200],
                   "confidence": round(float(it.confidence), 2), "label": f"{p['date']}｜她说：「{q_short}」"})
    return ok, why


def judge(client, prompt: tuple[str, str, str], clip_speech: str, what_happened: str, passages: list[dict], extra: dict | None = None):
    """调一次大模型，返回 (CbOut 或 None, 错误说明)。"""
    from .llm import fill
    version, system, user = prompt
    text = fill(user, what_happened=what_happened or "（没有精排结论）", clip_speech=clip_speech or "（没有转写）",
                passages=passages_text(passages), **(extra or {}))
    r = client.chat_json([{"role": "system", "content": system}, {"role": "user", "content": text}], CbOut, tag=version)
    return (r.data, "") if r.data is not None else (None, r.error or "未知错误")


# ---------------------------------------------------------------- 5. 旧录像还在不在
def footage_available(sess_root: Path, session: str, start: float, end: float) -> bool:
    """历史场次的录像文件现在还在本地吗（可能已经被同步脚本移走）。需要那场的 candidates.json 里的分段表。"""
    try:
        segs = json.loads((Path(sess_root) / session / "candidates.json").read_text(encoding="utf-8")).get("segments") or []
    except (OSError, ValueError):
        return False
    return bool(segs) and locate(segs, start) is not None and locate(segs, max(start, end - 0.2)) is not None


# ---------------------------------------------------------------- 已用过的原话
def used_path(sess_root: Path) -> Path:
    return Path(sess_root) / "_callbacks_used.json"


def load_used(sess_root: Path) -> dict:
    try:
        return json.loads(used_path(sess_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def used_key(session: str, start: float) -> str:
    return f"{session}@{int(start)}"


def mark_used(sess_root: Path, session: str, start: float, owner: str) -> None:
    d = load_used(sess_root)
    d[used_key(session, start)] = owner
    p = used_path(sess_root)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def count_owned(sess_root: Path, session: str, exclude_owner: str | None = None) -> int:
    """这一场里已经有几条切片用了呼应（含文字卡）。"""
    return sum(1 for v in load_used(sess_root).values() if v.startswith(session + "#") and v != exclude_owner)


# ---------------------------------------------------------------- 总入口
def find_callback(*, index, streamer: str, session: str, sents: list[dict], a: float, b: float, punch: float | None,
                  what_happened: str, cfg: dict, client, prompt: tuple[str, str, str], sess_root: Path, owner: str,
                  speech_text: str) -> tuple[dict | None, list[str], dict]:
    """给一个候选找呼应。a, b = 片段（素材范围）整场秒；punch = 笑点整场秒或 None；speech_text = 本片原话（带相对秒，给大模型看）。
    返回 (callback 或 None, 说明, 调试信息 {queries, passages, verified})。没找到 callback 为 None，说明里写原因。"""
    notes: list[str] = []
    dbg: dict = {"queries": [], "passages": [], "verified": []}
    queries = build_queries(sents, a, b, punch, what_happened)
    dbg["queries"] = queries
    if not queries:
        return None, ["这条片段没有足够长的原话可以检索"], dbg
    hits = retrieve(index, streamer, session, queries, cfg, session_df(sents, a, b))
    if not hits:
        return None, ["历史场次里没有够强的相近内容（不调大模型）"], dbg
    passages = build_passages(index, streamer, hits, cfg)
    dbg["passages"] = passages
    if not passages:
        return None, ["命中的句子取不到前后文"], dbg
    out, err = judge(client, prompt, speech_text, what_happened, passages)
    if out is None:
        return None, [f"呼应判定失败：{err}"], dbg
    clip_text = " ".join(s["text"] for s in sents if s["t1"] > a and s["t0"] < b)
    ok, why = verify(out.callbacks, passages, clip_text, cfg)
    notes += why
    dbg["verified"] = ok
    if not ok:
        return None, notes or ["大模型认为没有值得呼应的"], dbg
    used = load_used(sess_root)
    ok.sort(key=lambda x: (-x["confidence"], x["relation"] != "flag_payoff"))
    for c in ok:
        owner_of = used.get(used_key(c["session"], c["start"]))
        if owner_of and owner_of != owner:
            notes.append(f"{c['date']} 那句已经被 {owner_of} 用过，换一条")
            continue
        c = dict(c)
        c["mode"] = "clip" if footage_available(sess_root, c["session"], c["start"], c["end"]) else "card"
        c["enabled"] = True
        if c["mode"] == "card":
            notes.append(f"{c['date']} 的录像已经不在本地，改用文字卡")
        return c, notes, dbg
    return None, notes, dbg
