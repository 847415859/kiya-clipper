#!/usr/bin/env python3
"""阶段 0 手工验证：分析录播姬弹幕，找出前 N 个弹幕高峰。

用法（在服务器上，录像目录 /data/rec）：
  python scripts/analyze_danmaku.py /data/rec --out output            # 分析目录下所有场次
  python scripts/analyze_danmaku.py /data/rec --latest --out output   # 只分析最近一场
  python scripts/analyze_danmaku.py a.xml b.xml --out output          # 指定同一场的分段文件

每场输出到 output/<开播时间>/：
  peaks.csv            前 N 个高峰：时间、分数、高频弹幕、视频文件，留有“是否有趣”“备注”两列给你填
  curve.png            整场弹幕热度曲线，高峰标了序号
  cut_candidates.sh    剪出每个候选片段的 ffmpeg 命令（不重新编码，几秒一段）
"""
from __future__ import annotations

import argparse
import csv
import shlex
import sys
from datetime import timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper.danmaku import Segment, all_events, load_session, parse_xml  # noqa: E402
from clipper.peaks import find_top_peaks, hhmmss  # noqa: E402

DEFAULT_CONFIG = ROOT.parent / "config" / "kiya.yaml"


def collect_xml(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            files += sorted(p.rglob("*.xml"))
        elif p.suffix.lower() == ".xml":
            files.append(p)
    return files


def group_sessions(files: list[Path], gap_hours: float) -> list[list[Path]]:
    """按分段开始时间分场：相邻分段开始时间相差超过 gap_hours 视为新的一场。"""
    metas = []
    for f in files:
        seg = parse_xml(f)
        metas.append((seg.start_time, f))
    with_time = sorted([m for m in metas if m[0]], key=lambda m: m[0])
    no_time = [m[1] for m in metas if not m[0]]
    sessions: list[list[Path]] = []
    prev = None
    for st, f in with_time:
        if prev is None or st - prev > timedelta(hours=gap_hours):
            sessions.append([])
        sessions[-1].append(f)
        prev = st
    if no_time:  # 没有开始时间的文件：无法分场，整体当作一场
        sessions.append(sorted(no_time))
    return sessions


def session_name(segs: list[Segment]) -> str:
    st = segs[0].start_time
    return st.strftime("%Y%m%d-%H%M") if st else segs[0].xml_path.stem[:40]


def plot_curve(sig: dict, peaks, title: str, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    t_min = sig["times"] / 60.0
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 6), sharex=True,
                                   gridspec_kw={"height_ratios": [2, 1]})
    ax1.plot(t_min, sig["score"], lw=1, color="#3b6ea5")
    for p in peaks:
        ax1.axvline(p.t / 60.0, color="#d9822b", lw=0.6, alpha=0.6)
        ax1.annotate(str(p.rank), (p.t / 60.0, p.score), textcoords="offset points",
                     xytext=(0, 4), ha="center", fontsize=8, color="#b35c00")
    ax1.set_ylabel("score")
    ax1.set_title(title)
    ax2.bar(t_min, sig["count"] * 60.0 / sig["bucket"], width=sig["bucket"] / 60.0, color="#8aa9c9")
    ax2.set_ylabel("danmaku / min")
    ax2.set_xlabel("minutes since stream start")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def write_outputs(segs: list[Segment], peaks, sig, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "peaks.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["序号", "高峰时间", "片段起", "片段止", "分数", "弹幕/分钟", "情绪词",
                    "礼物价值", "高频弹幕", "视频文件", "文件内起点秒", "是否有趣(Y/N)", "备注"])
        for p in peaks:
            w.writerow([p.rank, hhmmss(p.t), hhmmss(p.clip_start), hhmmss(p.clip_end),
                        f"{p.score:.2f}", f"{p.danmaku_per_min:.0f}", p.emotion_hits,
                        f"{p.gift_value:.0f}", " | ".join(p.top_danmaku),
                        segs[p.file_index].video_path.name, f"{p.local_start:.1f}", "", ""])

    lines = ["#!/usr/bin/env bash",
             "# 剪出候选片段（-c copy 不重新编码，起点可能有 1–2 秒偏差，验证用足够）",
             "set -e", f"mkdir -p {shlex.quote(str(out_dir / 'clips'))}"]
    for p in peaks:
        video = segs[p.file_index].video_path
        dst = out_dir / "clips" / f"{p.rank:02d}_{hhmmss(p.t).replace(':', '-')}.flv"
        dur = p.clip_end - p.clip_start
        lines.append(f"ffmpeg -nostdin -loglevel error -y -ss {p.local_start:.1f} -i {shlex.quote(str(video))} "
                     f"-t {dur:.0f} -c copy {shlex.quote(str(dst))}")
    (out_dir / "cut_candidates.sh").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out_dir / "cut_candidates.sh").chmod(0o755)

    plot_curve(sig, peaks, f"{segs[0].xml_path.parent.name}  {out_dir.name}", out_dir / "curve.png")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="录播姬录像目录，或同一场的若干 .xml 文件")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--out", default="output")
    ap.add_argument("--top", type=int, help="覆盖配置里的 top_n")
    ap.add_argument("--latest", action="store_true", help="只分析最近一场")
    ap.add_argument("--gap-hours", type=float, default=2.0, help="分段间隔超过多少小时算新的一场")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    if args.top:
        cfg["analysis"]["top_n"] = args.top

    files = collect_xml(args.paths)
    if not files:
        print("没有找到 .xml 弹幕文件", file=sys.stderr)
        return 1
    sessions = group_sessions(files, args.gap_hours)
    if args.latest:
        sessions = sessions[-1:]

    for files_in_session in sessions:
        segs = load_session(files_in_session)
        events = all_events(segs)
        n_dm = sum(1 for e in events if e.kind == "danmaku")
        if n_dm < 50:
            print(f"跳过 {files_in_session[0].name}：弹幕太少（{n_dm} 条）")
            continue
        peaks, sig = find_top_peaks(events, segs, cfg)
        out_dir = Path(args.out) / session_name(segs)
        write_outputs(segs, peaks, sig, out_dir)
        dur = max(e.t for e in events)
        print(f"\n== {out_dir.name}：{len(segs)} 个分段，时长约 {hhmmss(dur)}，弹幕 {n_dm} 条 ==")
        for p in peaks:
            print(f"#{p.rank:>2}  {hhmmss(p.t)}  分数 {p.score:5.2f}  {p.danmaku_per_min:4.0f} 条/分  "
                  f"{' | '.join(p.top_danmaku)[:40]}")
        print(f"结果：{out_dir}/peaks.csv、curve.png、cut_candidates.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
