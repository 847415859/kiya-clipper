"""Decoded timestamp continuity (not acoustic silence) and stage evidence."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path


def decoded_audio_gaps(path: Path, threshold: float = 0.10) -> list[dict]:
    """Return internal missing-sample intervals; missing/undecodable audio raises.

    ffprobe commonly reports sample_rate on the stream, NOT on each frame.
    Silence contains samples and is deliberately not classified as a gap.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise FileNotFoundError("ffprobe")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "a:0", "-show_frames", "-show_streams",
         "-show_entries", "stream=sample_rate:frame=best_effort_timestamp_time,nb_samples,sample_rate",
         "-of", "json", str(path)], capture_output=True, text=True, check=True, timeout=180,
    )
    if result.stderr.strip():
        raise ValueError("Audio decode errors: " + result.stderr.strip()[:1000])
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    if not streams:
        raise ValueError("No audio stream")
    stream_rate = streams[0].get("sample_rate")
    end = None
    gaps = []
    frames = data.get("frames", [])
    if not frames:
        raise ValueError("No decoded audio frames")
    for frame in frames:
        start = float(frame["best_effort_timestamp_time"])
        samples = int(frame["nb_samples"])
        rate = float(frame.get("sample_rate") or stream_rate or 0)
        if not all(math.isfinite(v) for v in (start, rate)) or samples <= 0 or rate <= 0:
            raise ValueError("Invalid decoded audio timing")
        if end is not None and start - end > threshold:
            gaps.append({"start": round(end, 6), "end": round(start, 6),
                         "duration": round(start - end, 6)})
        end = max(end if end is not None else start, start + samples / rate)
    return gaps


def _sha(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def video_sha256(path: Path) -> str:
    """Hash the encoded video packets, independent of container/audio metadata."""
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:v:0",
         "-c", "copy", "-f", "hash", "-hash", "sha256", "-"],
        capture_output=True, text=True, check=True, timeout=180)
    value = result.stdout.strip()
    if not value.startswith("SHA256="):
        raise ValueError("Missing video packet hash")
    return value.split("=", 1)[1]


def _stage(path: Path | None) -> dict:
    row = {"path": str(path) if path is not None else None, "sha256": None,
           "gaps": [], "status": "unavailable"}
    if path is None:
        row["reason"] = "stage_not_supplied"
        return row
    path = Path(path)
    row["sha256"] = _sha(path)
    try:
        row["gaps"] = decoded_audio_gaps(path)
        row["status"] = "available"
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
        row["reason"] = f"{type(exc).__name__}: {exc}"
    return row


def _ranges(segment: dict) -> list[tuple[float, float]] | None:
    """source_ranges are file-local; offset/duration describe session placement."""
    if "source_ranges" in segment:
        ranges = [(float(a), float(b)) for a, b in segment["source_ranges"]]
    elif segment.get("duration") is not None:
        ranges = [(0.0, float(segment["duration"]))]
    elif segment.get("start") is not None and segment.get("end") is not None:
        offset = float(segment.get("offset", 0))
        ranges = [(float(segment["start"]) - offset, float(segment["end"]) - offset)]
    else:
        return None
    if any(not math.isfinite(a) or not math.isfinite(b) or a < 0 or b <= a for a, b in ranges):
        raise ValueError("Invalid selected source ranges")
    return ranges


def diagnose_segments(segments: list[dict], output_gaps: list[dict] | None = None,
                      cut_gaps: list[dict] | None = None) -> dict:
    sources = []
    for segment in segments:
        row = _stage(Path(segment["video"]))
        row.update(offset=segment.get("offset"), duration=segment.get("duration"))
        try:
            ranges = _ranges(segment)
            row["source_ranges"] = [list(span) for span in ranges] if ranges is not None else None
            if ranges is not None:
                selected = []
                for gap in row["gaps"]:
                    for a, b in ranges:
                        left, right = max(a, gap["start"]), min(b, gap["end"])
                        if right > left:
                            selected.append({"start": round(left, 6), "end": round(right, 6),
                                             "duration": round(right - left, 6)})
                row["gaps"] = selected
        except (ValueError, TypeError) as exc:
            row.update(status="unavailable", reason=str(exc))
        sources.append(row)
    mapping_error = None
    try:
        seams = _segment_boundary_gaps(segments)
    except (ValueError, TypeError, KeyError) as exc:
        seams = []
        mapping_error = str(exc)
    earliest = ("source" if any(row["gaps"] for row in sources) else
                "source_stitch" if seams else "cut" if cut_gaps else
                "render" if output_gaps else "none")
    return {"earliest_stage": earliest, "sources": sources, "segment_boundary_gaps": seams,
            "cut_gaps": cut_gaps or [], "output_gaps": output_gaps or [],
            "mapping_error": mapping_error,
            "status": "review" if mapping_error or not sources or any(row["status"] != "available" for row in sources) else "diagnosed"}


def diagnose_pipeline(source_segments: list[dict], cut_media: Path | None, rendered_media: Path | None) -> dict:
    """Unavailable stages are explicit and always require review, never a clean bill."""
    source = diagnose_segments(source_segments)
    cut, final = _stage(cut_media), _stage(rendered_media)
    earliest = source["earliest_stage"]
    if earliest == "none":
        earliest = "cut" if cut["gaps"] else "render" if final["gaps"] else "none"
    observed = earliest
    # A bad final with an unavailable upstream stage does not prove the renderer
    # introduced the defect. Keep the observation but withhold attribution.
    if earliest in ("cut", "render") and source["status"] == "review":
        earliest = "unknown"
    if earliest == "render" and cut["status"] != "available":
        earliest = "unknown"
    return {**source, "earliest_stage": earliest, "earliest_observed_stage": observed, "cut_gaps": cut["gaps"],
            "output_gaps": final["gaps"], "cut_media": cut["path"], "render_media": final["path"],
            "stages": {"cut": cut, "final": final},
            "status": "review" if source["status"] == "review" or any(
                row["status"] != "available" for row in (cut, final)) else "diagnosed"}


def _segment_boundary_gaps(segments: list[dict]) -> list[dict]:
    # Explicit selected ranges can intentionally omit content. They must not be
    # treated as accidental gaps between capture files.
    ordered = sorted(segments, key=lambda row: float(row.get("offset", row.get("start", 0))))
    seams = []
    for left, right in zip(ordered, ordered[1:]):
        if "source_ranges" in left or "source_ranges" in right:
            continue
        left_end = (float(left["offset"]) + float(left["duration"])
                    if left.get("duration") is not None and left.get("offset") is not None else left.get("end"))
        right_start = right.get("offset", right.get("start"))
        if left_end is not None and right_start is not None and float(right_start) - float(left_end) > 0.1:
            seams.append({"source_range": [float(left_end), float(right_start)],
                          "left": left.get("video"), "right": right.get("video"), "status": "source_gap"})
    return seams
