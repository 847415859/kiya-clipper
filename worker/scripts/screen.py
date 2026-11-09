#!/usr/bin/env python3
"""阶段 1 粗筛：弹幕 + 音频响度 + 画面变化，找出一场直播的前 N 个候选片段。

用法：
  python scripts/screen.py /data/rec --latest                 # 最近一场（首次会提取音频/画面特征并缓存）
  python scripts/screen.py /data/rec --session 20261110-2000  # 指定场次
  python scripts/screen.py /data/rec                          # 所有场次
  python scripts/screen.py /data/rec --latest --no-video      # 只用弹幕 + 音频，跳过画面（更快）
  python scripts/screen.py /data/rec --latest --danmaku-only  # 只用弹幕（录像已被同步删掉时）

特征只提取一次，缓存在 output/<场次>/features.npz；之后改权重、词典重跑只需几秒。
每场输出到 output/<场次>/：
  candidates.json   候选列表（含各信号贡献、弹幕延迟），供 cut.py / evaluate.py / try_llm.py 使用
  peaks.csv         同上的表格版，最后两列“是否有趣(Y/N)”“备注”给你人工标注
  curve.png         综合分数 + 各信号曲线，高峰标了序号
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper.danmaku import Segment, all_events  # noqa: E402
from clipper.media import extract_session, load_features, save_features  # noqa: E402
from clipper.peaks import find_top_peaks, hhmmss  # noqa: E402
from clipper.session import find_sessions  # noqa: E402

DEFAULT_CONFIG = ROOT.parent / "config" / "kiya.yaml"


def plot(sig: dict, peaks, title: str, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    t = sig["times"] / 60.0
    rows = ["score"] + list(sig["z"].keys())
    fig, axes = plt.subplots(len(rows), 1, figsize=(14, 1.6 * len(rows) + 1.5), sharex=True)
    for ax, name in zip(axes, rows):
        y = sig["score"] if name == "score" else sig["z"][name]
        ax.plot(t, y, lw=0.9 if name != "score" else 1.2, color="#3b6ea5" if name == "score" else "#8a8f98")
        label = "score" if name == "score" else f"{name} (w={sig['weights'][name]:.2f})"
        ax.set_ylabel(label, fontsize=8)
        for p in peaks:
            ax.axvline(p.t / 60.0, color="#d9822b", lw=0.5, alpha=0.5)
        if name == "score":
            for p in peaks:
                ax.annotate(str(p.rank), (p.t / 60.0, p.score), textcoords="offset points", xytext=(0, 3),
                            ha="center", fontsize=7, color="#b35c00")
            mode = "auto" if "自动" in sig["delay_note"] else "fixed"   # 图上只用英文，服务器可能没有中文字体
            ax.set_title(f"{title}   danmaku delay {sig['delay']:.0f}s ({mode})", fontsize=10)
    axes[-1].set_xlabel("minutes since stream start")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def write_outputs(name: str, segs: list[Segment], peaks, sig: dict, out_dir: Path, cfg: dict, used: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "session": name, "signals_used": used, "delay_seconds": sig["delay"], "delay_note": sig["delay_note"],
        "weights": sig["weights"], "generated_at": time.strftime("%F %T"),
        "segments": [{"video": str(s.video_path), "offset": s.offset} for s in segs],
        "candidates": [p.to_dict() for p in peaks],
    }
    (out_dir / "candidates.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    with open(out_dir / "peaks.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["序号", "高峰时间", "片段起", "片段止", "分数", "弹幕/分钟", "情绪词", "礼物价值",
                    "主要信号", "高频弹幕", "视频文件", "文件内起点秒", "是否有趣(Y/N)", "备注"])
        for p in peaks:
            main = " ".join(f"{k}{v:+.1f}" for k, v in sorted(p.contrib.items(), key=lambda kv: -kv[1])[:2])
            w.writerow([p.rank, hhmmss(p.t), hhmmss(p.clip_start), hhmmss(p.clip_end), f"{p.score:.2f}",
                        f"{p.danmaku_per_min:.0f}", p.emotion_hits, f"{p.gift_value:.0f}", main,
                        " | ".join(p.top_danmaku), segs[p.file_index].video_path.name, f"{p.local_start:.1f}", "", ""])
    plot(sig, peaks, name, out_dir / "curve.png")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="录播姬录像目录，或同一场的若干 .xml 文件")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--out", default=str(ROOT / "output"))
    ap.add_argument("--latest", action="store_true", help="只处理最近一场")
    ap.add_argument("--session", help="只处理指定场次，如 20261110-2000")
    ap.add_argument("--top", type=int, help="覆盖配置里的 top_n")
    ap.add_argument("--no-video", action="store_true", help="不提取画面变化（更快）")
    ap.add_argument("--danmaku-only", action="store_true", help="只用弹幕")
    ap.add_argument("--refresh", action="store_true", help="忽略缓存，重新提取特征")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    if args.top:
        cfg["analysis"]["top_n"] = args.top
    sessions = find_sessions(args.paths, latest=args.latest, name=args.session)
    if not sessions:
        print("没有找到场次（检查目录里是否有录播姬的 .xml 弹幕文件）", file=sys.stderr)
        return 1

    for name, segs in sessions:
        events = all_events(segs)
        n_dm = sum(e.kind == "danmaku" for e in events)
        out_dir = Path(args.out) / name
        print(f"\n== {name}：{len(segs)} 个分段，弹幕 {n_dm} 条 ==")
        if n_dm < 20:
            print("  弹幕太少，跳过")
            continue
        feats = None
        if not args.danmaku_only:
            cache = out_dir / ("features.npz" if not args.no_video else "features_novideo.npz")
            feats = None if args.refresh else load_features(cache)
            if feats is None:
                has_video = any(s.video_path.exists() and s.video_path.stat().st_size > 0 for s in segs)
                if has_video:
                    t0 = time.time()
                    print("  提取音频 / 画面特征（首次较慢，结果会缓存）…")
                    feats = extract_session(segs, with_video=not args.no_video)
                    save_features(cache, feats)
                    print(f"  特征提取完成，用时 {time.time() - t0:.0f} 秒")
                else:
                    print("  找不到录像文件（可能已同步到 OSS 并从本地删除），只用弹幕")
            else:
                print(f"  使用缓存特征 {cache.name}")
        peaks, sig = find_top_peaks(events, segs, cfg, feats)
        used = "+".join(sig["weights"].keys())
        write_outputs(name, segs, peaks, sig, out_dir, cfg, used)
        print(f"  使用信号：{used}；弹幕延迟 {sig['delay']:.0f} 秒（{sig['delay_note']}）")
        for p in peaks:
            main = " ".join(f"{k}{v:+.1f}" for k, v in sorted(p.contrib.items(), key=lambda kv: -kv[1])[:2])
            print(f"  #{p.rank:>2} {hhmmss(p.t)}  分数 {p.score:5.2f}  [{main}]  {' | '.join(p.top_danmaku)[:36]}")
        print(f"  结果：{out_dir}/candidates.json、peaks.csv、curve.png")
        print(f"  下一步：python scripts/cut.py {out_dir}/candidates.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
