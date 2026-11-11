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
import re
import sys
import time
from pathlib import Path

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from clipper.kcfg import load_cfg  # noqa: E402
from clipper.streamer import resolve_config  # noqa: E402
from clipper.asr import load_transcript  # noqa: E402
from clipper.context import danmaku_lines, load_events, profile, speech_lines  # noqa: E402
from clipper.stickers import offer_text as sticker_offer_text, offered as offered_stickers, sticker_dir, undescribed  # noqa: E402
from clipper.edit import EditError, enforce_density, ensure_caption, limit_stickers, suggest_window, validate_edit, validate_item  # noqa: E402
from clipper.editdiff import edit_taste, taste_text  # noqa: E402
from clipper.llm import LLMClient, fill, load_prompt  # noqa: E402
from clipper.variants import DEFAULT, STYLES, load as variants_load, resolve, save as save_variants, variants_cfg, wants_variants  # noqa: E402
from clipper.memes import (enforce_memes, load_library, offer_text, retrieve, settings as meme_settings,  # noqa: E402
                           situation_text)

COLORS = "#FFE14D|#FFFFFF|#FF7EB6|#7FE3FF"


class Trim(BaseModel):
    start: float
    end: float


class EditOut(BaseModel):
    trim: Trim | None = None
    items: list[dict] = Field(default_factory=list)
    note: str = ""


def sticker_names() -> list[str]:
    """能给 AI 用的贴图文件名：只有写了含义说明的才算（clipper/stickers.py）。"""
    return [s["name"] for s in offered_stickers(sticker_dir())]


def shift_beats(beats: list[str], delta: float) -> str:
    """精排的节拍是“12s 起因：…”（相对候选片段开头）→ 换成相对素材范围开头，和下面的原话时间对得上。"""
    out = []
    for b in beats:
        m = re.match(r"\s*(-?\d+(?:\.\d+)?)\s*s?\s*(.*)", str(b), re.S)
        out.append(f"[{float(m.group(1)) + delta:.0f}s] {m.group(2).strip()}" if m else str(b))
    return "\n".join(f"- {x}" for x in out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("candidates")
    ap.add_argument("--rank", type=int, required=True)
    ap.add_argument("--force", action="store_true", help="覆盖已有的剪辑脚本")
    ap.add_argument("--styles", help="指定要生成哪几版（逗号分隔：restrained,standard,lively）；指定后不受前 top_n 个的限制。默认 --variants auto 的三版")
    ap.add_argument("--variants", choices=["off", "auto"], default="off",
                    help="auto：AI 评分靠前的前 autoedit.variants.top_n 个候选按克制 / 标准 / 热闹各出一版（其余只出标准版）；off：只出标准版")
    ap.add_argument("--config", default=None, help="主播配置；不填时按 candidates.json 所在目录推导主播")
    args = ap.parse_args()

    cpath = Path(args.candidates)
    sess = cpath.parent
    cfg = load_cfg(resolve_config(args.config, sess))
    ac = {"prompt": "edit_v4", "model": "", "max_items": 8, **(cfg.get("autoedit") or {})}
    mc = cfg.get("memory") or {}
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
    sdir = sticker_dir()
    offered_st = offered_stickers(sdir)
    stickers = [s["name"] for s in offered_st]
    if undescribed(sdir):
        print(f"提示：贴图库里有 {len(undescribed(sdir))} 个贴图没有含义说明，AI 不会用它们（AI 只看得到文件名）；"
              "运行 python scripts/describe_stickers.py 自动补，或手写 assets/stickers/stickers.json")
    caption = (llm.get("context_caption") or "").strip()
    handoff = dict(caption=caption or "（不需要）", punch_line=llm.get("punch_line") or "（无）",
                   hook_note=llm.get("hook_note") or "（不需要）", pace=llm.get("pace") or "（未知，按精排结论自己判断）",
                   highlight_danmaku=" ｜ ".join(llm.get("highlight_danmaku") or []) or "（没有）",
                   types="、".join(llm.get("types") or [llm.get("type") or ""]).strip("、") or "（未知）",
                   zoom_times="、".join(f"{ca + float(x) - a:.0f}" for x in llm.get("highlight_times") or []) or "（没有）",
                   edit_hints=shift_beats(llm.get("edit_hints") or [], ca - a) or "（没有）",
                   beats=shift_beats(llm.get("beats") or [], ca - a) or "（没有节拍，按精排结论自己判断）")
    dm, _ = danmaku_lines(load_events(data["segments"]), a, b, a, float(data.get("delay_seconds") or 0), limit=40)
    version, system_t, user = load_prompt(ac["prompt"])
    ms = meme_settings(cfg)
    # 剪辑师口味：人改过 AI 初稿的记录攒够了才生效（clipper/editdiff.py）；提示词用 edit_v2 才有这一块
    taste = edit_taste(sess.parent, int(mc.get("min_edits", 5)), int(mc.get("min_edit_sessions", 2)))
    extra = {"taste": taste_text(taste) or "（还没有积累）"} if "{{taste}}" in user else {}
    # 梗库：用精排结论 + 弹幕当情境去检索，只给大模型“已确认、相符”的几个；提示词用 edit_v3 才有这一块（几个版本共用这一份结果）
    offered, meme_method = [], "none"
    if "{{memes}}" in user:
        lib = load_library(Path(os.environ.get("KIYA_MEMES") or sess.parent / "_memory" / "memes.json"))
        offered, meme_method = retrieve(situation_text(llm, extra=dm), lib, cfg, taboo=cfg.get("taboo") or [])
        extra["memes"] = offer_text(offered)
    # 多版本：前 top_n 个候选按风格档位各出一版（提示词要有 {{variant}}），其余只出标准版
    vc = variants_cfg(ac)
    if args.styles:                                   # 人在编辑器里勾选的：想要几版就几版，不看排名
        picked = list(dict.fromkeys(x.strip() for x in args.styles.split(",") if x.strip()))
        bad_keys = [x for x in picked if x not in STYLES]
        if bad_keys or not picked:
            print(f"不认识的版本：{'、'.join(bad_keys) or '（空）'}；可选 {', '.join(STYLES)}", file=sys.stderr)
            return 1
        if "{{variant}}" not in user:
            print("当前提示词不支持多版本（需要 edit_v4 及以上）", file=sys.stderr)
            return 1
        multi, why, styles = len(picked) > 1, "", picked
    else:
        multi, why = (wants_variants(args.variants, (rr or {}).get("new_rank"), args.rank, int(vc["top_n"]))
                      if "{{variant}}" in user else (False, ""))
        styles = vc["styles"] if multi else [DEFAULT]
    client = LLMClient(model=ac["model"] or None, cache_dir=sess.parent / "_llm_cache")

    def make(key: str):
        """某个档位的一版初稿：(edit, bad, dropped, meme_notes)，大模型失败返回 None。"""
        v = resolve(key, int(ac["max_items"]), int(ms["max_per_clip"]))
        system = fill(system_t, max_items=v["max_items"], **({"max_memes": str(v["max_memes"]), "meme_gap": f"{float(ms['min_gap']):.0f}"}
                                                            if "{{max_memes}}" in system_t else {}))
        vx = {"variant": v["brief"]} if "{{variant}}" in user else {}
        text = fill(user, profile=profile(cfg), **extra, **vx, **handoff, window=f"{clip_len:.0f}", trim_start=f"{t_start:.1f}", trim_end=f"{t_end:.1f}",
                    what_happened=llm.get("what_happened") or "（没有精排结论）",
                    punch=(f"{ca + llm['punch_time'] - a:.1f}" if llm.get("punch_time", -1) >= 0 else "（未知）"),
                    setup=story.get("setup") or "—", punchline=story.get("punch") or "—", reaction=story.get("reaction") or "—",
                    speech=speech_lines(sents, a, b, a) if sents else "（这场还没有转写）",
                    danmaku=dm, stickers=sticker_offer_text(offered_st), colors=COLORS)
        print(f"#{args.rank} 生成剪辑脚本初稿{'（' + v['label'] + '版）' if multi or key != DEFAULT else ''}（{client.model}，{version}）…")
        r = client.chat_json([{"role": "system", "content": system}, {"role": "user", "content": text}], EditOut, tag=version)
        if r.data is None:
            print(f"生成失败：{r.error}", file=sys.stderr)
            return None
        out = r.data
        items, bad = [], []
        for it in out.items:
            try:
                x = validate_item(it, clip_len)
                if x["type"] == "sticker" and x["asset"] not in stickers:
                    bad.append(f"贴图 {x['asset']} 不在贴图库")
                    continue
                items.append(x)
            except EditError as e:
                bad.append(str(e))
        trim = {"start": t_start, "end": t_end}
        if out.trim and 0 <= out.trim.start < out.trim.end <= clip_len and out.trim.end - out.trim.start >= 5:
            trim = {"start": round(out.trim.start, 2), "end": round(out.trim.end, 2)}
        items = [i for i in items if trim["start"] <= i["start"] < trim["end"]]
        items, st_dropped = limit_stickers(items, int(ac.get("max_stickers", 2)))
        if "{{caption}}" in user:                        # edit_v5：精排要求补的前情字幕，模型漏了就在开头补上
            items = ensure_caption(items, caption, trim)
        edit = {"version": 1, "window": window, "trim": trim, "subtitles": {"enabled": True}, "items": items,
                "generated_by": f"{version} / {client.model}" + (f" / {v['label']}" if multi or key != DEFAULT else ""), "note": out.note[:200]}
        edit, dropped = enforce_density(edit)
        meme_notes: list[str] = []
        if "{{memes}}" in user:                          # 没提供梗的时候，模型写的梗标记一律去掉
            edit, meme_notes = enforce_memes(edit, {m["text"] for m in offered}, int(v["max_memes"]), float(ms["min_gap"]))
        if len(edit["items"]) > v["max_items"]:
            dropped += edit["items"][v["max_items"]:]
            edit["items"] = edit["items"][:v["max_items"]]
        try:
            edit = validate_edit(edit, clip_len)
        except EditError as e:
            print(f"生成的脚本不合法：{e}", file=sys.stderr)
            return None
        edit.update(session=sess.name, rank=args.rank, updated_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
        return edit, bad, dropped, meme_notes

    made = []
    for key in styles:
        res = make(key)
        if res is None:
            if key == DEFAULT or len(styles) == 1:
                return 1                                # 标准版（或唯一的一版）失败就整个失败
            print(f"  {STYLES[key]['label']}版没生成成功，跳过")
            continue
        made.append((key, res))
    if not made:
        return 1
    chosen_key = DEFAULT if any(k == DEFAULT for k, _ in made) else made[0][0]
    chosen = next(r for k, r in made if k == chosen_key)
    edit = chosen[0]
    dst.parent.mkdir(exist_ok=True)
    dst.write_text(json.dumps(edit, ensure_ascii=False, indent=1), encoding="utf-8")
    ai_meta = {"prompt": version, "model": client.model, "taste_used": bool(extra and taste["active"]),
               "memes_offered": [{"meme": m["text"], "sim": m["sim"], "score": m["score"]} for m in offered],
               "meme_method": meme_method, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    # 原稿另存一份：之后和人工终稿对比，剪辑口味才能积累下来（多版本时是被选中的那一版）
    (dst.parent / f"{args.rank:02d}.ai.json").write_text(json.dumps(
        {"edit": edit, "meme_notes": chosen[3], "variant": chosen_key if multi or chosen_key != DEFAULT else None, **ai_meta}, ensure_ascii=False, indent=1), encoding="utf-8")
    if multi:
        save_variants(dst.parent, args.rank, [
            {"key": k, "label": STYLES[k]["label"], "edit": r[0], "meme_notes": r[3]} for k, r in made], chosen_key, ai_meta)
    elif variants_load(dst.parent, args.rank):
        (dst.parent / f"{args.rank:02d}.variants.json").unlink(missing_ok=True)       # 这次只出标准版：旧的多版本作废

    for key, (e, bad, dropped, meme_notes) in made:
        kinds = {}
        for i in e["items"]:
            kinds[i["type"]] = kinds.get(i["type"], 0) + 1
        label = f"【{STYLES[key]['label']}】" if multi or key != DEFAULT else ""
        print(f"{label}初稿：成片 {e['trim']['start']:.1f}–{e['trim']['end']:.1f} 秒，效果 {len(e['items'])} 个 "
              f"（花字 {kinds.get('text', 0)}、运镜 {kinds.get('zoom', 0)}、贴图 {kinds.get('sticker', 0)}）"
              + (f"；丢掉 {len(dropped) + len(bad)} 个不合规的" if dropped or bad else ""))
        for x in bad[:5]:
            print("  - " + x)
        used = [i["meme"] for i in e["items"] if i.get("meme")]
        if "{{memes}}" in user:
            print(f"梗库（{meme_method}）：给了 {len(offered)} 个，用了 {len(used)} 个" + (f"：{'、'.join(used)}" if used else ""))
            for x in meme_notes:
                print("  - " + x)
        print(f"思路：{e.get('note') or '—'}")
    if why:
        print(why)
    if multi:
        print(f"已生成 {len(made)} 个版本（默认选中{STYLES[chosen_key]['label']}版），在编辑器顶部切换比较")
    print(f"已保存 {dst}，在后台成片编辑器里打开即可微调、渲染")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
