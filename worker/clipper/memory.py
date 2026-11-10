"""精排记忆：让精排随着录制、审核和投稿数据自己变化，而不是一份写死的提示词。

learn.py 每次下播后（或后台点“更新精排记忆”）重新汇总，写到 output/_memory/memory.json：
  - 梗 / 口头禅：从各场转写和弹幕里找反复出现的说法（至少出现在 2 场），自动加入；后台删掉的不会再回来
  - 审核例子：审核页的 Y/N 和备注（精排一场时只用其他场的例子，不会拿答案考自己）
  - 她自己的投稿：最近的标题和播放（看她觉得什么值得发、现在聊什么、标题怎么起）
  - 切片号投稿：绑定了 BV 号的成片的播放 / 点赞，按类型汇总（发够 5 条才用）
  - 排序权重：攒够 3 场“精排 + 标注”后，在历史数据上搜一组更好的权重；更好才“建议”，后台确认后生效

提示词模板（prompts/rerank_v2.md）不变，变的是填进去的“记忆”；每次精排都记录用的是哪一版记忆。
后台的决定（删梗、手动加梗、排除例子、绑定 BV 号、应用权重）在 config/memory_decisions.json，learn.py 只读它。
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

NGRAM_VERSION = 1
MIN_N, MAX_N = 2, 6
EDGE = set("的了吗呢吧啊呀哦嗯在和就也都还又这那么个是有我你他她它们给把被让对说")
STOP = set("""这个 那个 什么 就是 我们 你们 他们 然后 一个 没有 不是 可以 怎么 知道 真的 因为 所以 还是 现在 已经 感觉
时候 东西 这样 那样 觉得 但是 如果 自己 还有 一下 一点 好像 这么 那么 为什么 不要 不会 不能 应该 可能 其实 只是 一样 大家
今天 明天 昨天 晚上 刚才 之前 之后 以后 出来 起来 过来 回来 一直 一起 有点 有人 没事 还在 看看 看到 听到 喜欢 谢谢 对不起
主播 直播 弹幕 观众 哈哈 好的 好吧 是的 不对 对吧 是不是 有没有 要不要 会不会 能不能 怎么办 为啥 干嘛 啥意思 我的 你的 他的
她的 我也 你也 也是 都是 就是说 然后呢 所以说 不是吧 真的吗 一个人 这种 那种 这边 那边 里面 外面 上面 下面""".split())
PUNCT = re.compile(r"[^0-9A-Za-z一-鿿]+")
TYPES_ORDER = ["破防", "口胡", "互动", "金句", "高能", "整活", "反差", "其他"]


# ---------------------------------------------------------------- 梗 / 口头禅挖掘
def grams(text: str) -> set[str]:
    out = set()
    for chunk in PUNCT.split(text.lower()):
        for n in range(MIN_N, MAX_N + 1):
            for i in range(len(chunk) - n + 1):
                out.add(chunk[i:i + n])
    return out


def count_grams(texts) -> Counter:
    """每句 / 每条弹幕里出现过就记 1 次（一条弹幕刷十遍也只算一次）。"""
    c = Counter()
    for t in texts:
        c.update(grams(t))
    return c


def session_ngrams(sess_dir: Path, sentences: list[dict], danmaku: list[tuple[float, str]],
                   windows: list[tuple[float, float]]) -> dict:
    """一场的 n-gram 计数，缓存到 output/<场次>/ngrams.json（录像和弹幕文件 2 天后会被移走，先算好）。"""
    f = sess_dir / "ngrams.json"
    if f.exists():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if d.get("version") == NGRAM_VERSION and (d.get("speech") or not sentences) and (d.get("danmaku") or not danmaku):
                return d
        except ValueError:
            pass
    inwin = lambda t: any(a <= t <= b for a, b in windows)
    speech = count_grams(s["text"] for s in sentences)
    hot = count_grams(s["text"] for s in sentences if inwin((s["t0"] + s["t1"]) / 2))
    dm = count_grams(t for _, t in danmaku)
    keep = lambda c, k: {g: n for g, n in c.items() if n >= k}
    d = {"version": NGRAM_VERSION, "speech": keep(speech, 2), "hot": keep(hot, 1) if hot else {},
         "danmaku": keep(dm, 3), "sentences": len(sentences), "danmaku_count": len(danmaku)}
    if sentences or danmaku:
        f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return d


def _boring(g: str) -> bool:
    if g in STOP or len(set(g)) == 1 or g.isdigit():
        return True
    if g[0] in EDGE or g[-1] in EDGE:
        return True
    if len(g) == 2 and re.fullmatch(r"[a-z0-9]{2}", g):
        return True
    return any(s in g for s in ("哈哈", "嘿嘿", "呜呜")) and len(g) <= 4


def mine_lore(per_session: dict[str, dict], known: set[str], rejected: set[str], titles: list[str],
              min_sessions: int = 2, limit: int = 30) -> list[dict]:
    """多场共同出现、且不是常用词的说法。返回 [{text, score, sessions, speech, danmaku, hot, in_titles}]。"""
    df_s, df_d, tot_s, tot_d, tot_h, last = Counter(), Counter(), Counter(), Counter(), Counter(), {}
    for name, d in sorted(per_session.items()):
        for g, n in (d.get("speech") or {}).items():
            df_s[g] += 1
            tot_s[g] += n
            last[g] = name
        for g, n in (d.get("danmaku") or {}).items():
            df_d[g] += 1
            tot_d[g] += n
            last[g] = max(last.get(g, ""), name)
        for g, n in (d.get("hot") or {}).items():
            tot_h[g] += n
    title_text = " ".join(titles).lower()
    cands = {}
    for g in set(df_s) | set(df_d):
        sess = max(df_s[g], df_d[g])
        if sess < min_sessions or _boring(g) or g in known or g in rejected:
            continue
        cands[g] = {"sessions": sess, "speech": tot_s[g], "danmaku": tot_d[g], "hot": tot_h[g],
                    "in_titles": g in title_text, "last_seen": last.get(g, "")}
    # 只留“最长的说法”：被更长的说法几乎完全包含的子串去掉（“律师函”留下，“师函”去掉）
    for g in sorted(cands, key=len):
        tot = cands[g]["speech"] + cands[g]["danmaku"]
        for h in cands:
            if len(h) > len(g) and g in h and (cands[h]["speech"] + cands[h]["danmaku"]) >= 0.6 * tot:
                cands[g]["dominated"] = True
                break
    out = []
    for g, c in cands.items():
        if c.get("dominated"):
            continue
        shared = c["speech"] > 0 and c["danmaku"] > 0              # 主播说、观众也刷：最像梗
        hot_ratio = c["hot"] / max(1, c["speech"])                   # 多出现在高峰片段里
        score = (math.log1p(c["speech"] + c["danmaku"]) * c["sessions"] * (1 + 0.15 * (len(g) - 2))
                 * (1 + 1.5 * shared + 2 * c["in_titles"] + min(hot_ratio, 1)))
        out.append({"text": g, "score": round(score, 2), **{k: v for k, v in c.items() if k != "dominated"}})
    out.sort(key=lambda x: -x["score"])
    return out[:limit]


# ---------------------------------------------------------------- 审核例子
def to_sec(s: str) -> float:
    parts = [float(x) for x in str(s).strip().split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def load_marks(reviews: Path) -> dict[str, dict[int, dict]]:
    """eval/reviews/<场次>.csv → {场次: {序号: {mark, note}}}（只要 Y / N）。"""
    out: dict[str, dict[int, dict]] = {}
    for p in sorted(reviews.glob("*.csv")) if reviews.exists() else []:
        with open(p, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                m = (r.get("是否有趣(Y/N)") or "").strip().upper()
                if m in ("Y", "N") and str(r.get("序号", "")).strip().isdigit():
                    out.setdefault(p.stem, {})[int(r["序号"])] = {
                        "mark": m, "note": (r.get("备注") or "").strip()[:200],
                        "clip_start": to_sec(r["片段起"]) if r.get("片段起") else None}
    return out


def build_examples(out_dir: Path, marks: dict, excluded: set[str], limit: int = 200) -> list[dict]:
    ex = []
    for sess, rows in sorted(marks.items(), reverse=True):
        d = out_dir / sess
        rr = _read(d / "rerank.json") or {}
        llm = {c["rank"]: c for c in rr.get("candidates", []) if c.get("llm")}
        tr = _read(d / "transcript.json") or {}
        cj = _read(d / "candidates.json") or {}
        cands = {c["rank"]: c for c in cj.get("candidates", [])}
        for rank, m in sorted(rows.items()):
            eid = f"{sess}#{rank}"
            c = cands.get(rank)
            if c and m.get("clip_start") is not None and abs(c["clip_start"] - m["clip_start"]) > 1:
                continue                                              # 标注对应的是旧片段
            r = llm.get(rank, {})
            o = r.get("llm") or {}
            speech = ""
            if c:
                speech = "".join(s["text"] for s in tr.get("sentences", [])
                                 if s["t1"] > c["clip_start"] and s["t0"] < c["clip_end"])[:120]
            ex.append({"id": eid, "session": sess, "rank": rank, "mark": m["mark"], "note": m["note"],
                       "what_happened": o.get("what_happened", ""), "type": o.get("type", ""),
                       "llm_score": o.get("score"), "speech": speech, "excluded": eid in excluded})
    return ex[:limit]


def pick_examples(examples: list[dict], session: str | None, n_yes: int, n_no: int) -> list[dict]:
    """给某一场精排挑例子：排除这一场本身、排除后台标记不用的；有备注的优先，越新越优先。"""
    pool = [e for e in examples if not e.get("excluded") and e["session"] != session and (e["note"] or e["what_happened"] or e["speech"])]
    key = lambda e: (bool(e["note"]), e["session"], -e["rank"])
    yes = sorted((e for e in pool if e["mark"] == "Y"), key=key, reverse=True)[:n_yes]
    no = sorted((e for e in pool if e["mark"] == "N"), key=key, reverse=True)[:n_no]
    return yes + no


# ---------------------------------------------------------------- 切片号数据
def type_stats(my_uploads: list[dict], min_n: int) -> dict:
    """已绑定场次的投稿按类型汇总播放和互动率。数量不够返回空。"""
    linked = [u for u in my_uploads if u.get("type") and u.get("view") is not None]
    if len(linked) < min_n:
        return {}
    allv = statistics.median(u["view"] for u in linked) or 1
    by = defaultdict(list)
    for u in linked:
        by[u["type"]].append(u)
    out = {}
    for t, us in by.items():
        med = statistics.median(u["view"] for u in us)
        rate = statistics.mean((u.get("like", 0) + u.get("coin", 0) + u.get("favorite", 0)) / max(1, u["view"]) for u in us)
        out[t] = {"n": len(us), "median_view": int(med), "relative": round(med / allv, 2), "engage": round(rate, 4)}
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["relative"]))


# ---------------------------------------------------------------- 排序权重
DEFAULT_WEIGHTS = {"llm": 0.8, "screen": 0.2, "not_funny": 0.6}


def final_score(llm: dict | None, rank: int, n_total: int, w: dict) -> float:
    screen = 1 - (rank - 1) / max(1, n_total - 1)
    if llm:
        f = w["llm"] * llm["score"] / 10 + w["screen"] * screen
        return f * (w.get("not_funny", 0.6) if not llm["funny"] else 1.0)
    return w["screen"] * screen * 0.5


def _hits(sessions: list[dict], w: dict, k: int) -> int:
    total = 0
    for s in sessions:
        order = sorted(s["rows"], key=lambda r: -final_score(r["llm"], r["rank"], s["n"], w))
        total += sum(1 for r in order[:k] if r["mark"] == "Y")
    return total


def calibrate(out_dir: Path, marks: dict, active: dict, min_sessions: int = 3, k: int = 10) -> dict:
    """在“精排 + 标注”都有的场次上搜权重。留一场交叉验证：用其余场次选的权重去排这一场，比现在的权重多命中才建议。"""
    sessions = []
    for sess, rows in marks.items():
        rr = _read(out_dir / sess / "rerank.json")
        cj = _read(out_dir / sess / "candidates.json")
        if not rr or not cj:
            continue
        llm = {c["rank"]: c.get("llm") for c in rr.get("candidates", [])}
        data = [{"rank": r, "mark": m["mark"], "llm": llm.get(r)} for r, m in rows.items() if r in llm]
        if sum(1 for d in data if d["mark"] == "Y") and len(data) >= 5:
            sessions.append({"name": sess, "n": len(cj.get("candidates", [])), "rows": data})
    rep = {"sessions": len(sessions), "needed": min_sessions, "k": k, "active": active, "proposed": None}
    if len(sessions) < min_sessions:
        rep["status"] = f"还差 {min_sessions - len(sessions)} 场“精排 + 标注”才会调权重"
        return rep
    grid = [{"llm": a, "screen": round(1 - a, 2), "not_funny": p}
            for a in (0.5, 0.6, 0.7, 0.8, 0.9, 1.0) for p in (0.4, 0.6, 0.8, 1.0)]
    base = _hits(sessions, active, k)
    loo = 0
    for i, s in enumerate(sessions):
        rest = sessions[:i] + sessions[i + 1:]
        best = max(grid, key=lambda w: (_hits(rest, w, k), w == active))
        loo += _hits([s], best, k)
    best_all = max(grid, key=lambda w: (_hits(sessions, w, k), w == active))
    rep.update(active_hits=base, loo_hits=loo, best_hits=_hits(sessions, best_all, k),
               total_yes=sum(sum(1 for r in s["rows"] if r["mark"] == "Y") for s in sessions))
    if loo > base and best_all != active:
        rep["proposed"] = best_all
        rep["status"] = f"建议换权重：前 {k} 名命中 {base} → 预计 {loo}（留一场验证）"
    else:
        rep["status"] = "现在的权重已经不差，不用换"
    return rep


# ---------------------------------------------------------------- 给提示词用
def context_text(mem: dict | None, session: str | None, cfg: dict, decisions: dict | None = None) -> tuple[str, str]:
    """(填进提示词的“记忆”文字, 记忆版本)。session = 正在精排的场次（它自己的例子不用）。
    decisions = 后台的决定：learn.py 还没重跑时，刚删掉的梗、刚排除的例子也立刻不用。"""
    dec = decisions or {}
    rejected = {str(x).lower() for x in dec.get("rejected_lore") or []}
    excluded = set(dec.get("excluded_examples") or [])
    if not mem:
        return "（还没有积累：运行 learn.py 或在后台点“更新精排记忆”）", ""
    mc = cfg.get("memory") or {}
    parts = []
    su = mem.get("streamer_uploads") or []
    if su:
        lines = [f"- 《{u['title']}》 播放 {u.get('view', 0)}" for u in su[: int(mc.get("streamer_titles", 10))]]
        parts.append("【她自己最近发的视频（看她觉得什么值得发、最近在聊什么、标题风格）】\n" + "\n".join(lines))
    lore = [x for x in mem.get("lore") or [] if x.get("status") != "rejected" and x["text"].lower() not in rejected]
    if lore:
        parts.append("【最近反复出现的说法 / 梗（从直播转写和弹幕里自动发现，可能有错字，仅供参考）】\n"
                     + "、".join(x["text"] for x in lore[: int(mc.get("lore_in_prompt", 25))]))
    ex = pick_examples([dict(e, excluded=e.get("excluded") or e["id"] in excluded) for e in mem.get("examples") or []], session, int(mc.get("examples_yes", 3)), int(mc.get("examples_no", 3)))
    if ex:
        lines = []
        for e in ex:
            desc = e["what_happened"] or e["speech"]
            lines.append(f"- {'值得切' if e['mark'] == 'Y' else '不值得切'}：{desc}" + (f"（审核备注：{e['note']}）" if e["note"] else ""))
        parts.append("【过去的人工审核结果（按这个口味判断）】\n" + "\n".join(lines))
    ts = mem.get("type_stats") or {}
    if ts:
        lines = [f"- {t}：{v['n']} 条，播放中位数 {v['median_view']}（是整体的 {v['relative']} 倍）" for t, v in ts.items()]
        parts.append("【切片号已发视频按类型的表现（越高说明观众越买账）】\n" + "\n".join(lines))
    top = mem.get("my_top_titles") or []
    if top:
        parts.append("【切片号播放最高的标题】\n" + "\n".join(f"- 《{t}》" for t in top[:5]))
    if not parts:
        return "（还没有积累）", mem.get("version", "")
    return "\n\n".join(parts), mem.get("version", "")


def load_memory(path: Path) -> dict | None:
    return _read(path)


def active_weights(cfg: dict, decisions: dict) -> dict:
    w = dict(DEFAULT_WEIGHTS)
    w.update((cfg.get("rerank") or {}).get("weights") or {})
    if isinstance(decisions.get("weights"), dict):
        w.update({k: float(v) for k, v in decisions["weights"].items() if k in DEFAULT_WEIGHTS})
    return w


def version_of(mem: dict) -> str:
    body = json.dumps({k: mem.get(k) for k in ("lore", "examples", "streamer_uploads", "type_stats", "my_top_titles")},
                      ensure_ascii=False, sort_keys=True)
    return "m-" + hashlib.sha1(body.encode()).hexdigest()[:8]


def _read(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except (OSError, ValueError):
        return None
