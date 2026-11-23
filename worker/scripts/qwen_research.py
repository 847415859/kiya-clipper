"""Qwen visual/audio research entry point. --check/--dry-run make no API calls."""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clipper.qwen_multimodal import (QwenAnalysisError, QwenResearchClient, atomic_json, file_hash, media_part)  # noqa: E402


def prepare(video: Path, start: float, duration: float, output: Path, mode: str) -> tuple[dict, int]:
    if not math.isfinite(start) or not math.isfinite(duration) or start < 0 or not 0 < duration <= 60:
        raise ValueError("起点须为非负有限数；时长须大于 0 且不超过 60 秒")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise ValueError("需要可执行的 ffmpeg 和 ffprobe")
    try:
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                                "-of", "json", str(video)], check=True, capture_output=True, timeout=30)
        info = json.loads(probe.stdout)
        total = float(info["format"]["duration"])
    except (subprocess.SubprocessError, ValueError, KeyError):
        raise ValueError("无法确认视频时长和媒体流") from None
    if not math.isfinite(total) or start + duration > total + .001:
        raise ValueError("请求区间超出视频时长，请缩短区间")
    types = {x.get("codec_type") for x in info.get("streams", [])}
    if "video" not in types:
        raise ValueError("输入没有视频流")
    if mode in {"audio", "both"} and "audio" not in types:
        raise ValueError("输入无音轨，不能进行实际听音研究；可选择 --mode visual")
    output.mkdir(parents=True, exist_ok=True)
    files = {}
    for modality in (["visual", "audio"] if mode == "both" else [mode]):
        dst = output / ("visual.mp4" if modality == "visual" else "audio.wav")
        cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
               "-ss", str(start), "-i", str(video), "-t", str(duration)]
        if modality == "visual":
            cmd += ["-map", "0:v:0", "-an", "-vf", "scale=w=1280:h=720:force_original_aspect_ratio=decrease:force_divisible_by=2",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "24", "-maxrate", "600k",
                    "-bufsize", "1200k", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
        else:
            cmd += ["-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"]
        try:
            subprocess.run(cmd + [str(dst)], check=True, capture_output=True, timeout=180)
        except subprocess.SubprocessError:
            raise ValueError("媒体区间准备失败；未发起模型调用") from None
        media_part(dst, modality)  # Size/type preflight for all media before any paid call.
        files[modality] = {"file": dst.name, "sha256": file_hash(dst), "bytes": dst.stat().st_size}
    return files, round(duration * 1000)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check", action="store_true", help="只检查配置是否存在，不显示密钥或调用模型")
    p.add_argument("--video", type=Path)
    p.add_argument("--start", type=float, default=0)
    p.add_argument("--duration", type=float, default=20)
    p.add_argument("--mode", choices=["visual", "audio", "both"], default="both")
    p.add_argument("--dry-run", action="store_true", help="准备媒体和清单，不调用模型")
    p.add_argument("--refresh", action="store_true", help="忽略匹配缓存，会产生新的模型调用")
    p.add_argument("--output", type=Path, default=ROOT / "output/_reference_study/qwen_runs")
    p.add_argument("--video-id", help="完整 BV 号，用于请求账本关联")
    p.add_argument("--analysis-version", default="qwen_research_v2")
    p.add_argument("--cache-dir", type=Path)
    args = p.parse_args(argv)
    modalities = ["visual", "audio"] if args.mode == "both" else [args.mode]
    try:
        clients = {m: QwenResearchClient(m) for m in modalities}
        config = [c.configuration_status() for c in clients.values()]
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if args.check:
        print(json.dumps({"configuration": config, "external_calls": 0}, ensure_ascii=False))
        return 0 if all(c["credential_present"] for c in config) else 2
    if not args.video or not args.video.is_file():
        p.error("需要存在的 --video 文件")
    if not args.dry_run and not all(c["credential_present"] for c in config):
        print("缺少 QWEN_API_KEY 或 DASHSCOPE_API_KEY。请在 .env/环境变量中配置，勿放入命令参数。", file=sys.stderr)
        return 2
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    run = args.output.resolve() / run_id
    try:
        files, duration_ms = prepare(args.video.resolve(), args.start, args.duration, run, args.mode)
    except (ValueError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 2
    start_ms = round(args.start * 1000)
    report = {"run_id": run_id, "video_id": args.video_id, "analysis_version": args.analysis_version,
              "source": str(args.video.resolve()), "source_sha256": file_hash(args.video), "start_ms": start_ms,
              "duration_ms": duration_ms, "media": files, "configuration": config,
              "dry_run": args.dry_run, "results": {}, "errors": {},
              "human_confirmed": False, "production_approved": False,
              "semantic_accuracy": "not_independently_verified",
              "status": "prepared_no_api_calls"}
    manifest = run / "run.json"
    atomic_json(manifest, report)
    if not args.dry_run:
        for modality, client in clients.items():
            try:
                result = client.analyze(
                    run / files[modality]["file"], start_ms=start_ms, duration_ms=duration_ms,
                    cache_dir=(args.cache_dir.resolve() if args.cache_dir else args.output.resolve() / "cache"), refresh=args.refresh)
                report["results"][modality] = result
                report.setdefault("request_attempts", {})[modality] = {
                    "attempted": not bool(result.get("cached")),
                    "phase": "cache_hit" if result.get("cached") else "completed",
                    "usage": result.get("usage", {}),
                    "usage_status": "reported" if result.get("usage") else "unknown",
                    "failure_detail": {},
                    "billable_status": "cached" if result.get("cached") else "reported_usage",
                }
            except QwenAnalysisError as e:
                report["errors"][modality] = str(e)
                report.setdefault("request_attempts", {})[modality] = {
                    "attempted": True,
                    "phase": e.phase,
                    "http_status": e.http_status,
                    "usage": e.usage,
                    "usage_status": "reported" if e.usage else "unknown",
                    "failure_detail": e.failure_detail,
                    "billable_status": "unknown_possible_billable",
                }
            except (RuntimeError, ValueError, OSError) as e:
                report["errors"][modality] = str(e)
                report.setdefault("request_attempts", {})[modality] = {
                    "attempted": True,
                    "phase": "preflight_or_unclassified",
                    "usage": {},
                    "usage_status": "unknown",
                    "billable_status": "unknown",
                }
            report["status"] = "api_schema_validated_needs_review" if (
                len(report["results"]) == len(clients)) else "partial_or_failed"
            atomic_json(manifest, report)
        atomic_json(run / "capability_probe.json", {
            "run_id": run_id, "source_report": "run.json",
            "modalities": {m: {
                "model": clients[m].model,
                "input_type": "video_url" if m == "visual" else "input_audio",
                "status": "api_schema_validated" if m in report["results"] else "failed",
                "cached": report["results"].get(m, {}).get("cached", False),
                "error": report["errors"].get(m),
            } for m in modalities},
            "semantic_accuracy": "not_independently_verified",
            "human_confirmed": False, "production_approved": False,
        })
    print(json.dumps({"report": str(manifest), "status": report["status"],
                      "completed_modalities": list(report["results"]),
                      "errors": report["errors"]}, ensure_ascii=False))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
