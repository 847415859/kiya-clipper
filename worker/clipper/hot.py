"""B站热门里的“梗”：每天抓热门视频、每周必看、热搜的标题 / 词条，提炼高频短语，存成当天快照。

只取文字（标题和热搜词），不下载、不转载任何视频内容；每天最多 4 次请求，间隔 1 秒（见 clipper/bili.py）。
快照在 output/_memory/hot/YYYY-MM-DD.json，只保留短语和出现次数（不存完整标题），默认留 14 天。
梗库（clipper/memes.py）用快照算“近 7 天热度”：热度跌破阈值的梗自动降权，不会一直推过气的梗。
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, timedelta
from pathlib import Path

from .bili import Bili, BiliError
from .memory import _boring, grams

BRACKET = re.compile(r"[【\[（(《「][^】\]）)》」]*[】\]）)》」]")
HOT_STOP = set("""视频 合集 全程 高能 教程 攻略 日常 现场 完整版 最新 一个 这个 我们 为什么 怎么 全网 第一 官方 正式 首发 预告
实测 测评 开箱 回顾 盘点 名场面 全集 解说 剪辑 搬运 转载 直播 回放 更新 联动 现场版 完整 剧情 周边 下集 上集""".split())
KEEP_DAYS = 14


def clean_title(t: str) -> str:
    return re.sub(r"\s+", " ", BRACKET.sub(" ", t)).strip()


def title_phrases(titles: list[str], min_titles: int = 3) -> dict[str, int]:
    """多个标题里共同出现的短语 → {短语: 出现在几个标题里}。被更长短语几乎包含的短的去掉。"""
    from collections import Counter
    cnt: Counter = Counter()
    for t in titles:
        cnt.update(grams(clean_title(t)))
    cand = {}
    for g, n in cnt.items():
        need = min_titles if len(g) >= 3 else min_titles * 2
        if n < need or _boring(g) or g in HOT_STOP or any(w in g for w in HOT_STOP if len(w) >= 3):
            continue
        cand[g] = n
    out = {}
    for g in sorted(cand, key=len):
        if any(len(h) > len(g) and g in h and cand[h] >= 0.6 * cand[g] for h in cand):
            continue
        out[g] = cand[g]
    return out


def clean_hotword(w: str) -> str | None:
    w = BRACKET.sub("", w).strip()
    if not 2 <= len(w) <= 14 or w.isdigit() or (_boring(w) and len(w) < 3):
        return None
    return w


def crawl(bili: Bili, min_titles: int = 3, log=print) -> dict:
    """抓一次，返回快照（不写盘）。某个来源失败只记下来，全失败才算失败。"""
    phrases: dict[str, dict] = {}
    errors, sources = [], {}

    def add(text: str, n: int, src: str):
        e = phrases.setdefault(text, {"count": 0, "sources": []})
        e["count"] += n
        if src not in e["sources"]:
            e["sources"].append(src)

    titles: list[str] = []
    for name, fn in (("popular", bili.popular), ("weekly", bili.weekly_must_watch)):
        try:
            got = fn()
            sources[name] = len(got)
            titles += got
        except BiliError as e:
            errors.append(f"{name}：{e}")
            log(f"  热门来源 {name} 失败：{e}")
    for g, n in title_phrases(titles, min_titles).items():
        add(g, n, "热门标题")
    try:
        hw = [x for x in (clean_hotword(w) for w in bili.hot_search()) if x]
        sources["hot_search"] = len(hw)
        for w in hw:
            add(w, 3, "热搜")                      # 热搜词本身就是短语，给个固定权重
    except BiliError as e:
        errors.append(f"hot_search：{e}")
        log(f"  热搜失败：{e}")
    if not sources:
        raise BiliError("三个来源都失败了：" + "；".join(errors))
    return {"date": date.today().isoformat(), "fetched_at": time.strftime("%F %T"), "sources": sources,
            "titles": len(titles), "errors": errors, "phrases": phrases}


def save_snapshot(hot_dir: Path, snap: dict, keep_days: int = KEEP_DAYS) -> Path:
    hot_dir.mkdir(parents=True, exist_ok=True)
    f = hot_dir / f"{snap['date']}.json"
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    tmp.replace(f)
    cutoff = date.fromisoformat(snap["date"]) - timedelta(days=keep_days)
    for p in hot_dir.glob("*.json"):
        try:
            if date.fromisoformat(p.stem) < cutoff:
                p.unlink()
        except ValueError:
            pass
    return f


def load_history(hot_dir: Path) -> dict[str, dict]:
    """{日期: {短语: 次数}}，按日期升序。"""
    out = {}
    for p in sorted(hot_dir.glob("*.json")) if hot_dir.exists() else []:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out[p.stem] = {k: int(v.get("count", 0)) for k, v in (d.get("phrases") or {}).items()}
        except (OSError, ValueError):
            continue
    return out


def heat(text: str, history: dict[str, dict], today: date, window: int = 7, decay: float = 0.8) -> tuple[float, int]:
    """近 window 天的加权日均出现次数（越近权重越大，只算有快照的日子），以及有几天出现过。"""
    num = den = 0.0
    seen = 0
    for d, ph in history.items():
        age = (today - date.fromisoformat(d)).days
        if 0 <= age < window:
            w = decay ** age
            num += w * ph.get(text, 0)
            den += w
            seen += 1 if ph.get(text, 0) else 0
    return (round(num / den, 3) if den else 0.0), seen
