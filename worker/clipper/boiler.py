"""识别并移除片段里的纯营业话。

这里只处理可以安全判定为“没有内容”的欢迎、谢礼和念弹幕句。
含有具体事件、观点或上下文的句子不会命中。调用方可以把命中的句子作为
需要从视频中删除的时间段；片段中间的营业话也因此可以被拼接掉。
"""
from __future__ import annotations

import re


NORM_RE = re.compile(r"[^0-9a-z一-鿿]+", re.I)
TRIM_RE = re.compile(r"^[，。！？!?、：:；;（）()\[\]【】\"'“”‘’…~～\s]+|[，。！？!?、：:；;（）()\[\]【】\"'“”‘’…~～\s]+$")

WELCOME_RE = re.compile(
    r"^(?:(?:大家|各位|朋友们|宝宝们|家人们|米娜桑|观众老爷们)?"
    r"(?:晚上好|早上好|中午好|下午好|晚安|大家好|各位好|好哇|好呀|好)"
    r"|(?:晚上好|早上好|中午好|下午好|晚安)(?:大家|各位|朋友们|宝宝们|家人们|米娜桑)?"
    r"|欢迎(?:回来|大家|各位|朋友们|宝宝们|家人们))"
    r"(?:呀|啊|哦|呢|喵|啦|喽)?$")
THANK_RE = re.compile(r"(?:谢谢|感谢|多谢)")
SERVICE_RE = re.compile(r"(?:礼物|小花花|舰长|提督|总督|上舰|醒目留言|SC|打赏|投喂|三连|点赞|关注|支持)", re.I)
READ_RE = re.compile(r"(?:弹幕|留言|醒目留言|SC)")
READ_VERB_RE = re.compile(r"(?:念一下|读一下|看一下|看看|读读|念念|我看|我念|我读)")


def normalized(text: str) -> str:
    return NORM_RE.sub("", (text or "").lower())


def is_business_speech(text: str) -> bool:
    """判断一句是否是可删除的纯营业话。

    采用保守规则：句子过长、同时包含具体叙事连接词，或只有“谢谢”而没有
    礼物/互动对象时都不删除，避免把有内容的感谢句误剪掉。
    """
    raw = TRIM_RE.sub("", str(text or "").strip())
    n = normalized(raw)
    if not n or len(n) > 36:
        return False
    if WELCOME_RE.fullmatch(n):
        return True
    if THANK_RE.search(n) and SERVICE_RE.search(n):
        # “谢谢你提醒我……”这类有内容的句子保留；纯谢礼通常没有叙事连接词。
        if re.search(r"(?:但是|不过|因为|所以|提醒|告诉|觉得|喜欢|今天|这个问题|这件事)", n):
            return False
        return True
    if READ_RE.search(n) and READ_VERB_RE.search(n):
        return True
    return False


def business_cuts(sentences: list[dict], start: float, end: float, min_seconds: float = 0.25) -> list[dict]:
    """返回片段范围内应删除的营业话时间段，合并相邻句子。"""
    hits = []
    for s in sentences or []:
        try:
            a, b = float(s["t0"]), float(s["t1"])
        except (KeyError, TypeError, ValueError):
            continue
        if b <= start or a >= end or b <= a or b - a < min_seconds:
            continue
        text = str(s.get("text") or "").strip()
        if not is_business_speech(text):
            continue
        hits.append({"start": max(start, a), "end": min(end, b), "text": text, "reason": "营业话"})
    hits.sort(key=lambda x: (x["start"], x["end"]))
    merged = []
    for hit in hits:
        if merged and hit["start"] <= merged[-1]["end"] + 0.3:
            merged[-1]["end"] = max(merged[-1]["end"], hit["end"])
            merged[-1]["text"] += " / " + hit["text"]
        else:
            merged.append(hit)
    return [{**x, "start": round(x["start"], 2), "end": round(x["end"], 2)} for x in merged]


def keep_ranges(start: float, end: float, removed: list[dict] | None) -> list[tuple[float, float]]:
    """把 [start, end) 减去 removed，返回待拼接的连续区间。"""
    cur = float(start)
    out = []
    for item in sorted(removed or [], key=lambda x: float(x.get("start", 0))):
        a, b = max(start, float(item["start"])), min(end, float(item["end"]))
        if b <= a or a >= end:
            continue
        if a > cur:
            out.append((cur, a))
        cur = max(cur, b)
    if cur < end:
        out.append((cur, end))
    return [(a, b) for a, b in out if b - a > 0.05]


def compact_time(t: float, removed: list[dict] | None) -> float:
    """把原始整场时间映射到删除营业话后的压缩时间轴。"""
    return float(t) - sum(max(0.0, min(float(t), float(q["end"])) - float(q["start"]))
                         for q in (removed or []) if float(t) > float(q["start"]))
