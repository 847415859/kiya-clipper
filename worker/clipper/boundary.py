"""片段边界修正（技术方案“模块二 第三步”）：起止对齐到句子，保留铺垫和余韵，时长限制在 30 秒到 3 分钟。

输入是整场时间（秒）。大模型给的起止点 / 笑点时间常偏几秒，这里用逐字稿的句子时间戳二次对齐。
"""
from __future__ import annotations


def _sentence_at(sents: list[dict], t: float) -> int | None:
    for i, s in enumerate(sents):
        if s["t0"] <= t < s["t1"]:
            return i
    return None


def refine_bounds(sentences: list[dict], peak: float, punch: float | None = None,
                  start: float | None = None, end: float | None = None,
                  min_len: float = 30.0, max_len: float = 180.0,
                  lead: float = 0.3, tail: float = 1.5, setup: float = 8.0) -> dict:
    """返回 {"start", "end", "note"}（整场秒数）。

    - 起点：大模型给的起点（没有就是 笑点 − 20 秒），落在某句中间就提前到那句开头；至少比笑点早 setup 秒（留铺垫）
    - 终点：大模型给的终点（没有就是 笑点 + 12 秒），落在某句中间就延后到那句结尾，再加 tail 秒余韵
    - 太短：前后按整句扩展直到 ≥ min_len；太长：从终点往回收到 ≤ max_len 的最后一个句末
    """
    sents = sorted((s for s in sentences if s.get("t1", 0) > s.get("t0", 0)), key=lambda s: s["t0"])
    p = punch if punch is not None else peak
    s0 = start if start is not None else p - 20
    e0 = end if end is not None else p + 12
    notes = []
    s0 = min(s0, p - setup)
    if e0 <= p:
        e0 = p + 3

    i = _sentence_at(sents, s0)
    if i is not None:
        s0 = sents[i]["t0"] - lead
        notes.append("起点对齐句首")
    j = _sentence_at(sents, e0)
    if j is not None:
        e0 = sents[j]["t1"]
        notes.append("终点对齐句末")
    e0 += tail

    # 太短：交替向前、向后各扩一句
    before = [s for s in sents if s["t1"] <= s0]
    after = [s for s in sents if s["t0"] >= e0 - tail]
    while e0 - s0 < min_len and (before or after):
        if after:
            e0 = after.pop(0)["t1"] + tail
        if e0 - s0 < min_len and before:
            s0 = before.pop()["t0"] - lead
    if e0 - s0 < min_len:                       # 没有句子可扩（安静的片段）：直接补足
        pad = (min_len - (e0 - s0)) / 2
        s0, e0 = s0 - pad, e0 + pad
        notes.append("补足最短时长")

    # 太长：终点收回到 start + max_len 之前的最后一个句末
    if e0 - s0 > max_len:
        limit = s0 + max_len - tail
        ends = [s["t1"] for s in sents if s0 < s["t1"] <= limit and s["t1"] > p]
        e0 = (max(ends) if ends else limit) + tail
        notes.append("截到最长时长")

    if s0 < 0:
        e0 -= s0
        s0 = 0.0
    return {"start": round(s0, 2), "end": round(e0, 2), "note": "，".join(notes) or "未调整"}
