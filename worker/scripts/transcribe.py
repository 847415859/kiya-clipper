#!/usr/bin/env python3
"""整场语音转写：把一场直播的所有分段交给百炼识别，生成 output/<场次>/transcript.json。

用法：
  python scripts/transcribe.py /data/rec --latest                   # 最近一场
  python scripts/transcribe.py /data/rec --session 20261110-2000    # 指定场次
  python scripts/transcribe.py /data/rec --latest --model fun-asr   # 换模型（默认读 config 的 asr.model）
  python scripts/transcribe.py /data/rec --latest --dry-run         # 只算时长和费用，不上传不识别

已经有 transcript.json 的场次会跳过（--refresh 强制重做）。转写完成后重跑 screen.py，
候选片段会带上“主播原话”，标注工具和 try_llm.py 也会自动用上。
录像会在 2 天后被同步脚本移走，所以要在下播后尽快转写（监控脚本设置 KIYA_ASR=1 后会自动做）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from clipper.asr import AsrError, estimate_cost, extract_audio, resolve_vocabulary, transcribe_files  # noqa: E402
from clipper.session import find_sessions  # noqa: E402


def load_env() -> None:
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
            if m and m.group(1) not in os.environ and m.group(2):
                os.environ[m.group(1)] = m.group(2).strip('"').strip("'")


def fmt(t: float) -> str:
    t = int(t)
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="录播姬录像目录，或同一场的若干 .xml 文件")
    ap.add_argument("--config", default=str(REPO / "config" / "kiya.yaml"))
    ap.add_argument("--out", default=str(ROOT / "output"))
    ap.add_argument("--latest", action="store_true")
    ap.add_argument("--session")
    ap.add_argument("--model", help="paraformer-v2 / fun-asr，默认读配置 asr.model")
    ap.add_argument("--bucket", help="OSS bucket，默认读 .env 的 OSS_BUCKET")
    ap.add_argument("--refresh", action="store_true", help="已有 transcript.json 也重做")
    ap.add_argument("--dry-run", action="store_true", help="只抽音频算时长和费用")
    args = ap.parse_args()

    load_env()
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    asr_cfg = cfg.get("asr") or {}
    model = args.model or asr_cfg.get("model") or "paraformer-v2"
    key = os.environ.get("DASHSCOPE_API_KEY")
    bucket = args.bucket or os.environ.get("OSS_BUCKET")
    if not args.dry_run and not key:
        print("没有找到 DASHSCOPE_API_KEY（填在仓库根目录 .env）", file=sys.stderr)
        return 1
    if not args.dry_run and not bucket:
        print("没有 OSS bucket：用 --bucket 指定，或在 .env 填 OSS_BUCKET", file=sys.stderr)
        return 1

    vid, vnote = resolve_vocabulary(model, asr_cfg)
    print(f"模型 {model}；热词：" + (f"{vnote} {vid}" if vid else vnote))
    sessions = find_sessions(args.paths, latest=args.latest, name=args.session)
    if not sessions:
        print("没有找到场次", file=sys.stderr)
        return 1
    rc = 0
    for name, segs in sessions:
        out_dir = Path(args.out) / name
        dst = out_dir / "transcript.json"
        print(f"\n== {name}：{len(segs)} 个分段 ==")
        if dst.exists() and not args.refresh:
            print(f"  已有 {dst.name}，跳过（--refresh 重做）")
            continue
        segs_v = [s for s in segs if s.video_path.exists() and s.video_path.stat().st_size > 0]
        if not segs_v:
            print("  找不到录像文件（可能已同步到 OSS 并从本地删除），跳过")
            rc = 1
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="asr-", dir=out_dir) as tmp:
            t0 = time.time()
            files, total = [], 0.0
            for i, s in enumerate(segs_v):
                f = Path(tmp) / f"seg{i:02d}.mp3"
                total += extract_audio(s.video_path, f)
                files.append(f)
            size = sum(f.stat().st_size for f in files) / 1e6
            cost = estimate_cost(model, total)
            print(f"  音频 {fmt(total)}（{size:.0f} MB），抽取用时 {time.time() - t0:.0f} 秒；"
                  f"{model} 预计 ¥{cost if cost is not None else '?'}")
            if args.dry_run:
                continue
            t1 = time.time()
            try:
                sents, ok = transcribe_files(files, [s.offset for s in segs_v], model, key, bucket, asr_cfg)
            except AsrError as e:
                print(f"  转写失败：{e}", file=sys.stderr)
                rc = 1
                continue
        data = {
            "session": name, "model": model, "created_at": time.strftime("%F %T"),
            "duration": round(total, 1), "cost": cost, "vocabulary_id": vid,
            "segments": [{"video": str(s.video_path), "offset": s.offset, "ok": o} for s, o in zip(segs_v, ok)],
            "sentences": sents,
        }
        dst.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with open(out_dir / "transcript.jsonl", "w", encoding="utf-8") as jf:    # 按句一行，给以后的全文检索用
            for snt in sents:
                jf.write(json.dumps({"session": name, **snt}, ensure_ascii=False) + "\n")
        (out_dir / "transcript.txt").write_text(
            "\n".join(f"[{fmt(s['t0'])}] {s['text']}" for s in sents) + "\n", encoding="utf-8")
        chars = sum(len(s["text"]) for s in sents)
        print(f"  完成：{len(sents)} 句、{chars} 字，识别用时 {time.time() - t1:.0f} 秒"
              + ("" if all(ok) else f"；{ok.count(False)} 个分段失败"))
        print(f"  结果：{dst}、transcript.txt、transcript.jsonl")
        print(f"  下一步：python scripts/screen.py {args.paths[0]} --session {name}（候选会带上主播原话）")
        if not all(ok):
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
