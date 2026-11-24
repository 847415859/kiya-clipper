"""Analyze complete candidate audio/video and keep source-time evidence for reranking."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from math import isfinite
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .context import danmaku_lines, speech_lines
from .llm import LLMClient, fill, load_prompt, text_part


class Observation(BaseModel):
    start: float
    end: float
    kind: Literal["前因", "触发", "变化", "回应", "结果", "新事件", "其他"]
    description: str = Field(min_length=1, max_length=220)
    modality: Literal["画面", "原声", "字幕", "音画"]


class Unobserved(BaseModel):
    start: float
    end: float
    reason: str = Field(min_length=1, max_length=180)


class WindowOut(BaseModel):
    observations: list[Observation] = Field(max_length=18)
    unobserved: list[Unobserved] = Field(default_factory=list, max_length=12)
    continues_before: bool | None
    continues_after: bool | None


@dataclass(frozen=True)
class Source:
    path: Path
    start: float
    end: float
    origin: float  # 容器起始 PTS。ffmpeg 的 -ss 以容器开头为 0。
    first_audio_visual: float


def make_video_client(settings: dict, cache_dir: Path) -> LLMClient:
    # LLMClient 对普通模型沿用已有 .env 配置。视频线路可复用独立研究凭证。
    return LLMClient(model=settings.get("model") or "qwen3.8-omni-flash",
                     base_url=os.environ.get("QWEN_BASE_URL"), api_key=os.environ.get("QWEN_API_KEY"),
                     cache_dir=cache_dir, temperature=0.7, max_retries=2,
                     request_options={"stream": True, "stream_options": {"include_usage": True},
                                      "modalities": ["text"], "enable_thinking": False, "max_tokens": 2500})


def _probe(args: list[str]) -> dict:
    result = subprocess.run(["ffprobe", "-v", "error", *args, "-of", "json"],
                            capture_output=True, check=True, timeout=30)
    return json.loads(result.stdout)


@lru_cache(maxsize=512)
def _streams(path: Path, size: int, modified_ns: int) -> tuple[float, float, float]:
    data = _probe(["-show_entries", "format=duration,start_time:stream=codec_type,duration,start_time", str(path)])
    info = data["format"]
    origin = float(info.get("start_time", 0))
    starts, ends = [0.0], []
    for media_type in ("video", "audio"):
        stream = next((s for s in data.get("streams", []) if s["codec_type"] == media_type), None)
        if stream is None:
            raise ValueError(f"缺少 {media_type} 流")
        stream_start = float(stream.get("start_time", origin)) - origin
        starts.append(stream_start)
        if stream.get("duration", "N/A") != "N/A":
            ends.append(stream_start + float(stream["duration"]))
    duration = min(ends) if ends else float(info["duration"])
    if not isfinite(duration) or duration <= 0 or not isfinite(origin):
        raise ValueError("无法确定源音画时长")
    return duration, origin, max(starts)


def session_sources(segments: list[dict]) -> list[Source]:
    ordered = sorted(segments, key=lambda s: float(s["offset"]))
    sources = []
    for i, item in enumerate(ordered):
        path, start = Path(item["video"]), float(item["offset"])
        next_start = float(ordered[i + 1]["offset"]) if i + 1 < len(ordered) else float("inf")
        try:
            stat = path.stat()
            duration, origin, first = _streams(path, stat.st_size, stat.st_mtime_ns)
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            continue
        end = min(start + duration, next_start)
        if end > start + first:
            sources.append(Source(path, start, end, origin, first))
    return sources


def missing(start: float, end: float, covered: list[tuple[float, float]]) -> list[list[float]]:
    gaps = []
    cursor = start
    for a, b in sorted(covered):
        if b <= cursor or a >= end:
            continue
        if a - cursor > 0.08:
            gaps.append([round(cursor, 3), round(a, 3)])
        cursor = max(cursor, b)
    if end - cursor > 0.08:
        gaps.append([round(cursor, 3), round(end, 3)])
    return gaps


def _available(start: float, end: float, excluded: list[list[float]]) -> list[tuple[float, float]]:
    result, cursor = [], start
    for a, b in sorted(excluded):
        if a > cursor:
            result.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < end:
        result.append((cursor, end))
    return result


def _windows(start: float, end: float, sources: list[Source], span: float, overlap: float):
    for src in sources:
        a, limit = max(start, src.start + src.first_audio_visual), min(end, src.end)
        while limit - a > 0.08:
            b = min(a + span, limit)
            yield a, b, src
            if b == limit:
                break
            a = b - overlap


def _encode(src: Source, start: float, end: float, target: Path, detail: bool) -> tuple[float, float]:
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-threads", "2",
                    "-ss", f"{start - src.start:.6f}", "-i", str(src.path), "-t", f"{end - start:.6f}",
                    "-map", "0:v:0", "-map", "0:a:0", "-filter_threads", "1",
                    "-vf", ("scale='min(960,iw)':-2" if detail else "scale='min(640,iw)':-2"),
                    "-c:v", "libx264", "-crf", "27" if detail else "33",
                    "-preset", "veryfast", "-threads", "2", "-c:a", "aac", "-b:a", "80k",
                    "-movflags", "+faststart", str(target)], capture_output=True, check=True, timeout=120)
    stat = target.stat()
    if stat.st_size > 7_000_000:
        raise ValueError("窗口编码后超过视频请求大小上限")
    duration, _, first = _streams(target, stat.st_size, stat.st_mtime_ns)
    return min(duration, end - start), first


def _source_pts(src: Source, start: float, end: float) -> list[float]:
    local = start - src.start + src.origin
    last = end - src.start + src.origin
    try:
        data = _probe(["-select_streams", "v:0", "-read_intervals", f"{local:.6f}%{last:.6f}",
                       "-show_frames", "-show_entries", "frame=best_effort_timestamp_time", str(src.path)])
        times = [float(f["best_effort_timestamp_time"]) for f in data.get("frames", [])
                 if "best_effort_timestamp_time" in f]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return []
    return sorted({round(src.start + t - src.origin, 6) for t in times if local <= t < last})


def _valid_interval(start: float, end: float, duration: float) -> bool:
    return isfinite(start) and isfinite(end) and 0 <= start < min(end, duration) and end <= duration + 0.08


def analyze_candidate(candidate: dict, sources: list[Source], sentences: list, events: list,
                      delay: float, settings: dict, client: LLMClient, material_end: float | None) -> dict:
    origin, end = float(candidate["clip_start"]), float(candidate["clip_end"])
    span = min(60.0, max(10.0, float(settings.get("window_seconds", 55))))
    overlap = min(span / 2, max(0.0, float(settings.get("overlap_seconds", 5))))
    low = max(0.0, origin - float(settings.get("context_before", 120)))
    high = min(material_end if material_end is not None else max((s.end for s in sources), default=end),
               end + float(settings.get("context_after", 240)))
    version, system, user = load_prompt("rerank_video_observe_v1")
    windows, observations, covered = [], [], []
    planned = [origin, end]
    open_context = []

    def review(a: float, b: float, src: Source, phase: str):
        detail = phase == "detail"
        row = {"start": round(a, 3), "end": round(b, 3), "phase": phase, "status": "failed",
               "source_file": str(src.path), "source_offset": src.start}
        windows.append(row)
        try:
            with tempfile.TemporaryDirectory(prefix="kiya-rerank-") as tmp:
                proxy = Path(tmp) / "window.mp4"
                duration, first = _encode(src, a, b, proxy, detail)
                if duration - first <= 0.08:
                    raise ValueError("可解码音画不足")
                video = {"type": "video_url", "video_url": {"url": "data:;base64," +
                         base64.b64encode(proxy.read_bytes()).decode("ascii")}}
                dm, gifts = danmaku_lines(events, a, a + duration, a, delay)
                prompt = fill(user, duration=f"{duration:.3f}", task=(
                    "检查触发、回应、字幕变化和声尾，标出无法确定的部分。" if detail else
                    "从头到尾观察，记录事件和窗口两端是否仍在同一互动中。"),
                    speech=speech_lines(sentences, a, a + duration, a), danmaku=dm, gifts=gifts)
                result = client.chat_json([{"role": "system", "content": system},
                                           {"role": "user", "content": [video, text_part(prompt)]}],
                                          WindowOut, tag=version)
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
            row["error"] = str(exc)[:240]
            return row
        row.update(cached=result.cached, usage=result.usage, attempts=result.attempts)
        if result.data is None:
            row["error"] = (result.error or "没有模型响应")[:240]
            return row
        data = result.data
        if not (data.observations or data.unobserved) or any(
            not _valid_interval(r.start, r.end, duration) for r in [*data.observations, *data.unobserved]
        ):
            row["error"] = "音画观察为空或时间超出输入窗口"
            return row
        actual_end = a + duration
        excluded = []
        for item in data.unobserved:
            excluded.append([a + item.start, min(actual_end, a + item.end)])
        row.update(status="partial" if excluded or first > 0.08 or b - actual_end > 0.08 else "complete",
                   available_start=round(a + first, 3), available_end=round(actual_end, 3),
                   continues_before=data.continues_before,
                   continues_after=data.continues_after, unobserved=[
                       {"start": round(a + r.start, 3), "end": round(min(actual_end, a + r.end), 3), "reason": r.reason}
                       for r in data.unobserved])
        covered.extend(_available(a + first, actual_end, excluded))
        frame_pts = _source_pts(src, a, actual_end) if detail else []
        if detail:
            row["source_frame_pts"] = frame_pts
        for item in data.observations:
            t0, t1 = a + item.start, min(actual_end, a + item.end)
            observed = {"start": round(t0, 3), "end": round(t1, 3), "kind": item.kind,
                        "modality": item.modality, "description": item.description,
                        "precision": "模型音画区间", "window": len(windows)}
            if frame_pts and item.modality != "原声":
                observed["nearest_source_frame_pts"] = [min(frame_pts, key=lambda t: abs(t - edge))
                                                        for edge in (t0, t1)]
            observations.append(observed)
        return row

    def scan(a: float, b: float, phase: str) -> list[dict]:
        return [review(x, y, s, phase) for x, y, s in _windows(a, b, sources, span, overlap)]

    initial = scan(origin, end, "candidate")
    for edge, marker, limit, direction in [(0, "continues_before", low, -1), (-1, "continues_after", high, 1)]:
        adjacent = initial[edge] if initial else {}
        cursor = origin if direction < 0 else end
        continuation = adjacent.get(marker)
        # 未知时只探索相邻窗一次。继续延伸需要模型观察到同一事件仍在进行。
        while continuation is not False and adjacent.get("status") in {"complete", "partial"}:
            step = span - overlap
            target = max(limit, cursor - step) if direction < 0 else min(limit, cursor + step)
            if abs(target - cursor) <= 0.08:
                break
            a, b = sorted((cursor, target))
            if direction < 0:
                planned[0] = a
                b = min(end, b + overlap)
            else:
                planned[1] = b
                a = max(origin, a - overlap)
            nearby = scan(a, b, "context")
            adjacent = nearby[edge] if nearby else {}
            cursor, continuation = target, adjacent.get(marker)
            if continuation is not True:
                break
        if continuation is not False:
            open_context.append("前因" if direction < 0 else "收束")

    # 从相邻窗的观察中挑关键点。短窗用更高分辨率并记录源画面帧坐标。
    changes = [r for r in observations if r["kind"] in {"触发", "回应", "结果", "新事件"}]
    anchors = [r["start"] for r in changes if r["kind"] == "触发"][:1]
    anchors += [r["end"] for r in sorted(changes, key=lambda r: r["end"], reverse=True)
                if r["kind"] in {"回应", "结果"}][:2]
    anchors += [r["start"] for r in changes if r["kind"] == "新事件"][:1]
    detail_centers = []
    for t in anchors:
        if any(abs(t - old) < 3 for old in detail_centers):
            continue
        detail_centers.append(t)
        for a, b, src in _windows(max(planned[0], t - 3), min(planned[1], t + 3), sources, 8, 0):
            review(a, b, src, "detail")
    merged = []
    for item in sorted(observations, key=lambda r: (r["start"], r["end"])):
        duplicate = next((r for r in reversed(merged) if r["end"] >= item["start"]
                          and all(r[k] == item[k] for k in ("kind", "modality", "description"))), None)
        if duplicate is None:
            merged.append({**item, "windows": [item["window"]]})
        else:
            duplicate["windows"].append(item["window"])
            duplicate["end"] = max(duplicate["end"], item["end"])
    for i, item in enumerate(merged, 1):
        item["id"] = f"AV{i:03d}"
    recorded = [(item["start"], item["end"]) for item in merged]
    return {"model": client.model, "prompt_version": version, "planned": planned, "windows": windows,
            "open_context": open_context,
            "observations": merged, "covered": covered, "gaps": missing(*planned, covered),
            "observation_gaps": missing(*planned, recorded)}


def evidence_text(report: dict, origin: float, start: float, end: float, required=()) -> str:
    required = set(required)
    rows = []
    for row in report.get("observations", []):
        if start <= row["start"] and row["end"] <= end:
            flag = "，必保" if row["id"] in required else ""
            rows.append(f"[{row['id']} {row['start']-origin:.2f}–{row['end']-origin:.2f}s {row['kind']}/{row['modality']}{flag}] {row['description']}")
    gaps = [(max(a, start), min(b, end)) for a, b in report.get("gaps", []) if a < end and b > start]
    rows += [f"[{a-origin:.2f}–{b-origin:.2f}s] 音画证据缺口" for a, b in gaps]
    return "\n".join(rows) or "（当前范围没有连续音画观察）"
