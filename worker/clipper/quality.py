"""成片质量检查与质检报告。

质检是可重复的程序检查，不修改成片，也不阻断渲染。报告保存每个检查的
状态和证据，供人工审核与后续校准使用。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .boiler import business_cuts
from .edit import density_problems, missing_assets, validate_edit
from .editdiff import diff_edit


@dataclass
class QualityIssue:
    code: str
    severity: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "severity": self.severity, "message": self.message, "details": self.details}


@dataclass
class QualityReport:
    rank: int
    media: str = ""
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: list[QualityIssue] = field(default_factory=list)
    warnings: list[QualityIssue] = field(default_factory=list)
    edit_diff: dict[str, Any] | None = None
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    @property
    def ok(self) -> bool:
        return not self.errors

    def issue(self, code: str, severity: str, message: str, **details: Any) -> None:
        item = QualityIssue(code, severity, message, details)
        (self.errors if severity == "error" else self.warnings).append(item)

    def check(self, name: str, status: str, **details: Any) -> None:
        self.checks[name] = {"status": status, **details}

    def as_dict(self) -> dict[str, Any]:
        return {"rank": self.rank, "media": self.media, "ok": self.ok,
                "generated_at": self.generated_at,
                "checks": self.checks,
                "errors": [x.as_dict() for x in self.errors],
                "warnings": [x.as_dict() for x in self.warnings],
                "edit_diff": self.edit_diff}


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, ValueError):
        return None


def _tool(name: str) -> str | None:
    return shutil.which(name)


def probe(path: Path) -> dict[str, Any] | None:
    ffprobe = _tool("ffprobe")
    if not ffprobe:
        return None
    try:
        r = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                           capture_output=True, text=True, timeout=60)
        if r.returncode:
            return None
        return json.loads(r.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "", "N/A") else None
    except (TypeError, ValueError):
        return None


def _duration(info: dict[str, Any] | None) -> float | None:
    if not info:
        return None
    return _number((info.get("format") or {}).get("duration"))


def _streams(info: dict[str, Any] | None, kind: str) -> list[dict[str, Any]]:
    return [s for s in (info or {}).get("streams", []) if s.get("codec_type") == kind]


def _media_checks(report: QualityReport, media: Path, expected_duration: float | None = None) -> dict[str, Any] | None:
    if not media.exists() or media.stat().st_size <= 0:
        report.issue("file_missing", "error", "成片文件不存在或为空", path=str(media))
        report.check("file_integrity", "error", path=str(media), bytes=0)
        return None
    info = probe(media)
    if info is None:
        report.issue("file_unreadable", "error", "ffprobe 无法读取成片", path=str(media))
        report.check("file_integrity", "error", path=str(media), bytes=media.stat().st_size)
        return None
    vs, aud = _streams(info, "video"), _streams(info, "audio")
    dur = _duration(info)
    report.check("file_integrity", "pass", path=str(media), bytes=media.stat().st_size,
                 video_streams=len(vs), audio_streams=len(aud), duration=dur)
    if not vs:
        report.issue("video_missing", "error", "成片没有视频流")
    if not aud:
        report.issue("audio_missing", "error", "成片没有音频流")
    if dur is None or dur <= 0:
        report.issue("duration_missing", "error", "成片时长无效")
    if expected_duration is not None and dur is not None:
        delta = abs(dur - expected_duration)
        status = "pass" if delta <= 0.75 else "warning" if delta <= 2 else "error"
        report.check("duration", status, actual=round(dur, 3), expected=round(expected_duration, 3), delta=round(delta, 3))
        if status != "pass":
            report.issue("duration_mismatch", status, f"成片时长与预期相差 {delta:.2f} 秒",
                         actual=round(dur, 3), expected=round(expected_duration, 3))
    else:
        report.check("duration", "pass" if dur else "error", actual=dur)
    if vs and aud:
        vd, ad = _number(vs[0].get("duration")), _number(aud[0].get("duration"))
        v0, a0 = _number(vs[0].get("start_time")) or 0.0, _number(aud[0].get("start_time")) or 0.0
        duration_delta = abs(vd - ad) if vd is not None and ad is not None else None
        start_delta = abs(v0 - a0)
        delta = max(x for x in (duration_delta, start_delta) if x is not None) if duration_delta is not None else start_delta
        status = "pass" if delta <= 0.25 else "warning" if delta <= 1 else "error"
        report.check("av_sync", status, video_duration=vd, audio_duration=ad, duration_delta=duration_delta,
                     video_start=v0, audio_start=a0, start_delta=start_delta)
        if status != "pass":
            report.issue("av_duration_mismatch", status, f"音画起止或时长相差 {delta:.2f} 秒",
                         video_duration=vd, audio_duration=ad, start_delta=start_delta)
    else:
        report.check("av_sync", "error", video_streams=len(vs), audio_streams=len(aud))
    return {"info": info, "duration": dur, "video": vs, "audio": aud}


def _black_frames(report: QualityReport, media: Path) -> None:
    ffmpeg = _tool("ffmpeg")
    if not ffmpeg:
        report.check("black_frames", "warning", available=False)
        report.issue("ffmpeg_missing", "warning", "找不到 ffmpeg，跳过黑帧检查")
        return
    try:
        r = subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-i", str(media), "-vf",
                            "blackdetect=d=0.15:pic_th=0.98", "-an", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=180)
        text = (r.stderr or "")
    except (OSError, subprocess.SubprocessError):
        report.check("black_frames", "warning", available=False)
        report.issue("black_check_failed", "warning", "黑帧检查执行失败")
        return
    spans = [{"start": float(a), "end": float(b), "duration": float(b) - float(a)}
             for a, b in re.findall(r"black_start:\s*([0-9.]+).*?black_end:\s*([0-9.]+)", text)]
    long_spans = [x for x in spans if x["duration"] >= 0.5]
    duration = _duration(probe(media))
    edge = [x for x in long_spans if x["start"] <= 0.2 or (duration is not None and x["end"] >= duration - 0.2)]
    internal = [x for x in long_spans if x not in edge]
    status = "error" if any(x["duration"] >= 1.5 for x in edge) else "warning" if long_spans else "pass"
    report.check("black_frames", status, spans=spans)
    if edge and status != "pass":
        report.issue("black_frame_edge", status, "成片开头或结尾存在较长黑帧", spans=edge)
    if internal:
        report.issue("black_frame_internal", "warning", "成片中间存在黑帧", spans=internal)


def _expected_window(session_dir: Path, rank: int, candidate: dict[str, Any] | None, edit: dict[str, Any] | None) -> tuple[float, float, float | None]:
    windows = _load(session_dir / "clips" / "windows.json") or {}
    w = windows.get(f"{rank:02d}") or windows.get(str(rank)) or {}
    if edit and isinstance(edit.get("window"), dict):
        a, b = float(edit["window"]["start"]), float(edit["window"]["end"])
        trim = edit.get("trim") or {}
        start = a + float(trim.get("start", 0))
        end = a + float(trim.get("end", b - a))
        expected = max(0.0, end - start)
        transcript = _load(session_dir / "transcript.json") or {}
        removed = business_cuts(transcript.get("sentences") or [], start, end)
        expected -= sum(float(q["end"]) - float(q["start"]) for q in removed)
        return start, end, max(0.0, expected)
    if w:
        return float(w.get("start", 0)), float(w.get("end", 0)), _number(w.get("output_duration"))
    if candidate:
        a, b = float(candidate.get("clip_start", 0)), float(candidate.get("clip_end", 0))
        return a, b, b - a
    return 0.0, 0.0, None


def _subtitle_check(report: QualityReport, session_dir: Path, rank: int, edit: dict[str, Any] | None, duration: float | None) -> None:
    enabled = bool((edit or {}).get("subtitles", {}).get("enabled", False))
    if not enabled:
        report.check("subtitles", "skip", enabled=False)
        return
    props = session_dir / "render" / f"job_{rank:02d}" / "props.json"
    data = _load(props)
    if data and isinstance(data.get("subtitles"), dict) and data["subtitles"].get("enabled"):
        events = data["subtitles"].get("events") or []
        bad = [e for e in events if _number(e.get("t0")) is None or _number(e.get("t1")) is None or e["t1"] <= e["t0"]
               or (duration is not None and (e["t0"] < -0.05 or e["t1"] > duration + 0.05))]
        status = "error" if bad else "pass"
        report.check("subtitles", status, events=len(events), invalid=len(bad), props=str(props))
        if bad:
            report.issue("subtitle_time_invalid", "error", "字幕时间超出成片范围或无效", count=len(bad))
        return
    trans = _load(session_dir / "transcript.json")
    sidecars = list((session_dir / "clips").glob(f"{rank:02d}_*.srt")) + list((session_dir / "clips").glob(f"{rank:02d}_*.ass"))
    if trans and trans.get("sentences") and sidecars:
        report.check("subtitles", "pass", sidecars=[str(x) for x in sidecars])
    elif trans and trans.get("sentences"):
        report.check("subtitles", "warning", sidecars=[])
        report.issue("subtitle_missing", "warning", "已启用字幕但没有找到字幕事件或 sidecar 文件")
    else:
        report.check("subtitles", "warning", reason="没有转写")
        report.issue("transcript_missing", "warning", "已启用字幕但场次没有转写")


def _business_check(report: QualityReport, session_dir: Path, rank: int, start: float, end: float) -> None:
    transcript = _load(session_dir / "transcript.json") or {}
    sentences = transcript.get("sentences") or []
    if not sentences or end <= start:
        report.check("business_speech", "skip", reason="没有转写或范围")
        return
    hits = business_cuts(sentences, start, end)
    windows = _load(session_dir / "clips" / "windows.json") or {}
    w = windows.get(f"{rank:02d}") or windows.get(str(rank)) or {}
    removed = w.get("removed") or []
    uncovered = [h for h in hits if not any(float(q.get("start", 0)) <= h["start"] + 0.1 and float(q.get("end", 0)) >= h["end"] - 0.1 for q in removed)]
    status = "warning" if uncovered else "pass"
    report.check("business_speech", status, detected=len(hits), removed=len(removed), uncovered=len(uncovered))
    if uncovered:
        report.issue("business_speech_left", "warning", "检测到可能未删除的营业话", spans=uncovered)


def _edit_check(report: QualityReport, session_dir: Path, rank: int, edit: dict[str, Any] | None) -> None:
    if not edit:
        report.check("effects", "skip", reason="没有剪辑脚本")
        return
    try:
        length = float((edit.get("window") or {}).get("end", 0)) - float((edit.get("window") or {}).get("start", 0)) if edit.get("window") else float((edit.get("trim") or {}).get("end", 0))
        normalized = validate_edit(edit, max(length, 1))
        problems = density_problems(normalized)
        missing = missing_assets(normalized, Path(__file__).resolve().parents[2] / "assets" / "stickers")
        report.check("effects", "warning" if problems or missing else "pass", count=len(normalized.get("items", [])), density=problems, missing_assets=missing)
        for p in problems:
            report.issue("effect_density", "warning", p)
        for asset in missing:
            report.issue("asset_missing", "warning", f"贴图素材不存在：{asset}", asset=asset)
    except (TypeError, ValueError) as e:
        report.check("effects", "error", error=str(e))
        report.issue("edit_invalid", "error", f"剪辑脚本校验失败：{e}")


def _find_media(session_dir: Path, rank: int, final: Path | None) -> Path | None:
    if final:
        return final
    found = sorted((session_dir / "final").glob(f"{rank:02d}_*.mp4"))
    if found:
        return found[-1]
    found = sorted((session_dir / "clips").glob(f"{rank:02d}_*.mp4"))
    return found[-1] if found else None


def quality_report(session_dir: Path, rank: int, final: Path | None = None,
                   candidate: dict[str, Any] | None = None, edit: dict[str, Any] | None = None) -> QualityReport:
    report = QualityReport(rank=rank)
    media = _find_media(session_dir, rank, final)
    report.media = str(media) if media else ""
    start, end, expected = _expected_window(session_dir, rank, candidate, edit)
    media_info = _media_checks(report, media, expected) if media else _media_checks(report, session_dir / "final" / f"{rank:02d}.mp4", expected)
    edit_file = session_dir / "edits" / f"{rank:02d}.json"
    ai_file = session_dir / "edits" / f"{rank:02d}.ai.json"
    props_file = session_dir / "render" / f"job_{rank:02d}" / "props.json"
    report.check("artifacts", "pass" if edit_file.exists() else "warning",
                 edit=str(edit_file), edit_exists=edit_file.exists(), ai_exists=ai_file.exists(), props_exists=props_file.exists())
    if not edit_file.exists():
        report.issue("edit_file_missing", "warning", "没有找到人工剪辑脚本", path=str(edit_file))
    if media:
        _black_frames(report, media)
    else:
        report.check("black_frames", "skip", reason="没有成片文件")
    _subtitle_check(report, session_dir, rank, edit, media_info.get("duration") if media_info else None)
    _business_check(report, session_dir, rank, start, end)
    _edit_check(report, session_dir, rank, edit)
    human_file = edit_file
    ai = _load(ai_file)
    human = _load(human_file)
    if ai and human and human.get("human_saved_at"):
        try:
            report.edit_diff = diff_edit(ai.get("edit") or ai, human)
            report.check("edit_diff", "pass", changed=bool(report.edit_diff.get("changed")))
        except (KeyError, TypeError, ValueError) as e:
            report.check("edit_diff", "warning", error=str(e))
            report.issue("edit_diff_failed", "warning", f"AI 与人工脚本差异无法计算：{e}")
    else:
        report.check("edit_diff", "skip", reason="缺少 AI 初稿或人工终稿")
    return report


def write_report(report: QualityReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
