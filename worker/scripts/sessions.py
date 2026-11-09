#!/usr/bin/env python3
"""列出录像目录里的场次（后台“扫描录像”调用）。

用法：
  python scripts/sessions.py /data/rec            # 表格
  python scripts/sessions.py /data/rec --json     # JSON（后台用）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper.session import find_sessions  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    out = []
    for name, segs in find_sessions(args.paths):
        videos = [s.video_path for s in segs]
        present = [v for v in videos if v.exists() and v.stat().st_size > 0]
        last = segs[-1]
        out.append({
            "name": name,
            "start_time": segs[0].start_time.isoformat() if segs[0].start_time else None,
            "segments": len(segs),
            "duration_seconds": round(last.offset + (last.last_t or 0), 1),
            "videos_present": len(present),
            "video_bytes": sum(v.stat().st_size for v in present),
            "danmaku": sum(1 for s in segs for e in s.events if e.kind == "danmaku"),
        })
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        for s in out:
            print(f"{s['name']}  {s['segments']} 段  约 {s['duration_seconds'] / 3600:.1f} 小时  "
                  f"录像在本地 {s['videos_present']}/{s['segments']}  弹幕 {s['danmaku']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
