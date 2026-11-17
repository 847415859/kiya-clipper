"""片段边界修正（技术方案“模块二 第三步”）：起止对齐到句子，保留铺垫和余韵，时长限制在 30 秒到 3 分钟。

输入是整场时间（秒）。大模型给的起止点 / 笑点时间常偏几秒，这里用逐字稿的句子时间戳二次对齐。
"""
from __future__ import annotations

from math import isfinite

from .boiler import business_cuts


def _sentence_at(sents: list[dict], t: float) -> int | None:
    for i, s in enumerate(sents):
        if s["t0"] <= t < s["t1"]:
            return i
    return None


def refine_bounds(sentences: list[dict], peak: float, punch: float | None = None,
                  start: float | None = None, end: float | None = None,
                  min_len: float = 30.0, max_len: float = 180.0,
                  lead: float = 0.3, tail: float = 1.5, setup: float = 8.0, material_end: float | None = None) -> dict:
    """返回 {"start", "end", "note"}（整场秒数）。

    - 起点：有有效的大模型起点时优先使用，仅做句首对齐；没有起点才用笑点前 setup 秒的默认前留。
    - 终点：大模型给的终点（没有就是 笑点 + 12 秒），落在某句中间就延后到那句结尾，再加 tail 秒余韵。
    - 太短：仅对缺少明确起点的候选按整句扩展；明确短内容不因统一下限被强行补长。太长仍收回到 ≤ max_len。
    """
    def valid(v):
        return type(v) in (int, float) and isfinite(v)
    if not valid(peak) or not valid(min_len) or not valid(max_len) or not 0 < max_len or min_len < 0:
        raise ValueError("非法时间或时长限制")
    original = {k: v if v is None or valid(v) else str(v)
                for k, v in {"start": start, "end": end, "peak": peak, "punch": punch}.items()}
    start = start if valid(start) else None
    end = end if valid(end) else None
    punch = punch if valid(punch) else None
    if material_end is not None and (not valid(material_end) or material_end <= 0):
        raise ValueError("素材时长必须为正有限数")
    peak = max(0.0, min(peak, material_end) if material_end else peak)
    if start is not None and end is not None and start >= end:
        start, end = None, None
    if material_end is not None:
        start = min(max(0, start), max(0, material_end - .1)) if start is not None else None
        end = min(end, material_end) if end is not None else None
    sentences = [s for s in sentences if valid(s.get("t0")) and valid(s.get("t1"))]
    sents = sorted((s for s in sentences if s.get("t1", 0) > s.get("t0", 0)), key=lambda s: s["t0"])
    p = max(0.0, punch if punch is not None else peak)
    if material_end is not None:
        p = min(p, material_end)
    s0 = start if start is not None else p - 20
    e0 = end if end is not None else p + 12
    notes = []
    explicit_start = start is not None
    if not explicit_start:
        s0 = p - setup
        notes.append("按默认前留")
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
    while not explicit_start and e0 - s0 < min_len and (before or after):
        if after:
            e0 = after.pop(0)["t1"] + tail
        if e0 - s0 < min_len and before:
            s0 = before.pop()["t0"] - lead
    if not explicit_start and e0 - s0 < min_len:  # 没有句子可扩时才补足默认候选
        pad = (min_len - (e0 - s0)) / 2
        s0, e0 = s0 - pad, e0 + pad
        notes.append("补足最短时长")

    # 太长：终点收回到 start + max_len 之前的最后一个句末
    if e0 - s0 > max_len:
        limit = s0 + max_len - tail
        ends = [s["t1"] for s in sents if s0 < s["t1"] <= limit and s["t1"] > p]
        e0 = (max(ends) if ends else limit) + tail
        notes.append("截到最长时长")

    s0 = max(0.0, s0)
    if material_end is not None:
        e0 = min(e0, material_end)
        s0 = min(s0, max(0, e0 - .1))
    if e0 <= s0:
        e0 = s0 + min(max_len, .1)
    if original["start"] != start or original["end"] != end:
        notes.append("无效或越界时间已修正")
    removed = business_cuts(sents, s0, e0)
    if removed:
        notes.append(f"移除{len(removed)}段营业话")
    return {"start": round(s0, 2), "end": round(e0, 2), "removed": removed,
            "note": "，".join(notes) or "未调整", "before": original,
            "after": {"start": round(s0, 2), "end": round(e0, 2)}}
