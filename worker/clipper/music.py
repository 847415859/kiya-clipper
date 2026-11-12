"""曲库：assets/music/tracks.json 登记每首曲子，授权说明（license）必填，没填不能登记、也不能用。

曲子文件由你自己放进 assets/music（系统不下载、不自带任何音乐）。鼓点检测结果存 assets/music/beats/<文件名>.json。
环境变量 KIYA_MUSIC 可改目录（测试用）。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXTS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")


class MusicError(ValueError):
    pass


def music_dir() -> Path:
    return Path(os.environ.get("KIYA_MUSIC") or REPO / "assets" / "music")


def _read(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
    except (OSError, ValueError):
        return default


def load_tracks() -> list[dict]:
    return [t for t in (_read(music_dir() / "tracks.json", {}) or {}).get("tracks", []) if isinstance(t, dict) and t.get("id")]


def save_tracks(tracks: list[dict]) -> None:
    d = music_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "tracks.json.tmp"
    tmp.write_text(json.dumps({"tracks": tracks}, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(d / "tracks.json")


def get_track(track_id: str) -> dict | None:
    return next((t for t in load_tracks() if t["id"] == track_id), None)


def track_file(track: dict) -> Path:
    return music_dir() / track["file"]


def beats_path(track: dict) -> Path:
    return music_dir() / "beats" / (track["file"] + ".json")


def load_beats(track_id: str) -> dict | None:
    t = get_track(track_id)
    return _read(beats_path(t), None) if t else None


def register(filename: str, title: str, license_text: str, source: str = "", mood: str = "") -> dict:
    """登记一首已经放在 assets/music 里的曲子。授权说明必填。"""
    if (license_text or "").strip() == "" or len(license_text.strip()) < 4:
        raise MusicError("必须写清授权说明（例如“购买自 XX，商用授权，订单号 123”或“CC0，来源链接 …”），没有授权的曲子不能登记")
    name = Path(filename).name
    if Path(name).suffix.lower() not in EXTS:
        raise MusicError(f"只支持 {' '.join(EXTS)}")
    if not (music_dir() / name).is_file():
        raise MusicError(f"{music_dir()} 里没有 {name}，先把文件放进去")
    tid = re.sub(r"[^\w\-]+", "_", Path(name).stem, flags=re.U).strip("_")[:60] or "track"
    tracks = [t for t in load_tracks() if t["id"] != tid]
    t = {"id": tid, "file": name, "title": (title or Path(name).stem).strip()[:80], "license": license_text.strip()[:300],
         "source": (source or "").strip()[:300], "mood": (mood or "").strip()[:40], "added_at": time.strftime("%Y-%m-%d")}
    tracks.append(t)
    save_tracks(tracks)
    return t


def remove(track_id: str) -> bool:
    tracks = load_tracks()
    keep = [t for t in tracks if t["id"] != track_id]
    if len(keep) == len(tracks):
        return False
    save_tracks(keep)
    return True


def analyze(track: dict) -> dict:
    """跑鼓点检测并保存。"""
    from .beats import detect_beats
    res = detect_beats(track_file(track))
    p = beats_path(track)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    return res


def summary() -> list[dict]:
    """给后台列表用：不含完整鼓点数组。"""
    out = []
    for t in load_tracks():
        b = _read(beats_path(t), None) or {}
        out.append({**t, "exists": track_file(t).is_file(), "analyzed": bool(b), "bpm": b.get("bpm"),
                    "confidence": b.get("confidence"), "snap_ok": bool(b.get("snap_ok")), "duration": b.get("duration")})
    return out


def pick_track(rank: int, hint: str = "") -> dict | None:
    """AI 初稿自动配 BGM：只在能用的曲子里选（文件在、做过鼓点检测、节拍清晰）。mood 里写了和 hint（精排的类型）
    相同的词优先，否则按序号轮换，免得每条都是同一首。曲库为空或没有可用的曲子返回 None（不配 BGM）。"""
    ok = []
    for t in load_tracks():
        b = _read(beats_path(t), None) or {}
        if track_file(t).is_file() and b.get("snap_ok"):
            ok.append(t)
    if not ok:
        return None
    ok.sort(key=lambda t: t["id"])
    hit = [t for t in ok if hint and hint in (t.get("mood") or "")]
    pool = hit or ok
    return pool[(rank - 1) % len(pool)]
