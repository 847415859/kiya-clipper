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
import hashlib
import math
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from clipper import music as music_lib  # noqa: E402
from clipper.boiler import business_cuts, compact_time, keep_ranges  # noqa: E402
from clipper.timeline import edit_cuts, compact_events as map_events  # noqa: E402
from clipper.render_plan import plan  # noqa: E402
from clipper.kcfg import load_cfg  # noqa: E402
from clipper.env import load_env  # noqa: E402
from clipper.asr import load_transcript  # noqa: E402
from clipper.speech_edit import apply_overrides  # noqa: E402
from clipper.edit import EditError, default_edit, edit_window, validate_edit, validate_item  # noqa: E402
from clipper.streamer import resolve_config  # noqa: E402
from clipper.peaks import hhmmss  # noqa: E402
from clipper.subtitles import (STRIP_PUNCT, StyleError, apply_corrections, build_events, get_style,  # noqa: E402
                               load_config, lore_keywords, validate_style)
from clipper.media import probe_duration  # noqa: E402
from clipper.audio_diagnostics import decoded_audio_gaps, diagnose_pipeline, video_sha256  # noqa: E402
from clipper.quality import probe as probe_media, quality_report, write_report  # noqa: E402
from clipper.schedule import for_pack, append_description  # noqa: E402
from cut import cut_one, pieces, probe_size  # noqa: E402

FPS = 30


def restore_source_audio(source: Path, rendered: Path, duration: float) -> bool:
    """Remux good source audio for truncated OR internally gapped main-only audio.

    Never generate padding or re-encode video; validate before atomic replacement.
    """
    def timing(info, kind):
        stream = next((s for s in (info or {}).get("streams", []) if s.get("codec_type") == kind), {})
        try:
            start, length = float(stream["start_time"]), float(stream["duration"])
        except (KeyError, TypeError, ValueError):
            return None
        return (start, length) if math.isfinite(start) and math.isfinite(length) and length > 0 else None

    current = probe_media(rendered)
    audio = timing(current, "audio")
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid recovery duration")
    # Missing/decode-error tracks require review, rather than being assumed good.
    try:
        current_gaps = decoded_audio_gaps(rendered) if audio else []
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return False
    if audio and abs(audio[0]) <= 1 / FPS and sum(audio) >= duration - 1 / FPS and not current_gaps:
        return False
    video = timing(current, "video")
    original = timing(probe_media(source), "audio")
    tolerance = 1 / FPS
    if (not video or not original or abs(video[0]) > tolerance or abs(original[0]) > tolerance
            or abs(video[1] - duration) > tolerance or sum(original) < duration - tolerance):
        return False

    try:
        if decoded_audio_gaps(source):
            return False
        before_video_hash = video_sha256(rendered)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return False

    with tempfile.TemporaryDirectory(prefix=".restore-audio-", dir=rendered.parent) as directory:
        output = Path(directory) / rendered.name
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(rendered),
                        "-i", str(source), "-map", "0:v:0", "-map", "1:a:0", "-c", "copy",
                        "-t", f"{duration:.6f}", "-movflags", "+faststart", str(output)], check=True, timeout=120)
        restored = timing(probe_media(output), "audio")
        if not restored or abs(restored[0]) > tolerance or abs(sum(restored) - duration) > tolerance:
            raise ValueError("恢复的原声音轨未覆盖成片时长")
        if decoded_audio_gaps(output):
            raise ValueError("Recovered audio still contains timestamp gaps")
        if video_sha256(output) != before_video_hash:
            raise ValueError("Audio recovery changed encoded video")
        output.replace(rendered)
    return True


def audio_receipt(segments: list[dict], cut: Path, rendered: Path, receipt: Path,
                  duration: float, recover: bool) -> dict:
    """Persist original evidence before any recovery, including recovery failures."""
    def snapshot():
        result = diagnose_pipeline(segments, cut, rendered)
        for name, path in (("cut", cut), ("final", rendered)):
            try:
                result["stages"][name]["video_sha256"] = video_sha256(path)
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                result["stages"][name]["video_hash_error"] = str(exc)
                result["status"] = "review"
        return result

    def save(value):
        pending = receipt.with_suffix(receipt.suffix + ".tmp")
        pending.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        pending.replace(receipt)

    before = snapshot()
    report = {"before_recovery": before, "after_recovery": None,
              "recovery": {"eligible": recover, "applied": False}}
    save(report)
    try:
        sources_good = (bool(before["sources"]) and not before["segment_boundary_gaps"] and all(
            row["status"] == "available" and not row["gaps"] for row in before["sources"]))
        if recover and sources_good:
            report["recovery"]["applied"] = restore_source_audio(cut, rendered, duration)
        elif recover:
            report["recovery"]["skipped_reason"] = "selected_source_unavailable_or_gapped"
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        report["recovery"]["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["after_recovery"] = snapshot()
        if report["recovery"].get("error"):
            report["after_recovery"]["status"] = "review"
        save(report)
    return report


def stickers_dir() -> Path:
    return Path(os.environ.get("KIYA_STICKERS") or REPO / "assets" / "stickers")


def fonts_dir() -> Path:
    return Path(os.environ.get("KIYA_FONTS") or REPO / "fonts")


def render_cmd() -> list[str]:
    if os.environ.get("KIYA_RENDER_CMD"):              # 测试时替换成假的渲染器
        parts = shlex.split(os.environ["KIYA_RENDER_CMD"], posix=os.name != "nt")
        # Windows 路径中的反斜线不能按 POSIX 转义吞掉；Popen(list) 会自行处理参数引号。
        return [p[1:-1] if os.name == "nt" and len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]
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
    if os.environ.get("KIYA_RENDER_COPY_ASSETS") == "1":
        shutil.copyfile(src, dst)
        return
    try:
        dst.symlink_to(src.resolve())
    except OSError:
        shutil.copy(src, dst)


def subtitle_events(sentences, start, end, style, corrections, sentence_overrides=None) -> list[dict]:
    events = build_events(sentences, start, end, style, corrections, sentence_overrides)
    for e in events:          # 逐字变色用的词：与烧录字幕一致地纠错、去标点
        words = []
        for a, b, w in e["words"]:
            w = apply_corrections(w, None if e.get("manual") else corrections)
            if style["strip_punct"]:
                w = w.rstrip(STRIP_PUNCT)
            if w:
                words.append([round(a, 3), round(b, 3), w])
        e["words"] = [] if e.get("manual") else words
        e["t0"], e["t1"] = round(e["t0"], 3), round(e["t1"], 3)
    return events


def compact_events(events: list[dict], origin: float, removed: list[dict]) -> list[dict]:
    return map_events(events, origin, removed)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("candidates", help="screen.py 输出的 candidates.json")
    ap.add_argument("--rank", type=int, required=True)
    ap.add_argument("--height", type=int, default=720, choices=[360, 480, 720, 1080])
    ap.add_argument("--edit", help="剪辑脚本，默认 output/<场次>/edits/<序号>.json")
    ap.add_argument("--config", default=None, help="主播配置；不填时按 candidates.json 所在目录推导主播")
    ap.add_argument("--range-start", type=float, help="局部试渲染起点（成片秒数，含前情呼应）")
    ap.add_argument("--range-end", type=float)
    ap.add_argument("--schedule-tail", action="store_true", help="只试渲染日程片尾")
    ap.add_argument("--draft-id")
    ap.add_argument("--draft-style", choices=['restrained', 'standard', 'lively'])
    args = ap.parse_args()
    has_range = args.range_start is not None or args.range_end is not None
    partial = has_range or args.schedule_tail
    if args.schedule_tail and has_range:
        ap.error("日程片尾试渲染不能同时指定时间范围")
    if has_range and (args.range_start is None or args.range_end is None or
                    not all(math.isfinite(v) for v in (args.range_start, args.range_end)) or
                    not 0 <= args.range_start < args.range_end or args.range_end - args.range_start > 30):
        ap.error("局部试渲染须提供起止时间，长度不超过 30 秒")
    load_env()

    cpath = Path(args.candidates)
    sess_dir = cpath.parent
    cfg_path = resolve_config(args.config, sess_dir)
    cfg = load_cfg(cfg_path) if cfg_path.exists() else {}
    pack_path = sess_dir / "publish" / f"{args.rank:02d}.json"
    pack = json.loads(pack_path.read_text(encoding="utf-8")) if pack_path.exists() else {}
    try:
        schedule = for_pack(sess_dir.parent, pack, cfg)
        append_description(pack.get("description") or "", schedule)
    except (ValueError, OSError, TypeError) as e:
        print(f"日程预告无法生成：{e}", file=sys.stderr)
        return 1
    if args.schedule_tail and not schedule["enabled"]:
        print(schedule["reason"] + "，无法试渲染日程片尾", file=sys.stderr)
        return 1
    data = json.loads(cpath.read_text(encoding="utf-8"))
    cand = next((c for c in data["candidates"] if c["rank"] == args.rank), None)
    if not cand:
        print(f"没有序号 {args.rank} 的候选", file=sys.stderr)
        return 1
    clip_len = cand["clip_end"] - cand["clip_start"]
    epath = Path(args.edit) if args.edit else sess_dir / "edits" / f"{args.rank:02d}.json"
    try:
        raw = json.loads(epath.read_text(encoding="utf-8")) if epath.exists() else default_edit(clip_len)
        if args.draft_id:
            if not partial or not re.fullmatch('[a-f0-9]{32}', args.draft_id) or not args.draft_style:
                raise ValueError('草稿试渲染必须指定有效草稿ID、风格和局部范围')
            draft = json.loads((sess_dir/'edits/ai_drafts'/f'{args.rank:02d}'/f'{args.draft_id}.json').read_text(encoding='utf-8'))
            candidate_revision = hashlib.sha256(cpath.read_bytes()).hexdigest()
            rerank_path = sess_dir/'rerank.json'
            rerank_revision = hashlib.sha256(rerank_path.read_bytes()).hexdigest() if rerank_path.exists() else 'missing'
            if draft['candidate_revision'] != candidate_revision or draft.get('rerank_revision') != rerank_revision:
                raise ValueError('草稿候选或精排证据已变化，请重新生成')
            variant = next((v for v in draft['variants'] if v['key'] == args.draft_style), None)
            if not variant:
                raise ValueError('草稿中没有该风格')
            raw = variant['edit']
        edit = validate_edit(raw, clip_len)
        from clipper.evidence_edit import attach
        edit = attach(edit, cand, sess_dir)
    except (EditError, ValueError) as e:
        print(f"剪辑脚本有误：{e}", file=sys.stderr)
        return 1
    ws, _ = edit_window(edit, cand)            # 素材范围可以超出候选片段（按精排修正后的起止）
    t_start = ws + edit["trim"]["start"]
    t_end = ws + edit["trim"]["end"]
    tr = load_transcript(sess_dir / "transcript.json")
    removed = edit_cuts(edit, tr["sentences"] if tr else [], ws, t_start, t_end)
    kept = keep_ranges(t_start, t_end, removed)
    reaction = edit.get("required_reaction")
    if reaction and not any(start <= reaction["start"] + 1e-6 and end >= reaction["end"] - 1e-6 for start, end in kept):
        print(f"成片未完整保留回应 {reaction['start']:g}–{reaction['end']:g} 秒，请检查裁剪起止和片中删除", file=sys.stderr)
        return 1
    dur = sum(b - a for a, b in kept)
    if dur < 1:
        print("删除后不足 1 秒，请减少片中删除或关闭自动删除营业话", file=sys.stderr)
        return 1
    print(f"#{args.rank} {hhmmss(t_start)}–{hhmmss(t_end)}（原始 {t_end - t_start:.1f} 秒，成片 {dur:.1f} 秒），"
          f"效果 {len(edit['items'])} 个，{args.height}p" + (f"，移除 {len(removed)} 段内容" if removed else ""))

    # 1. 干净的源视频（按裁剪范围和分辨率缓存）
    work = sess_dir / "render"
    source_revision = [{"video": s["video"], "offset": s["offset"],
                        "size": Path(s["video"]).stat().st_size if Path(s["video"]).exists() else None,
                        "mtime_ns": Path(s["video"]).stat().st_mtime_ns if Path(s["video"]).exists() else None} for s in data["segments"]]
    parts = [part for a, b in kept for part in pieces(data["segments"], a, b)]
    audio_sources = [{"video": str(path), "source_ranges": [[a, b]]} for path, a, b in parts]
    cache_version = "_v4_" if len(parts) > 1 else "_v3_"
    rm_tag = cache_version + hashlib.sha256(json.dumps([t_start, t_end, removed, source_revision], sort_keys=True).encode()).hexdigest()[:16]
    src = work / f"src_{args.rank:02d}_{t_start:.1f}_{t_end:.1f}{rm_tag}_{args.height}.mp4"
    if not src.is_file() or not src.stat().st_size:
        if not parts:
            print("找不到对应的录像文件（可能已同步到 OSS 并从本地删除）", file=sys.stderr)
            return 1
        work.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        with tempfile.TemporaryDirectory(prefix=".source-", dir=work) as tmp:
            pending = Path(tmp) / src.name
            cut_one(parts, pending, False, args.height, None, removed)
            pending.replace(src)
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
        overrides = edit["subtitles"].get("sentence_overrides") or []
        _, unmatched = apply_overrides(tr["sentences"], overrides)
        if unmatched:
            print(f"提醒：{len(unmatched)} 条字幕修订的原文或时间已变化，未套用；请回编辑器重新校对")
        subs = plan(edit, tr["sentences"], ws, style, corrections, keywords)["subtitles"]
        events = subs["events"]
        print(f"字幕 {len(events)} 条（字体 {style['font']}）")
    elif edit["subtitles"]["enabled"]:
        print("这场还没有转写，成片没有字幕")

    # 3. 素材目录 + props
    job = work / (f"job_{args.rank:02d}" + ("_preview" if partial else ""))
    if job.exists():
        if job.resolve().parent != work.resolve():
            raise ValueError("渲染目录超出工作目录")
        shutil.rmtree(job)
    job.mkdir(parents=True)
    link(src, job / "source.mp4")
    portrait_file = schedule.pop("_portrait_file", None)
    if portrait_file:
        portrait_name = "schedule-portrait" + Path(portrait_file).suffix.lower()
        link(Path(portrait_file), job / portrait_name)
        schedule["portrait"] = portrait_name
    shift = edit["trim"]["start"]
    source_origin = compact_time(t_start, removed)
    items = []
    for it in edit["items"]:
        old_start = max(t_start, ws + float(it["start"]))
        old_end = min(t_end, ws + float(it["start"]) + float(it["duration"]))
        if old_end <= old_start:
            continue
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
    if schedule["enabled"]:
        used.add("Noto Sans CJK SC")
    used |= {it.get("font") for it in items if it["type"] == "text"}
    fonts = []
    for fam, f in font_files().items():
        if fam in used:
            link(f, job / "fonts" / f.name)
            fonts.append({"family": fam, "file": "fonts/" + f.name})
    cb_len = callback["duration"] if callback else 0.0
    # Quantize each sequence independently so the outro starts on the exact next frame.
    main_frames, cb_frames = round(dur * FPS), round(cb_len * FPS)
    tail_frames = round(schedule["duration"] * FPS)
    from clipper.arrangement import inspect_plan, speech_events

    speech = speech_events(edit, tr["sentences"] if tr else [], kept) if bgm and bgm["duck"] else []
    findings = inspect_plan(edit, items, subs, kept, FPS)
    props = {"speechEvents": speech, "width": width, "height": height, "fps": FPS, "duration": (main_frames + cb_frames + tail_frames) / FPS,
             "mainDuration": main_frames / FPS, "video": "source.mp4", "schedule": schedule,
             "subtitles": subs, "items": items, "fonts": fonts, "music": bgm, "callback": callback}
    (job / "props.json").write_text(json.dumps(props, ensure_ascii=False, indent=1), encoding="utf-8")

    frame_args = []
    if args.schedule_tail:
        args.range_start = (main_frames + cb_frames) / FPS
        args.range_end = props["duration"]
    if partial:
        first = int(math.floor(args.range_start * FPS + 1e-6))
        last = min(int(math.ceil(args.range_end * FPS - 1e-6)), max(1, int(math.floor(props["duration"] * FPS + 0.5)))) - 1
        if first > last:
            print("试渲染起点超出成片时长", file=sys.stderr)
            return 1
        frame_args = ["--frames", f"{first}:{last}"]
    final_dir = sess_dir / ("render_previews" if partial else "final")
    final_dir.mkdir(exist_ok=True)
    out = final_dir / f"{args.rank:02d}_{hhmmss(cand['t']).replace(':', '-')}_{args.height}p.mp4"
    if partial:
        out = final_dir / f"{args.rank:02d}_{first}_{last}_{args.height}p.mp4"
        if args.draft_id:
            draft_tag = hashlib.sha256(json.dumps(edit, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
            out = final_dir / f"{args.rank:02d}_{first}_{last}_{args.height}p_draft_{args.draft_id}_{args.draft_style}_{draft_tag}.mp4"
    tmp = out.with_suffix(".part.mp4")
    t0 = time.time()
    r = subprocess.run(render_cmd() + [str(job), str(job / "props.json"), str(tmp)] + frame_args)
    if r.returncode != 0 or not tmp.exists():
        audio_receipt(audio_sources, src, tmp, out.with_suffix(".audio.json"),
                      props["mainDuration"], False)
        print("渲染失败（看上面 Remotion 的输出）", file=sys.stderr)
        return 1
    audio_report = audio_receipt(audio_sources, src, tmp, out.with_suffix(".audio.json"),
                                 props["mainDuration"],
                                 not partial and not bgm and not callback and not schedule["enabled"])
    if audio_report["recovery"]["applied"]:
        print("Remotion 音轨缺失采样，已从连续拼接源视频恢复原声（视频流哈希保持一致）")
    if not partial:
        # Record the new file before replacing the video. An interrupted replacement
        # leaves a detectable mismatch, never a new outro without its matching receipt.
        metadata = out.with_suffix(".schedule.json")
        metadata_tmp = metadata.with_suffix(".json.tmp")
        metadata_tmp.write_text(json.dumps({**schedule, "callback_duration": cb_frames / FPS,
            "outro_duration": tail_frames / FPS, "video_size": tmp.stat().st_size,
            "video_mtime_ns": tmp.stat().st_mtime_ns}, ensure_ascii=False, indent=1), encoding="utf-8")
        metadata_tmp.replace(metadata)
    tmp.replace(out)
    out.with_suffix(".edit.json").write_text(json.dumps(edit, ensure_ascii=False, indent=1), encoding="utf-8")
    from clipper.protection import coverage, required, revision
    evidence_plan = coverage(required(edit), kept)
    preview_start = first / FPS if partial else 0
    preview_end = (last + 1) / FPS if partial else float("inf")
    for row in evidence_plan["checks"]:
        visible = []
        for span in row["output_ranges"]:
            a = span["output_start"] + cb_frames / FPS
            b = span["output_end"] + cb_frames / FPS
            if min(b, preview_end) > max(a, preview_start):
                left, right = max(a, preview_start), min(b, preview_end)
                visible.append(dict(span, source_start=span["source_start"] + left - a,
                                    source_end=span["source_end"] - (b - right),
                                    output_start=left - preview_start, output_end=right - preview_start))
        row["output_ranges"] = visible
        if partial:
            row["full_edit_status"] = row["status"]
            row["status"] = "partial_preview_not_evaluable"
            row["preview_scope"] = "only_intersection_with_partial_render"
    if partial:
        evidence_plan["full_edit_status"] = evidence_plan["status"]
        evidence_plan["status"] = "partial_preview_not_evaluable"
        evidence_plan["scope_note"] = "局部预览仅提供交集定位，不能证明完整成片覆盖"
    evidence_plan.update(source_revision=source_revision, edit_revision=revision(edit), media_size=out.stat().st_size,
                         media_mtime_ns=out.stat().st_mtime_ns, height=args.height,
                         callback_offset=cb_frames / FPS, partial=partial)
    if not partial:
        for finding in findings:
            finding["output_seconds"] = [t + cb_frames / FPS for t in finding["main_seconds"]]
        evidence_plan["arrangement"] = {"findings": findings, "speech_events": speech,
                                        "basis": "render_plan", "narrative": edit.get("narrative_plan")}
    out.with_suffix(".evidence.json").write_text(json.dumps(evidence_plan, ensure_ascii=False, indent=2), encoding="utf-8")
    if partial:
        out.with_suffix(".range.json").write_text(json.dumps({"start": first / FPS, "end": (last + 1) / FPS,
                                                            "duration": (last - first + 1) / FPS}), encoding="utf-8")
        print(f"局部试渲染：{out}（{(last - first + 1) / FPS:.2f} 秒）")
        return 0
    quality = quality_report(sess_dir, args.rank, final=out, candidate=cand, edit=edit)
    quality_path = sess_dir / "quality" / f"{args.rank:02d}.json"
    write_report(quality, quality_path)
    state = "通过" if quality.ok and not quality.warnings else "有警告" if quality.ok else "失败"
    print(f"质检{state}：错误 {len(quality.errors)}，警告 {len(quality.warnings)}，报告 {quality_path}")
    print(f"成片：{out}（渲染用时 {time.time() - t0:.0f} 秒）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
