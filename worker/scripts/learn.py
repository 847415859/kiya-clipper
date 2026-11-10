#!/usr/bin/env python3
"""更新精排记忆（output/_memory/memory.json），让精排跟着录制、审核和投稿数据变化。

用法：
  python scripts/learn.py                 # 汇总所有场次 + 拉 B站投稿数据
  python scripts/learn.py --no-fetch      # 不联网，只用本地的转写 / 弹幕 / 标注

每次会：
  1. 各场转写和弹幕 → 反复出现的说法 / 梗（至少 2 场都有；后台删掉的不会再加回来）
  2. 审核页的 Y/N + 备注 → 精排时给大模型看的例子
  3. 主播自己的最新投稿（config 的 streamer.uid）→ 标题、播放
  4. 切片号投稿（后台“精排记忆”页填 UID）→ 播放 / 点赞；后台绑定了场次的按类型汇总
  5. 攒够 3 场“精排 + 标注”后搜一组更好的排序权重，更好才建议，后台确认后生效
下播后监控脚本会自动跑（KIYA_RERANK=1 时，在精排之前）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from clipper.kcfg import load_cfg  # noqa: E402
from clipper.bili import Bili, BiliError  # noqa: E402
from clipper.editdiff import edit_taste  # noqa: E402
from clipper.env import load_env  # noqa: E402
from clipper.memory import (DEFAULT_WEIGHTS, active_weights, build_examples, calibrate, load_marks,  # noqa: E402
                            mine_lore, ngrams_of_session, review_stats, type_stats, version_of)

MEM_DEFAULTS = {"min_sessions": 2, "lore_max": 30, "streamer_uploads": 20, "my_uploads": 30, "min_published": 5,
                "min_label_sessions": 3, "examples_yes": 3, "examples_no": 3}


def decisions_path() -> Path:
    return Path(os.environ.get("KIYA_MEMORY_DECISIONS") or REPO / "config" / "memory_decisions.json")


def read_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
    except (OSError, ValueError):
        return default


def known_words(cfg: dict) -> set[str]:
    lore = cfg.get("lore") or {}
    words = set()
    for v in lore.values():
        if isinstance(v, list):
            words.update(str(x).lower() for x in v)
    words.update(str(x).lower() for x in cfg.get("lore_words") or [])
    words.update(str(x).lower() for x in cfg.get("emotion_words") or [])
    words.add(str((cfg.get("streamer") or {}).get("name", "")).lower())
    return {w for w in words if w}


def collect_ngrams(out_dir: Path) -> dict[str, dict]:
    per = {}
    for d in sorted(p for p in out_dir.iterdir() if p.is_dir() and not p.name.startswith("_")):
        g = ngrams_of_session(d)
        if g is not None:
            per[d.name] = g
    return per


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(REPO / "config" / "kiya.yaml"))
    ap.add_argument("--out", default=str(ROOT / "output"))
    ap.add_argument("--reviews", default=str(REPO / "eval" / "reviews"))
    ap.add_argument("--no-fetch", action="store_true", help="不请求 B站")
    args = ap.parse_args()

    load_env()
    cfg = load_cfg(Path(args.config))
    mc = {**MEM_DEFAULTS, **(cfg.get("memory") or {})}
    out_dir = Path(args.out)
    mem_file = out_dir / "_memory" / "memory.json"
    old = read_json(mem_file, {}) or {}
    dec = read_json(decisions_path(), {}) or {}
    t0 = time.time()
    errors = []

    # 1. B站投稿
    su, mine = old.get("streamer_uploads") or [], old.get("my_uploads_raw") or []
    if not args.no_fetch:
        b = Bili()
        uid = (cfg.get("streamer") or {}).get("uid")
        try:
            if uid:
                su = b.uploads(uid, int(mc["streamer_uploads"]))
                print(f"主播投稿：{len(su)} 条（最新：《{su[0]['title']}》）" if su else "主播投稿：0 条")
        except BiliError as e:
            errors.append(f"主播投稿拉取失败，沿用上次的：{e}")
        my_uid = str(dec.get("my_uid") or "").strip()
        if my_uid:
            try:
                lst = b.uploads(my_uid, int(mc["my_uploads"]))
                mine = []
                for u in lst:
                    try:
                        mine.append({**u, **b.stat(u["bvid"])})
                    except BiliError as e:
                        mine.append(u)
                        errors.append(f"{u['bvid']} 数据拉取失败：{e}")
                print(f"切片号投稿：{len(mine)} 条")
            except BiliError as e:
                errors.append(f"切片号投稿拉取失败，沿用上次的：{e}")
    # 绑定到场次 / 序号：类型取精排结果，标注取审核页
    marks = load_marks(Path(args.reviews))
    published = dec.get("published") or {}
    my_uploads = []
    for u in mine:
        link = published.get(u.get("bvid")) or {}
        row = dict(u)
        if link.get("session") and link.get("rank"):
            rr = read_json(out_dir / link["session"] / "rerank.json", {}) or {}
            o = next((c.get("llm") for c in rr.get("candidates", []) if c["rank"] == int(link["rank"])), None) or {}
            row.update(session=link["session"], rank=int(link["rank"]), type=o.get("type") or "",
                       mark=(marks.get(link["session"], {}).get(int(link["rank"])) or {}).get("mark"))
        for k in ("completion", "followers"):
            if link.get(k) is not None:
                row[k] = link[k]
        my_uploads.append(row)
    ts = type_stats(my_uploads, int(mc["min_published"]))
    top_titles = [u["title"] for u in sorted(my_uploads, key=lambda u: -(u.get("view") or 0))[:5]]

    # 2. 梗 / 口头禅
    per = collect_ngrams(out_dir)
    rejected = {str(x).lower() for x in dec.get("rejected_lore") or []}
    manual = [{"text": str(m.get("text", "")).strip(), "note": m.get("note", ""), "status": "manual"}
              for m in dec.get("manual_lore") or [] if str(m.get("text", "")).strip()]
    known = known_words(cfg) | {m["text"].lower() for m in manual}
    mined = mine_lore(per, known, rejected, [u["title"] for u in su] + [u["title"] for u in my_uploads],
                      int(mc["min_sessions"]), int(mc["lore_max"]))
    old_lore = {x["text"] for x in old.get("lore") or []}
    first_seen = {x["text"]: x.get("first_added") for x in old.get("lore") or []}
    today = time.strftime("%F")
    lore = manual + [{**x, "status": "auto", "first_added": first_seen.get(x["text"]) or today} for x in mined]

    # 3. 例子 + 权重
    examples = build_examples(out_dir, marks, set(dec.get("excluded_examples") or []))
    active = active_weights(cfg, dec)
    cal = calibrate(out_dir, load_marks(Path(args.reviews), trusted_only=True), active, int(mc["min_label_sessions"]))
    an = cfg.get("analysis") or {}
    rv = review_stats(marks, mc, float(an.get("clip_before_seconds", 30)), float(an.get("clip_after_seconds", 30)))

    mem = {"generated_at": time.strftime("%F %T"), "sessions": sorted(per), "lore": lore,
           "examples": examples, "streamer_uploads": su, "my_uploads": my_uploads, "my_uploads_raw": mine,
           "review": rv, "edit_taste": edit_taste(out_dir, int(mc.get("min_edits", 5)), int(mc.get("min_edit_sessions", 2))),
           "type_stats": ts, "my_top_titles": top_titles if len(my_uploads) >= int(mc["min_published"]) else [],
           "weights": {"active": active, "default": DEFAULT_WEIGHTS, **{k: v for k, v in cal.items() if k != "active"}},
           "errors": errors}
    mem["version"] = version_of(mem)
    mem_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = mem_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(mem, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(mem_file)

    new = [x["text"] for x in mined if x["text"] not in old_lore]
    gone = sorted(old_lore - {x["text"] for x in lore})
    ny = sum(1 for e in examples if e["mark"] == "Y")
    print(f"场次 {len(per)}；梗 / 说法 {len(lore)} 个（手动 {len(manual)}，新发现 {len(new)}"
          + (f"：{'、'.join(new[:10])}" if new else "") + (f"；不再出现 {len(gone)}" if gone else "") + "）")
    if len(per) < int(mc["min_sessions"]):
        print(f"  提示：说法至少要在 {mc['min_sessions']} 场里出现才算，现在只有 {len(per)} 场")
    print(f"审核例子 {len(examples)} 个（有趣 {ny} / 不有趣 {len(examples) - ny}）；切片号已绑定 "
          f"{sum(1 for u in my_uploads if u.get('session'))}/{len(my_uploads)} 条"
          + ("；类型表现已启用" if ts else f"（绑定满 {mc['min_published']} 条后按类型汇总）"))
    print(f"权重：{cal['status']}（只用盲审的标注）")
    print(f"剪辑口味：{mem['edit_taste']['status']}")
    if rv["marked"]:
        top = "、".join(f"{k} {v}" for k, v in sorted(rv["reasons"].items(), key=lambda kv: -kv[1])[:4])
        print(f"审核：{rv['marked']} 条已标" + (f"，平均 {rv['avg_seconds']} 秒/条" if rv["avg_seconds"] else "")
              + (f"；常见原因：{top}" if top else ""))
        for sug in rv["boundary"]["suggestions"]:
            print(f"  边界建议：{sug}")
    for e in errors:
        print(f"注意：{e}")
    print(f"记忆版本 {mem['version']}，用时 {time.time() - t0:.0f} 秒 → {mem_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
