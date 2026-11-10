#!/usr/bin/env python3
"""剪辑脚本初稿（综艺版）：大模型按精排结论和原话，给候选片段编排花字 / 放大运镜 / 贴图，存成剪辑脚本，
在后台成片编辑器里打开就是初稿，微调后渲染。

用法：
  python scripts/autoedit.py output/20261109-2014/candidates.json --rank 3
  python scripts/autoedit.py output/20261109-2014/candidates.json --rank 3 --force    # 覆盖已有的剪辑脚本

需要先跑过 rerank.py（用它的笑点时间和建议起止）；没跑过也能用，只是没有笑点提示。
生成后会按 clipper/edit.py 校验，并强制密度约束（同一时刻最多 2 个效果、两次推镜至少隔 10 秒、最多 max_items 个），
不合规的效果直接丢掉，不会让整份脚本失败。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from clipper.asr import load_transcript  # noqa: E402
from clipper.context import danmaku_lines, load_events, profile, speech_lines  # noqa: E402
from clipper.edit import EditError, enforce_density, suggest_window, validate_edit, validate_item  # noqa: E402
from clipper.llm import LLMClient, fill, load_prompt  # noqa: E402

COLORS = "#FFE14D|#FFFFFF|#FF7EB6|#7FE3FF"


class Trim(BaseModel):
    start: float
    end: float


class EditOut(BaseModel):
    trim: Trim | None = None
    items: list[dict] = Field(default_factory=list)
    note: str = ""


def sticker_names() -> list[str]:
    d = Path(os.environ.get("KIYA_STICKERS") or REPO / "assets" / "stickers")
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("candidates")
    ap.add_argument("--rank", type=int, required=True)
    ap.add_argument("--force", action="store_true", help="覆盖已有的剪辑脚本")
    ap.add_argument("--config", default=str(REPO / "config" / "kiya.yaml"))
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    ac = {"prompt": "edit_v1", "model": "", "max_items": 8, **(cfg.get("autoedit") or {})}
    cpath = Path(args.candidates)
    sess = cpath.parent
    data = json.loads(cpath.read_text(encoding="utf-8"))
    c = next((x for x in data["candidates"] if x["rank"] == args.rank), None)
    if not c:
        print(f"没有序号 {args.rank} 的候选", file=sys.stderr)
        return 1
    dst = sess / "edits" / f"{args.rank:02d}.json"
    if dst.exists() and not args.force:
        print(f"{dst} 已存在（手动编辑过），不覆盖；要重新生成加 --force", file=sys.stderr)
        return 1
    ca = c["clip_start"]
    tr = load_transcript(sess / "transcript.json")
    sents = tr["sentences"] if tr else []
    rr = None
    rpath = sess / "rerank.json"
    if rpath.exists():
        rr = next((r for r in json.loads(rpath.read_text(encoding="utf-8"))["candidates"] if r["rank"] == args.rank), None)
    llm = (rr or {}).get("llm") or {}
    refined = (rr or {}).get("refined")
    window = suggest_window(c, refined)              # 素材范围：候选片段 ∪ 精排修正后的起止
    a, b = window["start"], window["end"]
    clip_len = b - a
    t_start = max(0.0, refined["start"] - a) if refined else ca - a
    t_end = min(clip_len, refined["end"] - a) if refined else c["clip_end"] - a
    if t_end - t_start < 5:
        t_start, t_end = 0.0, clip_len
    story = llm.get("story") or {}
    stickers = sticker_names()
    dm, _ = danmaku_lines(load_events(data["segments"]), a, b, a, float(data.get("delay_seconds") or 0), limit=40)
    version, system, user = load_prompt(ac["prompt"])
    system = fill(system, max_items=ac["max_items"])
    text = fill(user, profile=profile(cfg), window=f"{clip_len:.0f}", trim_start=f"{t_start:.1f}", trim_end=f"{t_end:.1f}",
                what_happened=llm.get("what_happened") or "（没有精排结论）",
                punch=(f"{ca + llm['punch_time'] - a:.1f}" if llm.get("punch_time", -1) >= 0 else "（未知）"),
                setup=story.get("setup") or "—", punchline=story.get("punch") or "—", reaction=story.get("reaction") or "—",
                speech=speech_lines(sents, a, b, a) if sents else "（这场还没有转写）",
                danmaku=dm, stickers="\n".join(stickers) or "（贴图库是空的，不要用贴图）", colors=COLORS)
    client = LLMClient(model=ac["model"] or None)
    print(f"#{args.rank} 生成剪辑脚本初稿（{client.model}，{version}）…")
    r = client.chat_json([{"role": "system", "content": system}, {"role": "user", "content": text}], EditOut, tag=version)
    if r.data is None:
        print(f"生成失败：{r.error}", file=sys.stderr)
        return 1
    out = r.data
    items, bad = [], []
    for it in out.items:
        try:
            v = validate_item(it, clip_len)
            if v["type"] == "sticker" and v["asset"] not in stickers:
                bad.append(f"贴图 {v['asset']} 不在贴图库")
                continue
            items.append(v)
        except EditError as e:
            bad.append(str(e))
    trim = {"start": t_start, "end": t_end}
    if out.trim and 0 <= out.trim.start < out.trim.end <= clip_len and out.trim.end - out.trim.start >= 5:
        trim = {"start": round(out.trim.start, 2), "end": round(out.trim.end, 2)}
    items = [i for i in items if trim["start"] <= i["start"] < trim["end"]]
    edit = {"version": 1, "window": window, "trim": trim, "subtitles": {"enabled": True}, "items": items,
            "generated_by": f"{version} / {client.model}", "note": out.note[:200]}
    edit, dropped = enforce_density(edit)
    if len(edit["items"]) > int(ac["max_items"]):
        dropped += edit["items"][int(ac["max_items"]):]
        edit["items"] = edit["items"][:int(ac["max_items"])]
    try:
        edit = validate_edit(edit, clip_len)
    except EditError as e:
        print(f"生成的脚本不合法：{e}", file=sys.stderr)
        return 1
    edit.update(session=sess.name, rank=args.rank, updated_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    dst.parent.mkdir(exist_ok=True)
    dst.write_text(json.dumps(edit, ensure_ascii=False, indent=1), encoding="utf-8")
    kinds = {}
    for i in edit["items"]:
        kinds[i["type"]] = kinds.get(i["type"], 0) + 1
    print(f"初稿：成片 {trim['start']:.1f}–{trim['end']:.1f} 秒，效果 {len(edit['items'])} 个 "
          f"（花字 {kinds.get('text', 0)}、运镜 {kinds.get('zoom', 0)}、贴图 {kinds.get('sticker', 0)}）"
          + (f"；丢掉 {len(dropped) + len(bad)} 个不合规的" if dropped or bad else ""))
    for x in bad[:5]:
        print("  - " + x)
    print(f"思路：{edit.get('note') or '—'}")
    print(f"已保存 {dst}，在后台成片编辑器里打开即可微调、渲染")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
