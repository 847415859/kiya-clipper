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
import re
import shutil
import subprocess
import sys
import time
import uuid
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
from clipper.publish import (COVER_THEMES, DEFAULT_TAGS, bili_copyright, build_description, build_tags, clean_title,
                             cover_concept, normalize_title_options, render_theme_cover)  # noqa: E402
from clipper.cover_prompt import seedream_options, seedream_prompt  # noqa: E402
from clipper.cover_quality import cover_text_options, inspect_cover_text, inspect_reference_frame  # noqa: E402
from clipper.seedream import SeedreamError, generate as generate_seedream  # noqa: E402
from cut import pieces  # noqa: E402


def crop_cover_portrait(source: Path, destination: Path, box: list) -> Path:
    """Crop a portrait out of a larger reference using normalized coordinates."""
    from PIL import Image

    if (not isinstance(box, list) or len(box) != 4
            or any(type(v) not in (int, float) or not 0 <= v <= 1 for v in box)
            or box[0] >= box[2] or box[1] >= box[3]):
        raise SeedreamError("publish.cover_portrait_crop 须为有效的 [左, 上, 右, 下]，坐标范围 0–1")
    with Image.open(source) as image:
        rect = tuple(round(v * size) for v, size in zip(box, (image.width, image.height) * 2))
        if rect[2] - rect[0] < 15 or rect[3] - rect[1] < 15:
            raise SeedreamError("主播形象参考的裁剪区域过小")
        image.crop(rect).convert("RGB").save(destination, "JPEG", quality=95)
    return destination


def read(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
    except (OSError, ValueError):
        return default


def archive_seedream(out_dir: Path, rank: int, image: Path, raw: Path | None, meta: dict,
                     history: list[dict], text: str = "", legacy: bool = False) -> dict:
    """Immutable files for selection and further refinement; never invokes an image model."""
    version = uuid.uuid4().hex
    folder = out_dir / "cover_history" / f"{rank:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / f"{version}.jpg"
    raw_dst = folder / f"{version}.raw.jpg"
    shutil.copyfile(image, dst)
    if raw and raw.is_file():
        shutil.copyfile(raw, raw_dst)
    return {"id": version, "number": max((int(v.get("number", 0)) for v in history), default=0) + 1,
            "created_at": time.time(), "legacy": legacy, "text": text, "meta": dict(meta, version_id=version),
            "has_raw": raw_dst.is_file()}


def preserve_legacy_seedream(out_dir: Path, rank: int, pack: dict) -> None:
    if pack.get("cover_history"):
        return
    old = out_dir / f"{rank:02d}_cover_seedream.jpg"
    if not old.is_file():
        return
    raw = out_dir / f"{rank:02d}_cover_seedream_raw.jpg"
    meta = next((v.get("meta") or {} for v in pack.get("cover_variants", []) if v.get("variant") == "seedream"), {})
    # Legacy covers may not have a raw image; retain the final image but mark that limitation.
    pack["cover_history"] = [archive_seedream(out_dir, rank, old, raw, meta, [], pack.get("cover_text", ""), True)]


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
    ap.add_argument("--cover-variant", choices=["hero", "comic", "abstract"], help="主题化构图：人物海报 / 漫画冲击 / 抽象事件卡")
    ap.add_argument("--cover-theme", choices=sorted(COVER_THEMES), help="覆盖自动判断的主题")
    ap.add_argument("--cover-mode", choices=["programmatic", "seedream", "all"], default="programmatic",
                    help="封面方式：程序化、Seedream 生图或两者；默认程序化")
    ap.add_argument("--cover-supplement", help="Seedream 补充要求，在自动方案上追加，最多 1500 字；空字符串清除")
    ap.add_argument("--cover-reference", choices=["frame", "seedream"], help="直播画面或上次 Seedream 底图")
    ap.add_argument("--cover-prompt-only", action="store_true", help="仅预览完整生图提示词，不截图、不生成、不保存")
    ap.add_argument("--cover-select", choices=["hero", "comic", "abstract", "seedream"],
                    help="把已经生成好的某张候选设为主封面（不重新生成、不调生图接口）")
    ap.add_argument("--cover-select-version", help="选择已经保存的 Seedream 历史版本，不重新生成")
    ap.add_argument("--no-cover", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="标题 / 简介 / 标签重新生成（覆盖后台改过的）")
    ap.add_argument("--config", default=None, help="主播配置；不填时按 candidates.json 所在目录推导主播")
    args = ap.parse_args()

    cpath = Path(args.candidates)
    sess = cpath.parent
    cfg = load_cfg(resolve_config(args.config, sess))
    pc = cfg.get("publish") or {}
    try:
        copyright = bili_copyright(cfg)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
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
    pf = out_dir / f"{args.rank:02d}.json"
    pack = read(pf, {}) or {}
    try:
        options = seedream_options(pack.get("cover_seedream_options"), args.cover_supplement, args.cover_reference)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    streamer = cfg.get("streamer") or {}
    titles, title_options = normalize_title_options(llm)
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
    pack.update(strategy_version=llm.get("strategy_version"), rubric_version=llm.get("rubric_version"),
                prompt_version=rr.get("prompt_version"), packaging_evidence=llm.get("value_evidence") or llm.get("evidence") or [],
                candidate_id=f"{sess.name}:{args.rank}", hook_caption=llm.get("hook_caption") or "",
                session=sess.name, rank=args.rank, title_candidates=titles, title_options=title_options,
                cover_text_options=cover_text_options(llm, titles[0] if titles else ""),
                source=streamer.get("live_url", ""), copyright=copyright,
                range={"start": round(a, 1), "end": round(b, 1)})

    # Both preview and generation use the same automatic concept and saved additions.
    text = args.cover_text if args.cover_text is not None else pack.get("cover_text", (titles[0] if titles else "")[:24])
    concept = cover_concept(llm, text, speech)
    if not str(text or "").strip() and args.cover_text is None:
        text = str(llm.get("punch_line") or llm.get("what_happened") or concept.get("label") or "这也能发生？").strip()[:18]
    if args.cover_theme:
        concept["theme"] = args.cover_theme
        concept.update({k: COVER_THEMES[args.cover_theme][k] for k in ("label", "accent", "second", "symbol")})
    concept["headline"] = str(text or "").strip()[:30]
    text = concept["headline"]
    portrait_setting = pc.get("cover_portrait_file")
    portrait = REPO / portrait_setting if portrait_setting else None
    prompt = seedream_prompt(concept, options, text, streamer_name=streamer.get("name") or "",
                             has_portrait=portrait is not None)
    if args.cover_prompt_only:
        print(json.dumps({"prompt": prompt, "options": options, "concept": concept, "text": text}, ensure_ascii=False))
        return 0
    out_dir.mkdir(exist_ok=True)

    if args.cover_select_version:
        version = args.cover_select_version
        if not re.fullmatch(r"[a-f0-9]{32}", version):
            print("封面版本标识不合法", file=sys.stderr)
            return 1
        hit = next((v for v in pack.get("cover_history", []) if v.get("id") == version), None)
        archived = out_dir / "cover_history" / f"{args.rank:02d}" / f"{version}.jpg"
        archived_raw = archived.with_name(version + ".raw.jpg")
        if not hit or not archived.is_file():
            print("历史封面不存在，请刷新后重试", file=sys.stderr)
            return 1
        shutil.copyfile(archived, out_dir / f"{args.rank:02d}_cover.jpg")
        shutil.copyfile(archived, out_dir / f"{args.rank:02d}_cover_seedream.jpg")
        current_raw = out_dir / f"{args.rank:02d}_cover_seedream_raw.jpg"
        if archived_raw.is_file():
            shutil.copyfile(archived_raw, current_raw)
        else:
            current_raw.unlink(missing_ok=True)  # A different version's raw image must never be used here.
        variants = [v for v in pack.get("cover_variants", []) if v.get("variant") != "seedream"]
        variants.append({"variant": "seedream", "file": f"{args.rank:02d}_cover_seedream.jpg", "label": "Seedream 生图", "meta": hit.get("meta") or {}})
        pack.update(cover=f"{args.rank:02d}_cover.jpg", cover_variant="seedream", cover_variants=variants,
                    cover_history_selected=version, cover_text=hit.get("text", ""), cover_version=time.time_ns() // 1_000_000)
        print(f"封面：选用历史版本 {hit.get('number', '')}")
    elif args.cover_select:
        hit = next((x for x in pack.get("cover_variants") or [] if x.get("variant") == args.cover_select), None)
        src_file = out_dir / str((hit or {}).get("file") or "")
        if not hit or not src_file.is_file():
            print(f"还没有生成过“{args.cover_select}”这张候选封面，先生成再选", file=sys.stderr)
            return 1
        shutil.copyfile(src_file, out_dir / f"{args.rank:02d}_cover.jpg")
        pack.update(cover=f"{args.rank:02d}_cover.jpg", cover_variant=args.cover_select, cover_version=time.time_ns() // 1_000_000)
        if args.cover_select != "seedream":
            pack.pop("cover_history_selected", None)
        elif (hit.get("meta") or {}).get("version_id"):
            pack["cover_history_selected"] = hit["meta"]["version_id"]
        print(f"封面：改用已生成的候选 {hit.get('label') or args.cover_select}")
    elif not args.no_cover and (args.cover_t is not None or args.cover_text is not None or args.cover_variant is not None
                              or args.cover_theme is not None or args.cover_mode != "programmatic" or not pack.get("cover")):
        punch = cand["clip_start"] + llm["punch_time"] if llm.get("punch_time", -1) >= 0 else cand["t"]
        if llm.get("cover_time", -1) >= 0:                    # 精排 v4 选的封面帧（有表情 / 关键动作的那一秒）优先于笑点时刻
            punch = cand["clip_start"] + llm["cover_time"]
        t = args.cover_t if args.cover_t is not None else pack.get("cover_t", punch)
        t = min(max(t, a), b - 0.1)
        frame = out_dir / f"{args.rank:02d}_frame.jpg"
        reference = frame
        if args.cover_mode == "seedream" and options["reference"] == "seedream":
            reference = out_dir / f"{args.rank:02d}_cover_seedream_raw.jpg"
            if not reference.is_file():
                reference = out_dir / f"{args.rank:02d}_cover_seedream.jpg"  # Existing covers predate raw-image storage.
            if not reference.is_file():
                print("还没有 Seedream 底图，请先选择直播画面生成一张", file=sys.stderr)
                return 1
            src = "上次 Seedream 生图"
        else:
            src = grab_frame(sess, data, cand, edit, t, frame)
        if not src:
            print("找不到可以截图的视频（录像已移走、也还没有成片 / 预览 / 审核片段）", file=sys.stderr)
            return 1
        reference_check = inspect_reference_frame(reference)
        pack["cover_reference_check"] = dict(reference_check, time=round(t, 2), source=src)
        selected = args.cover_variant or pack.get("cover_variant") or "hero"
        if args.cover_mode == "programmatic" and selected == "seedream":
            selected = "hero"           # 当前是生图封面时点“重画封面”，重画的是程序化封面
        variant_files = []
        cover = out_dir / f"{args.rank:02d}_cover.jpg"
        try:
            if args.cover_mode in ("programmatic", "all"):
                for variant in ("hero", "comic", "abstract"):
                    vf = out_dir / f"{args.rank:02d}_cover_{variant}.jpg"
                    render_theme_cover(frame, vf, concept, pc.get("cover_font") or "Noto Sans CJK SC:bold", variant)
                    variant_files.append({"variant": variant, "file": vf.name, "label": {"hero": "人物海报", "comic": "漫画冲击", "abstract": "抽象事件"}[variant]})
            if args.cover_mode in ("seedream", "all"):
                seed_file = out_dir / f"{args.rank:02d}_cover_seedream.jpg"
                if args.cover_mode == "all" and options["reference"] == "seedream":
                    reference = out_dir / f"{args.rank:02d}_cover_seedream_raw.jpg"
                    if not reference.is_file():
                        reference = out_dir / f"{args.rank:02d}_cover_seedream.jpg"
                    if not reference.is_file():
                        raise SeedreamError("还没有 Seedream 底图，请先选择直播画面生成一张")
                raw = out_dir / f".seedream-{uuid.uuid4().hex}.jpg"
                composed = out_dir / f".seedream-{uuid.uuid4().hex}.jpg"
                cropped_portrait = None
                try:
                    references = reference
                    if portrait is not None:
                        if not portrait.is_file():
                            raise SeedreamError("当前主播的封面形象参考图不存在，请检查 publish.cover_portrait_file")
                        box = pc.get("cover_portrait_crop")
                        if box is not None:
                            cropped_portrait = out_dir / f".seedream-portrait-{uuid.uuid4().hex}.jpg"
                            crop_cover_portrait(portrait, cropped_portrait, box)
                        references = [cropped_portrait or portrait, reference]
                    meta = generate_seedream(prompt, references, raw, model=pc.get("seedream_model"),
                                             endpoint=pc.get("seedream_endpoint"), size=pc.get("seedream_size", "1920x1200"))
                    from PIL import Image
                    with Image.open(raw) as generated:
                        normalized = generated.convert("RGB")
                    normalized.save(raw, "JPEG", quality=95)
                    # Seedream supplies the finished typography and composition. Preserve it unchanged.
                    shutil.copyfile(raw, composed)
                    preserve_legacy_seedream(out_dir, args.rank, pack)
                    history = list(pack.get("cover_history") or [])
                    ocr_check = inspect_cover_text(composed, text)
                    meta.update(reference=options["reference"], supplement=options["supplement"], composition="seedream-integrated-v1",
                                reference_check=reference_check, ocr_check=ocr_check,
                                streamer_portrait_reference=portrait is not None)
                    saved = archive_seedream(out_dir, args.rank, composed, raw, dict(meta, file=f"{args.rank:02d}_cover_seedream.jpg"), history, text)
                    pack["cover_history"] = [saved, *history]
                    pack["cover_history_selected"] = saved["id"]
                    raw.replace(out_dir / f"{args.rank:02d}_cover_seedream_raw.jpg")
                    composed.replace(seed_file)
                except (OSError, ValueError) as e:
                    raise SeedreamError(f"生图结果处理失败：{e}") from e
                finally:
                    raw.unlink(missing_ok=True)
                    composed.unlink(missing_ok=True)
                    if cropped_portrait is not None:
                        cropped_portrait.unlink(missing_ok=True)
                meta.update(file=seed_file.name, reference=options["reference"], supplement=options["supplement"], version_id=saved["id"],
                            raw_file=f"{args.rank:02d}_cover_seedream_raw.jpg")
                pack["cover_seedream_options"] = options
                pack.pop("cover_seedream_error", None)
                variant_files.append({"variant": "seedream", "file": seed_file.name, "label": "Seedream 生图", "meta": meta})
            selected = "seedream" if args.cover_mode == "seedream" else selected
            chosen = next((x for x in variant_files if x["variant"] == selected), variant_files[0])
            shutil.copyfile(out_dir / chosen["file"], cover)
        except SeedreamError as e:
            pack["cover_seedream_error"] = str(e)
            print(f"Seedream 封面失败：{e}", file=sys.stderr)
            if args.cover_mode == "seedream":
                return 1
            if not variant_files:
                return 1
            chosen = variant_files[0]
            selected = chosen["variant"]
            shutil.copyfile(out_dir / chosen["file"], cover)
        except RuntimeError as e:
            print(f"封面生成失败：{e}", file=sys.stderr)
            return 1
        # 这次没重新生成的候选（比如只重画了程序化封面时的生图那张）只要文件还在就保留，审核时还能选
        fresh = {x["variant"] for x in variant_files}
        for old in pack.get("cover_variants") or []:
            if old.get("variant") not in fresh and (out_dir / str(old.get("file") or "")).is_file():
                variant_files.append(old)
        if variant_files:
            chosen = next((x for x in variant_files if x["variant"] == selected), variant_files[0])
            if chosen["variant"] != "seedream":
                pack.pop("cover_history_selected", None)
            pack.update(cover=cover.name, cover_t=round(t, 2), cover_text=text, cover_source=src,
                        cover_variant=chosen["variant"], cover_variants=variant_files, cover_concept=concept,
                        cover_version=time.time_ns() // 1_000_000)
        print(f"封面：{hhmmss(t)} 的画面（来自{src}），主题 {concept['theme']}，构图 {chosen['variant']} → {cover.name}")
    pack["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = pf.with_name(pf.name + ".tmp-" + uuid.uuid4().hex)
    try:
        tmp.write_text(json.dumps(pack, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(pf)
    finally:
        tmp.unlink(missing_ok=True)
    print(json.dumps(pack, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
