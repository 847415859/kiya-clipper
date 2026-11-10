#!/usr/bin/env python3
"""转写抽测：把一段时间内的识别结果和你手打的标准文本比，算字错率（CER），并列出错得最多的地方。

用法：
  1. 在标注页 / 播放器里听一段（建议 10 分钟），把主播说的话逐字打到 ref.txt（标点、空格随意，会被忽略）
  2. python scripts/asr_eval.py output/20261109-2014/transcript.json --start 01:40:00 --end 01:50:00 --ref ref.txt

字错率 = (替换 + 删除 + 插入) / 标准文本字数。经验上 10% 以内够用；
高于 20% 先加热词（hotwords.py），再考虑换模型。错得最多的词可以直接加进热词表或字幕纠错词。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

PUNCT = re.compile(r"[\s，。！？、；：,.!?;:“”\"'‘’（）()…—\-~～《》<>【】\[\]]+")


def to_sec(s: str) -> float:
    if re.fullmatch(r"[\d.]+", s):
        return float(s)
    parts = [float(x) for x in s.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def norm(text: str) -> str:
    return PUNCT.sub("", text).lower()


def align(ref: str, hyp: str) -> list[tuple[str, str, str]]:
    """字级编辑距离对齐，返回 [(操作, 标准字, 识别字)]，操作：= 相同、S 替换、D 删除、I 插入。"""
    n, m = len(ref), len(hyp)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]))
    ops, i, j = [], n, m
    while i or j:
        if i and j and d[i][j] == d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]):
            ops.append(("=" if ref[i - 1] == hyp[j - 1] else "S", ref[i - 1], hyp[j - 1]))
            i, j = i - 1, j - 1
        elif i and d[i][j] == d[i - 1][j] + 1:
            ops.append(("D", ref[i - 1], ""))
            i -= 1
        else:
            ops.append(("I", "", hyp[j - 1]))
            j -= 1
    return ops[::-1]


def error_spans(ops) -> Counter:
    """连续出错的片段：“标准 → 识别”计数。"""
    c, r, h = Counter(), "", ""
    for op, a, b in ops + [("=", "", "")]:
        if op == "=":
            if r or h:
                c[f"{r or '∅'} → {h or '∅'}"] += 1
            r, h = "", ""
        else:
            r += a
            h += b
    return c


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("transcript", help="transcript.json")
    ap.add_argument("--start", required=True, help="整场时间，如 01:40:00 或秒数")
    ap.add_argument("--end", required=True)
    ap.add_argument("--ref", required=True, help="手打的标准文本文件")
    args = ap.parse_args()
    tr = json.loads(Path(args.transcript).read_text(encoding="utf-8"))
    a, b = to_sec(args.start), to_sec(args.end)
    hyp = norm("".join(s["text"] for s in tr["sentences"] if s["t0"] >= a - 0.5 and s["t1"] <= b + 0.5))
    ref = norm(Path(args.ref).read_text(encoding="utf-8"))
    if not ref:
        print("标准文本是空的", file=sys.stderr)
        return 1
    if len(ref) * max(1, len(hyp)) > 40_000_000:
        print("文本太长（超过约 6000 字），请分段抽测", file=sys.stderr)
        return 1
    ops = align(ref, hyp)
    s = sum(op == "S" for op, *_ in ops)
    dl = sum(op == "D" for op, *_ in ops)
    ins = sum(op == "I" for op, *_ in ops)
    cer = (s + dl + ins) / len(ref)
    print(f"模型 {tr.get('model', '?')}，{(b - a) / 60:.1f} 分钟；标准 {len(ref)} 字，识别 {len(hyp)} 字")
    print(f"字错率 CER = {cer:.1%}（替换 {s}、漏字 {dl}、多字 {ins}）")
    print("错得最多的地方（标准 → 识别）：")
    for k, v in error_spans(ops).most_common(15):
        print(f"  {v:>3} 次  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
