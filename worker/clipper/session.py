"""场次识别：把录像目录里的分段文件按开播时间分成一场一场。"""
from __future__ import annotations

import time
from datetime import timedelta
from pathlib import Path

from .danmaku import Segment, load_session, parse_xml


def collect_xml(paths: list[str | Path]) -> list[Path]:
    files: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            files += sorted(p.rglob("*.xml"))
        elif p.suffix.lower() == ".xml":
            files.append(p)
    # 录播姬的配置文件也可能是 xml 之外的名字，这里只排除明显不是弹幕的
    return [f for f in files if not f.name.startswith(".")]


def group_sessions(files: list[Path], gap_hours: float = 2.0) -> list[list[Path]]:
    """相邻分段开始时间相差超过 gap_hours 视为新的一场。没有开始时间的文件整体算一场。"""
    metas = [(parse_xml(f).start_time, f) for f in files]
    with_time = sorted([m for m in metas if m[0]], key=lambda m: m[0])
    no_time = [m[1] for m in metas if not m[0]]
    sessions: list[list[Path]] = []
    prev = None
    for st, f in with_time:
        if prev is None or st - prev > timedelta(hours=gap_hours):
            sessions.append([])
        sessions[-1].append(f)
        prev = st
    if no_time:
        sessions.append(sorted(no_time))
    return sessions


def session_name(segs: list[Segment]) -> str:
    st = segs[0].start_time
    return st.strftime("%Y%m%d-%H%M") if st else segs[0].xml_path.stem[:40]


def find_sessions(paths: list[str | Path], latest: bool = False, name: str | None = None,
                  gap_hours: float = 2.0) -> list[tuple[str, list[Segment]]]:
    """返回 [(场次名, 分段列表)]；latest 只要最近一场，name 只要指定场次。"""
    files = collect_xml(paths)
    groups = group_sessions(files, gap_hours) if files else []
    out = []
    for g in groups:
        segs = load_session(g)
        out.append((session_name(segs), segs))
    if name:
        out = [s for s in out if s[0] == name]
    if latest:
        out = out[-1:]
    return out


def drop_active(segs: list[Segment], quiet_seconds: float = 120.0, now: float | None = None) -> list[Segment]:
    """边播边筛用：去掉末尾还在写的分段（视频或弹幕文件最近 quiet_seconds 秒内改过）。

    录播姬只会往最新的分段里写，所以只看末尾；前面的分段已经定稿，特征可以放心缓存。
    """
    now = time.time() if now is None else now
    out = list(segs)
    while out:
        times = []
        for f in (out[-1].video_path, out[-1].xml_path):
            try:
                times.append(f.stat().st_mtime)
            except OSError:
                pass
        if times and now - max(times) < quiet_seconds:
            out.pop()
        else:
            break
    return out
