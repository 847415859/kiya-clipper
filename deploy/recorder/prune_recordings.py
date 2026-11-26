"""Free recording disk space after verifying a complete archival set."""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "worker"))
from clipper.danmaku import parse_xml  # noqa: E402
from clipper.session import collect_xml, group_sessions  # noqa: E402


GIB = 1024 ** 3
QUIET_SECONDS = 2 * 60 * 60


def oldest_recording(root: Path) -> tuple[list[Path], list[Path]] | None:
    rooms: dict[str, list[Path]] = defaultdict(list)
    for xml in collect_xml([root]):
        if xml.is_symlink() or xml.resolve() != xml:
            continue
        parts = xml.relative_to(root).parts
        rooms[parts[0] if len(parts) > 1 else ""].append(xml)
    recordings = []
    for files in rooms.values():
        # group_sessions orders known start times first and puts undated XMLs
        # last. Undated XMLs cannot reliably identify a scene to remove.
        for group in group_sessions(files):
            start = parse_xml(group[0]).start_time
            if start is None:
                continue
            videos = [xml.with_suffix(".flv") for xml in group]
            videos = [video for video in videos if video.exists() or video.is_symlink()]
            if videos:
                recordings.append((start, group, videos))
    if not recordings:
        return None
    _, group, videos = min(recordings, key=lambda recording: recording[0])
    return group, videos


def snapshot(root: Path, files: list[Path]) -> dict[Path, tuple[int, ...]]:
    device = root.stat().st_dev
    result = {}
    for path in files:
        info = path.lstat()
        if path.resolve() != path or not stat.S_ISREG(info.st_mode) or info.st_dev != device:
            raise ValueError(f"不是录像磁盘上的普通文件：{path}")
        result[path] = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    return result


def prune(root: Path, remote: str, minimum: int, plan: bool) -> None:
    free = shutil.disk_usage(root).free
    print(f"录像磁盘：可用 {free / GIB:.2f} GiB，清理阈值 {minimum / GIB:.2f} GiB", flush=True)
    if free >= minimum:
        return
    selected = oldest_recording(root)
    if selected is None:
        print("未找到有本地 FLV 且可识别的场次")
        return

    xmls, videos = selected
    watched = xmls + videos
    before = snapshot(root, watched)
    cutoff = time.time_ns() - QUIET_SECONDS * 1_000_000_000
    if any(entry[3] > cutoff for entry in before.values()):
        print(f"最老场次 {xmls[0].stem} 最近两小时仍有文件变动，暂缓清理")
        return

    paths = [video.relative_to(root).as_posix() for video in videos]
    if any("\n" in path or "\r" in path for path in paths):
        raise ValueError("录像路径含换行，无法安全核对 OSS 清单")
    prefix = "计划核验" if plan else "开始核验"
    print(f"{prefix}最老场次 {xmls[0].stem}，共 {len(videos)} 段 FLV", flush=True)
    if plan:
        print("\n".join(paths))
        return

    checked = subprocess.run(
        ["rclone", "check", str(root), remote, "--one-way", "--download",
         "--files-from-raw", "-", "--match", "-", "--checkers", "2", "--log-level", "NOTICE"],
        input="\n".join(paths) + "\n", stdout=subprocess.PIPE, encoding="utf-8", check=True,
    )
    if set(checked.stdout.split("\n")) - {""} != set(paths):
        raise ValueError("OSS 核验结果与待清理录像清单不一致")
    if shutil.disk_usage(root).free >= minimum:
        print("录像磁盘已恢复到阈值，取消本次清理")
        return
    if oldest_recording(root) != selected or snapshot(root, watched) != before:
        raise ValueError("场次或文件在归档核验期间发生变化，取消清理")

    for video in videos:
        video.unlink()
        print(f"已移除本地 FLV：{video}", flush=True)
    print(f"清理完成，共 {len(videos)} 段，弹幕 XML 已保留")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording_dir", type=Path)
    parser.add_argument("oss_remote")
    parser.add_argument("min_free_gib", type=int)
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args()
    if args.min_free_gib < 1:
        parser.error("磁盘阈值必须是正整数 GiB")
    try:
        prune(args.recording_dir.resolve(strict=True), args.oss_remote, args.min_free_gib * GIB, args.plan)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"录像清理中止：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
