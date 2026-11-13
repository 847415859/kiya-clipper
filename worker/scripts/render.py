#!/usr/bin/env python3
"""成片渲染：按剪辑脚本（output/<场次>/edits/<序号>.json）调用 Remotion 渲染一个候选片段的成片。

用法：
  python scripts/render.py output/20261109-2014/candidates.json --rank 3
  python scripts/render.py output/20261109-2014/candidates.json --rank 3 --height 1080

步骤：
  1. 从录像里剪出裁剪范围内的干净源视频（不烧字幕；字幕由 Remotion 画在运镜层之外，放大时不跟着放大）
  2. 用转写结果算出字幕条（样式取剪辑脚本里的；没有就用默认样式）
  3. 准备素材目录（源视频、用到的贴图、字体）和 props.json，调用 renderer/render.mjs
输出：output/<场次>/final/<序号>_<高峰时间>_<分辨率>p.mp4，旁边存一份当时的剪辑脚本。
渲染在 2 核 2G 上较慢（1 分钟片段 720p 可能要 10 分钟以上），先用 --height 480 看效果。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from clipper import music as music_lib  # noqa: E402
from clipper.boiler import business_cuts, compact_time, keep_ranges  # noqa: E402
from clipper.kcfg import load_cfg  # noqa: E402
from clipper.asr import load_transcript  # noqa: E402
from clipper.edit import EditError, default_edit, edit_window, validate_edit, validate_item  # noqa: E402
from clipper.streamer import resolve_config  # noqa: E402
from clipper.peaks import hhmmss  # noqa: E402
from clipper.subtitles import (STRIP_PUNCT, StyleError, apply_corrections, build_events, get_style,  # noqa: E402
                               load_config, lore_keywords, validate_style)
from clipper.media import probe_duration  # noqa: E402
from clipper.quality import quality_report, write_report  # noqa: E402
from cut import cut_one, pieces, probe_size  # noqa: E402

FPS = 30


def stickers_dir() -> Path:
    return Path(os.environ.get("KIYA_STICKERS") or REPO / "assets" / "stickers")


def fonts_dir() -> Path:
    return Path(os.environ.get("KIYA_FONTS") or REPO / "fonts")


def load_env() -> None:
    """读仓库根目录 .env（REMOTION_BROWSER 等），后台调用时也能拿到。"""
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
            if m and m.group(1) not in os.environ and m.group(2):
                os.environ[m.group(1)] = m.group(2).strip('"').strip("'")


def render_cmd() -> list[str]:
    if os.environ.get("KIYA_RENDER_CMD"):              # 测试时替换成假的渲染器
        return shlex.split(os.environ["KIYA_RENDER_CMD"])
    renderer = REPO / "renderer"
    if not (renderer / "node_modules").exists():
        sys.exit(f"渲染器还没安装依赖：cd {renderer} && npm install --registry=https://registry.npmmirror.com")
    return ["node", str(renderer / "render.mjs")]


def font_files() -> dict[str, Path]:
    """fonts/ 目录里的字体：{字体族名: 文件}（系统已安装的字体 Chrome 自己能找到，不用列）。"""
    out: dict[str, Path] = {}
    d = fonts_dir()
    if not d.is_dir() or not shutil.which("fc-scan"):
        return out
    for f in sorted(d.iterdir()):
        if f.suffix.lower() in (".ttf", ".otf", ".ttc", ".woff2"):
            r = subprocess.run(["fc-scan", "--format", "%{family}\n", str(f)], capture_output=True, text=True)
            for line in r.stdout.splitlines():
                for fam in line.split(","):
                    if fam.strip():
                        out.setdefault(fam.strip(), f)
    return out


def link(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        dst.symlink_to(src.resolve())
    except OSError:
        shutil.copy(src, dst)


def subtitle_events(sentences, start, end, style, corrections) -> list[dict]:
    events = build_events(sentences, start, end, style, corrections)
    for e in events:          # 逐字变色用的词：与烧录字幕一致地纠错、去标点
        words = []
        for a, b, w in e["words"]:
            w = apply_corrections(w, corrections)
            if style["strip_punct"]:
                w = w.rstrip(STRIP_PUNCT)
            if w:
                words.append([round(a, 3), round(b, 3), w])
        e["words"] = words
        e["t0"], e["t1"] = round(e["t0"], 3), round(e["t1"], 3)
    return events


def compact_events(events: list[dict], origin: float, removed: list[dict]) -> list[dict]:
    """营业话从源视频删除后，把字幕事件和逐字时间映射到压缩后的时间轴。"""
    out = []
    for event in events:
        ea, eb = origin + float(event["t0"]), origin + float(event["t1"])
        if any(ea < float(q["end"]) and eb > float(q["start"]) for q in removed):
            continue
        e = dict(event)
        e["t0"] = round(compact_time(ea, removed) - compact_time(origin, removed), 3)
        e["t1"] = round(compact_time(eb, removed) - compact_time(origin, removed), 3)
        e["words"] = [[round(compact_time(origin + float(a), removed) - compact_time(origin, removed), 3),
                        round(compact_time(origin + float(b), removed) - compact_time(origin, removed), 3), w]
                       for a, b, w in event.get("words", [])
                       if not any(origin + float(a) < float(q["end"]) and origin + float(b) > float(q["start"])
                                  for q in removed)]
        if e["t1"] > e["t0"] and e.get("words"):
            out.append(e)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("candidates", help="screen.py 输出的 candidates.json")
    ap.add_argument("--rank", type=int, required=True)
    ap.add_argument("--height", type=int, default=720, choices=[360, 480, 720, 1080])
    ap.add_argument("--edit", help="剪辑脚本，默认 output/<场次>/edits/<序号>.json")
    ap.add_argument("--config", default=None, help="主播配置；不填时按 candidates.json 所在目录推导主播")
    args = ap.parse_args()
    load_env()

    cpath = Path(args.candidates)
    sess_dir = cpath.parent
    data = json.loads(cpath.read_text(encoding="utf-8"))
    cand = next((c for c in data["candidates"] if c["rank"] == args.rank), None)
    if not cand:
        print(f"没有序号 {args.rank} 的候选", file=sys.stderr)
        return 1
    clip_len = cand["clip_end"] - cand["clip_start"]
    epath = Path(args.edit) if args.edit else sess_dir / "edits" / f"{args.rank:02d}.json"
    try:
        raw = json.loads(epath.read_text(encoding="utf-8")) if epath.exists() else default_edit(clip_len)
        edit = validate_edit(raw, clip_len)
    except (EditError, ValueError) as e:
        print(f"剪辑脚本有误：{e}", file=sys.stderr)
        return 1
    ws, _ = edit_window(edit, cand)            # 素材范围可以超出候选片段（按精排修正后的起止）
    t_start = ws + edit["trim"]["start"]
    t_end = ws + edit["trim"]["end"]
    tr = load_transcript(sess_dir / "transcript.json")
    removed = business_cuts(tr["sentences"], t_start, t_end) if tr else []
    kept = keep_ranges(t_start, t_end, removed)
    dur = sum(b - a for a, b in kept)
    print(f"#{args.rank} {hhmmss(t_start)}–{hhmmss(t_end)}（原始 {t_end - t_start:.1f} 秒，成片 {dur:.1f} 秒），"
          f"效果 {len(edit['items'])} 个，{args.height}p" + (f"，移除 {len(removed)} 段营业话" if removed else ""))

    # 1. 干净的源视频（按裁剪范围和分辨率缓存）
    work = sess_dir / "render"
    rm_tag = "_rm" + str(int(round(sum(q["end"] - q["start"] for q in removed) * 100))) if removed else ""
    src = work / f"src_{args.rank:02d}_{t_start:.1f}_{t_end:.1f}{rm_tag}_{args.height}.mp4"
    if not src.exists():
        parts = [part for a, b in kept for part in pieces(data["segments"], a, b)]
        if not parts:
            print("找不到对应的录像文件（可能已同步到 OSS 并从本地删除）", file=sys.stderr)
            return 1
        work.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        cut_one(parts, src, False, args.height, None, removed)
        print(f"源视频已剪好（{time.time() - t0:.0f} 秒）")
    width, height = probe_size(src)

    # 2. 字幕
    subs: dict = {"enabled": False}
    corrections: dict = {}
    if edit["subtitles"]["enabled"] and tr:
        sub_cfg = load_config()
        try:
            style = validate_style(edit["subtitles"]["style"]) if "style" in edit["subtitles"] else get_style(None, sub_cfg)[1]
        except StyleError as e:
            print(f"字幕样式有误：{e}", file=sys.stderr)
            return 1
        streamer = load_cfg(resolve_config(args.config, sess_dir)) if resolve_config(args.config, sess_dir).exists() else {}
        keywords = sorted({*(lore_keywords(streamer) if style["highlight_lore"] else []), *style["highlight_words"]},
                          key=len, reverse=True)
        corrections = sub_cfg.get("corrections") or {}
        events = subtitle_events(tr["sentences"], t_start, t_end, style, corrections)
        if removed:
            events = compact_events(events, t_start, removed)
        subs = {"enabled": True, "style": style, "events": events, "keywords": keywords}
        print(f"字幕 {len(events)} 条（字体 {style['font']}）")
    elif edit["subtitles"]["enabled"]:
        print("这场还没有转写，成片没有字幕")

    # 3. 素材目录 + props
    job = work / f"job_{args.rank:02d}"
    if job.exists():
        shutil.rmtree(job)
    job.mkdir(parents=True)
    link(src, job / "source.mp4")
    shift = edit["trim"]["start"]
    source_origin = compact_time(t_start, removed)
    items = []
    for it in edit["items"]:
        old_start = t_start + float(it["start"] - shift)
        old_end = old_start + float(it["duration"])
        new_start = max(0.0, compact_time(old_start, removed) - source_origin)
        new_end = min(dur, compact_time(old_end, removed) - source_origin)
        it = dict(it, start=round(new_start, 3), duration=round(new_end - new_start, 3))
        if it["duration"] <= 0 or it["start"] >= dur:
            continue                                              # 在裁剪范围之外
        if it["start"] + it["duration"] <= 0:
            continue
        if it["type"] == "sticker":
            f = stickers_dir() / it["asset"]
            if not f.exists():
                print(f"贴图不存在：{it['asset']}（上传到后台的贴图库）", file=sys.stderr)
                return 1
            link(f, job / "stickers" / it["asset"])
        items.append(it)
    callback = None
    cb = edit.get("callback")
    if cb and cb.get("enabled", True):                              # 跨场次呼应：开头插旧场次的一小段画面；旧录像不在了就改成文字卡，并在日志里说清楚
        old_dir = sess_dir.parent / cb["session"]
        try:
            old = json.loads((old_dir / "candidates.json").read_text(encoding="utf-8"))
            parts = pieces(old["segments"], cb["start"], cb["end"]) if cb["mode"] == "clip" else []
        except (OSError, ValueError, KeyError):
            parts = []
        if parts:
            cbv = work / f"cb_{cb['session']}_{cb['start']:.1f}_{cb['end']:.1f}_{args.height}.mp4"
            if not cbv.exists():
                work.mkdir(parents=True, exist_ok=True)
                cut_one(parts, cbv, False, args.height, None)
            cb_dur = probe_duration(cbv) or (cb["end"] - cb["start"])
            link(cbv, job / "callback.mp4")
            cb_subs: dict = {"enabled": False}
            old_tr = load_transcript(old_dir / "transcript.json") if subs.get("enabled") else None
            if old_tr:
                cb_subs = {"enabled": True, "style": subs["style"], "keywords": subs["keywords"],
                           "events": subtitle_events(old_tr["sentences"], cb["start"], cb["end"], subs["style"], corrections)}
            callback = {"video": "callback.mp4", "duration": round(cb_dur, 3), "label": cb["label"], "subtitles": cb_subs}
            print(f"呼应：开头插 {cb['date'] or cb['session']} 的画面 {cb_dur:.1f} 秒（{cb['label']}）")
        else:
            why = "这条呼应本来就是文字卡" if cb["mode"] == "card" else f"旧录像（{cb['date'] or cb['session']}）已经不在本地"
            print(f"呼应：{why}，开头改放文字卡「{cb['label']}」")
            items.insert(0, validate_item({"type": "fakeui", "kind": "notice", "title": "前情提要", "text": cb["label"], "start": 0.3,
                                           "duration": 3.5, "preset": "slide"}, dur))
    bgm = None
    m = edit.get("music")
    if m:                                                          # 背景音乐：曲库里找不到就失败，不悄悄渲染成没有 BGM 的片
        track = music_lib.get_track(m["track"])
        if not track or not music_lib.track_file(track).is_file():
            print(f"曲库里没有 {m['track']}：在剪辑器里取消背景音乐，或把曲子放进 assets/music 并登记（scripts/music.py add）", file=sys.stderr)
            return 1
        link(music_lib.track_file(track), job / "music" / track["file"])
        # 成片从素材范围的 trim.start 处开始，所以曲子从 start + trim.start 处放
        bgm = {"file": "music/" + track["file"], "start": round(m["start"] + shift, 3), "volume": m["volume"], "duck": m["duck"]}
        print(f"背景音乐：{track['title']}（音量 {m['volume']}，{'说话时压低' if m['duck'] else '不压低'}）")
    used = {subs["style"]["font"]} if subs.get("enabled") else set()
    used |= {it.get("font") for it in items if it["type"] == "text"}
    fonts = []
    for fam, f in font_files().items():
        if fam in used:
            link(f, job / "fonts" / f.name)
            fonts.append({"family": fam, "file": "fonts/" + f.name})
    cb_len = callback["duration"] if callback else 0.0
    props = {"width": width, "height": height, "fps": FPS, "duration": round(dur + cb_len, 3), "mainDuration": round(dur, 3), "video": "source.mp4",
             "subtitles": subs, "items": items, "fonts": fonts, "music": bgm, "callback": callback}
    (job / "props.json").write_text(json.dumps(props, ensure_ascii=False, indent=1), encoding="utf-8")

    final_dir = sess_dir / "final"
    final_dir.mkdir(exist_ok=True)
    out = final_dir / f"{args.rank:02d}_{hhmmss(cand['t']).replace(':', '-')}_{args.height}p.mp4"
    tmp = out.with_suffix(".part.mp4")
    t0 = time.time()
    r = subprocess.run(render_cmd() + [str(job), str(job / "props.json"), str(tmp)])
    if r.returncode != 0 or not tmp.exists():
        print("渲染失败（看上面 Remotion 的输出）", file=sys.stderr)
        return 1
    tmp.replace(out)
    out.with_suffix(".edit.json").write_text(json.dumps(edit, ensure_ascii=False, indent=1), encoding="utf-8")
    quality = quality_report(sess_dir, args.rank, final=out, candidate=cand, edit=edit)
    quality_path = sess_dir / "quality" / f"{args.rank:02d}.json"
    write_report(quality, quality_path)
    state = "通过" if quality.ok and not quality.warnings else "有警告" if quality.ok else "失败"
    print(f"质检{state}：错误 {len(quality.errors)}，警告 {len(quality.warnings)}，报告 {quality_path}")
    print(f"成片：{out}（渲染用时 {time.time() - t0:.0f} 秒）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
