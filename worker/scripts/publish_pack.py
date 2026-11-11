#!/usr/bin/env python3
"""发布包：给一个片段生成 B站投稿要填的封面、标题、简介、标签。存到 output/<场次>/publish/<序号>.json。

用法：
  python scripts/publish_pack.py output/<场次>/candidates.json --rank 3                    # 生成（已有就补缺的）
  python scripts/publish_pack.py output/<场次>/candidates.json --rank 3 --cover-t 5150.5 --cover-text "金油！"   # 换封面
  python scripts/publish_pack.py output/<场次>/candidates.json --rank 3 --no-cover         # 只生成文字

- 标题候选来自精排；简介按 config 的 publish.desc_template；标签 = 固定标签 + 类型 + 片段里出现的梗
- 封面截图默认取精排找到的笑点那一帧；录像被移走后会依次改用成片、编辑预览、审核片段里的画面
- 后台改过的标题 / 简介 / 标签不会被覆盖（--refresh 才会重新生成）
最后一行输出整份 JSON（后台读取）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from clipper.kcfg import find_taboo, load_cfg  # noqa: E402
from clipper.streamer import decisions_path, resolve_config  # noqa: E402
from clipper.asr import load_transcript  # noqa: E402
from clipper.clipwin import clip_origin, read_windows  # noqa: E402
from clipper.edit import edit_window  # noqa: E402
from clipper.peaks import hhmmss  # noqa: E402
from clipper.publish import DEFAULT_TAGS, build_description, build_tags, clean_title, render_cover  # noqa: E402
from cut import pieces  # noqa: E402


def read(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
    except (OSError, ValueError):
        return default


def grab(video: Path, t: float, out: Path) -> bool:
    if t < 0 or not video.exists():
        return False
    r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video),
                        "-frames:v", "1", "-q:v", "2", str(out)], capture_output=True)
    return r.returncode == 0 and out.exists() and out.stat().st_size > 0


def grab_frame(sess: Path, data: dict, cand: dict, edit: dict, t: float, out: Path) -> str | None:
    """按优先级找画面：原录像（最清楚）→ 成片 → 编辑预览 → 审核片段。返回用了哪个。"""
    parts = pieces(data["segments"], t, t + 1.0)
    if parts and grab(parts[0][0], parts[0][1], out):
        return "录像"
    ws, we = edit_window(edit, cand)
    t0 = ws + float((edit.get("trim") or {}).get("start", 0))
    rank = cand["rank"]
    finals = sorted((sess / "final").glob(f"{rank:02d}_*p.mp4"), key=lambda p: int(p.stem.rsplit("_", 1)[1][:-1]),
                    reverse=True) if (sess / "final").exists() else []
    for f in finals:
        if grab(f, t - t0, out):
            return "成片"
    for f in sorted((sess / "edit_preview").glob(f"{rank:02d}_*.mp4")) if (sess / "edit_preview").exists() else []:
        try:
            a = float(f.stem.split("_")[1])
        except (IndexError, ValueError):
            continue
        if grab(f, t - a, out):
            return "编辑预览"
    for f in sorted((sess / "clips").glob(f"{rank:02d}_*.mp4")) if (sess / "clips").exists() else []:
        if grab(f, t - clip_origin(read_windows(sess), cand), out):
            return "审核片段"
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("candidates")
    ap.add_argument("--rank", type=int, required=True)
    ap.add_argument("--cover-t", type=float, help="封面用整场第几秒的画面")
    ap.add_argument("--cover-text", help="封面上的大字（空字符串 = 不加字）")
    ap.add_argument("--no-cover", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="标题 / 简介 / 标签重新生成（覆盖后台改过的）")
    ap.add_argument("--config", default=None, help="主播配置；不填时按 candidates.json 所在目录推导主播")
    args = ap.parse_args()

    cpath = Path(args.candidates)
    sess = cpath.parent
    cfg = load_cfg(resolve_config(args.config, sess))
    pc = cfg.get("publish") or {}
    data = json.loads(cpath.read_text(encoding="utf-8"))
    cand = next((c for c in data["candidates"] if c["rank"] == args.rank), None)
    if not cand:
        print(f"没有序号 {args.rank} 的候选", file=sys.stderr)
        return 1
    edit = read(sess / "edits" / f"{args.rank:02d}.json", {}) or {}
    rr = next((r for r in (read(sess / "rerank.json", {}) or {}).get("candidates", []) if r["rank"] == args.rank), {})
    llm = rr.get("llm") or {}
    ws, we = edit_window(edit, cand)
    trim = edit.get("trim") or {"start": cand["clip_start"] - ws, "end": cand["clip_end"] - ws}
    a, b = ws + float(trim["start"]), ws + float(trim["end"])

    out_dir = sess / "publish"
    out_dir.mkdir(exist_ok=True)
    pf = out_dir / f"{args.rank:02d}.json"
    pack = read(pf, {}) or {}
    streamer = cfg.get("streamer") or {}
    titles = [clean_title(t) for t in llm.get("title_candidates") or [] if clean_title(t)]
    tr = load_transcript(sess / "transcript.json")
    speech = "".join(s["text"] for s in (tr or {}).get("sentences", []) if s["t1"] > a and s["t0"] < b)
    lore_cfg = cfg.get("lore") or {}
    lore = [*(cfg.get("lore_words") or []), *(lore_cfg.get("catchphrases") or []), *(lore_cfg.get("recurring_topics") or []),
            *(lore_cfg.get("memes") or [])]
    mem = read(sess.parent / "_memory" / "memory.json", {}) or {}
    dec = read(Path(os.environ.get("KIYA_MEMORY_DECISIONS") or decisions_path()), {}) or {}
    rejected = {str(x).lower() for x in dec.get("rejected_lore") or []}
    lore += [x["text"] for x in mem.get("lore") or [] if x["text"].lower() not in rejected]
    session_date = sess.name[:8]
    date = f"{session_date[:4]}-{session_date[4:6]}-{session_date[6:8]}" if session_date.isdigit() else sess.name
    defaults = {
        "title": titles[0] if titles else f"{streamer.get('name', '')} {date} 直播切片",
        "description": build_description(pc.get("desc_template"), what_happened=llm.get("what_happened", ""),
                                          streamer=streamer.get("name", ""), space_url=streamer.get("space_url", ""),
                                          live_url=streamer.get("live_url", ""), date=date, time=hhmmss(a)),
        "tags": build_tags(pc.get("fixed_tags") or DEFAULT_TAGS, streamer.get("name", ""), llm.get("type", ""), lore,
                           speech + " ".join(titles)),
    }
    for k, v in defaults.items():
        if args.refresh or not pack.get(k):
            pack[k] = v
    pack["taboo_hits"] = find_taboo(" ".join([pack.get("title", ""), pack.get("description", ""), speech]), cfg.get("taboo") or [])
    pack.update(session=sess.name, rank=args.rank, title_candidates=titles,
                source=streamer.get("live_url", ""), copyright="转载",
                range={"start": round(a, 1), "end": round(b, 1)})

    if not args.no_cover and (args.cover_t is not None or args.cover_text is not None or not pack.get("cover")):
        punch = cand["clip_start"] + llm["punch_time"] if llm.get("punch_time", -1) >= 0 else cand["t"]
        if llm.get("cover_time", -1) >= 0:                    # 精排 v4 选的封面帧（有表情 / 关键动作的那一秒）优先于笑点时刻
            punch = cand["clip_start"] + llm["cover_time"]
        t = args.cover_t if args.cover_t is not None else pack.get("cover_t", punch)
        t = min(max(t, a), b - 0.1)
        text = args.cover_text if args.cover_text is not None else pack.get("cover_text", (titles[0] if titles else "")[:24])
        frame = out_dir / f"{args.rank:02d}_frame.jpg"
        src = grab_frame(sess, data, cand, edit, t, frame)
        if not src:
            print("找不到可以截图的视频（录像已移走、也还没有成片 / 预览 / 审核片段）", file=sys.stderr)
            return 1
        cover = out_dir / f"{args.rank:02d}_cover.jpg"
        try:
            render_cover(frame, cover, text, pc.get("cover_font") or "Noto Sans CJK SC:bold",
                         pc.get("cover_color") or "#FFE14D", pc.get("cover_stroke") or "#E0457B")
        except RuntimeError as e:
            print(f"封面生成失败：{e}", file=sys.stderr)
            return 1
        pack.update(cover=cover.name, cover_t=round(t, 2), cover_text=text, cover_source=src,
                    cover_version=int(time.time()))
        print(f"封面：{hhmmss(t)} 的画面（来自{src}），文字“{text}”→ {cover.name}")
    pack["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    pf.write_text(json.dumps(pack, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(pack, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
