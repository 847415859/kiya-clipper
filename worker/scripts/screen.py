#!/usr/bin/env python3
"""阶段 1 粗筛：弹幕 + 音频响度 + 画面变化，找出一场直播的前 N 个候选片段。

用法：
  python scripts/screen.py /data/rec --latest                 # 最近一场（首次会提取音频/画面特征并缓存）
  python scripts/screen.py /data/rec --session 20261110-2000  # 指定场次
  python scripts/screen.py /data/rec                          # 所有场次
  python scripts/screen.py /data/rec --latest --no-video      # 只用弹幕 + 音频，跳过画面（更快）
  python scripts/screen.py /data/rec --latest --danmaku-only  # 只用弹幕（录像已被同步删掉时）
  python scripts/screen.py /data/rec --latest --min-score 2.5 # 临时指定分数线（默认读配置 analysis.min_score）
  python scripts/screen.py /data/rec --latest --all           # 忽略分数线，输出前 top_n 个
  python scripts/screen.py /data/rec --latest --live          # 边播边筛：直播中只筛已录完的分段（监控脚本定时调用）

分数线：低于 analysis.min_score 的高峰不进候选列表（不剪、不送审），只记在 candidates.json 的
below_threshold 里备查。没有片段过线时候选为空，不会硬凑。分数线用 evaluate.py --sweep 按标注定。

特征只提取一次，缓存在 output/<场次>/features.npz；之后改权重、词典重跑只需几秒。
每场输出到 output/<场次>/：
  candidates.json   候选列表（含各信号贡献、弹幕延迟），供 cut.py / evaluate.py / try_llm.py 使用
  peaks.csv         同上的表格版，最后两列“是否有趣(Y/N)”“备注”给你人工标注
  curve.png         综合分数 + 各信号曲线，高峰标了序号
边播边筛（--live）：candidates.json 带 live=true / covered_seconds，审核页能提前看到高分候选；
不转写、不精排、不剪辑，也不写整场特征缓存（各分段的特征缓存在 segfeat/，下播后的完整粗筛直接复用，只补最后一段）。
下播后的正式粗筛会整体覆盖 live 结果；已有正式结果的场次不会被 live 覆盖。
转写过的场次会加入“笑声”信号；有录像的会加入“尖叫”信号，并给每个候选算“音乐”程度（唱歌 / 纯音乐，0~1）。
旧版本的特征缓存没有尖叫 / 音乐特征，会自动重新提取一次。
如果已经用 transcribe.py 转写过（output/<场次>/transcript.json），每个候选会附上片段内的主播原话。
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

from clipper.asr import format_speech, load_transcript, speech_between  # noqa: E402
from clipper.danmaku import Segment, all_events  # noqa: E402
from clipper.media import extract_session, load_features, save_features  # noqa: E402
from clipper.peaks import find_top_peaks, hhmmss  # noqa: E402
from clipper.session import drop_active, find_sessions  # noqa: E402

DEFAULT_CONFIG = ROOT.parent / "config" / "kiya.yaml"


def plot(sig: dict, peaks, title: str, path: Path, min_score: float | None = None, dropped=()) -> None:
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
            if min_score is not None:
                ax.axhline(min_score, color="#c0392b", lw=0.8, ls="--")
            for p in dropped:
                ax.plot(p.t / 60.0, p.score, "x", color="#8a8f98", ms=4)
            for p in peaks:
                ax.annotate(str(p.rank), (p.t / 60.0, p.score), textcoords="offset points", xytext=(0, 3),
                            ha="center", fontsize=7, color="#b35c00")
            mode = "auto" if "自动" in sig["delay_note"] else "fixed"   # 图上只用英文，服务器可能没有中文字体
            ax.set_title(f"{title}   danmaku delay {sig['delay']:.0f}s ({mode})", fontsize=10)
    axes[-1].set_xlabel("minutes since stream start")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def write_outputs(name: str, segs: list[Segment], peaks, sig: dict, out_dir: Path, cfg: dict, used: str,
                  min_score: float | None = None, dropped=(), extra: dict | None = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    tr = load_transcript(out_dir / "transcript.json")
    sents = tr["sentences"] if tr else []
    cands = []
    for p in peaks:
        d = p.to_dict()
        if tr:
            d["speech"] = [{"t0": s["t0"], "t1": s["t1"], "text": s["text"]}
                           for s in speech_between(sents, p.clip_start, p.clip_end)]
        cands.append(d)
    data = {
        "session": name, "signals_used": used, "delay_seconds": sig["delay"], "delay_note": sig["delay_note"],
        "weights": sig["weights"], "generated_at": time.strftime("%F %T"),
        "transcript": tr["model"] if tr else None,
        "min_score": min_score,
        "below_threshold": [{"rank": p.rank, "t": p.t, "score": round(p.score, 3), "clip_start": p.clip_start,
                             "clip_end": p.clip_end} for p in dropped],
        "segments": [{"video": str(s.video_path), "offset": s.offset} for s in segs],
        "candidates": cands,
    }
    if extra:
        data.update(extra)
    (out_dir / "candidates.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    with open(out_dir / "peaks.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["序号", "高峰时间", "片段起", "片段止", "分数", "弹幕/分钟", "情绪词", "礼物价值",
                    "主要信号", "高频弹幕", "主播原话", "音乐", "视频文件", "文件内起点秒", "是否有趣(Y/N)", "备注"])
        for p, d in zip(peaks, cands):
            main = " ".join(f"{k}{v:+.1f}" for k, v in sorted(p.contrib.items(), key=lambda kv: -kv[1])[:2])
            speech = format_speech(d.get("speech", []), p.clip_start, limit=400).replace("\n", " ")
            w.writerow([p.rank, hhmmss(p.t), hhmmss(p.clip_start), hhmmss(p.clip_end), f"{p.score:.2f}",
                        f"{p.danmaku_per_min:.0f}", p.emotion_hits, f"{p.gift_value:.0f}", main,
                        " | ".join(p.top_danmaku), speech, f"{p.music:.2f}", segs[p.file_index].video_path.name, f"{p.local_start:.1f}", "", ""])
    plot(sig, peaks, name, out_dir / "curve.png", min_score, dropped)


def _read(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except (OSError, ValueError):
        return None


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
    ap.add_argument("--min-score", type=float, help="分数线，覆盖配置 analysis.min_score")
    ap.add_argument("--all", action="store_true", help="忽略分数线")
    ap.add_argument("--live", action="store_true",
                    help="边播边筛：只用已经录完的分段，结果标 live=true；不写整场特征缓存，不覆盖下播后的正式结果")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    if args.top:
        cfg["analysis"]["top_n"] = args.top
    min_score = None if args.all else (args.min_score if args.min_score is not None
                                         else cfg["analysis"].get("min_score"))
    sessions = find_sessions(args.paths, latest=args.latest, name=args.session)
    if not sessions:
        print("没有找到场次（检查目录里是否有录播姬的 .xml 弹幕文件）", file=sys.stderr)
        return 1

    lc = {"quiet_seconds": 120, "min_seconds": 900, **(cfg.get("live_screen") or {})}
    for name, segs in sessions:
        out_dir = Path(args.out) / name
        live_extra = None
        if args.live:
            prev = _read(out_dir / "candidates.json")
            if prev and not prev.get("live"):
                print(f"\n== {name}：已有下播后的正式结果，不再做边播边筛 ==")
                continue
            total_segs = len(segs)
            segs = drop_active(segs, float(lc["quiet_seconds"]))
            if not segs:
                print(f"\n== {name}：还没有录完的分段，等下一轮 ==")
                continue
        events = all_events(segs)
        n_dm = sum(e.kind == "danmaku" for e in events)
        print(f"\n== {name}：{len(segs)} 个分段，弹幕 {n_dm} 条" + (f"（边播边筛，共 {total_segs} 段，在写的不算）" if args.live else "") + " ==")
        if n_dm < 20:
            print("  弹幕太少，跳过")
            continue
        feats = None
        if not args.danmaku_only:
            cache = out_dir / ("features.npz" if not args.no_video else "features_novideo.npz")
            feats = None if (args.refresh or args.live) else load_features(cache)   # 边播边筛的数据不完整，不读也不写整场缓存
            if feats is None:
                has_video = any(s.video_path.exists() and s.video_path.stat().st_size > 0 for s in segs)
                if has_video:
                    t0 = time.time()
                    print("  提取音频 / 画面特征（首次较慢，结果会缓存）…")
                    feats = extract_session(segs, with_video=not args.no_video,
                                            cache_dir=None if args.refresh else out_dir / "segfeat")
                    if not args.live:
                        save_features(cache, feats)
                    print(f"  特征提取完成，用时 {time.time() - t0:.0f} 秒")
                else:
                    print("  找不到录像文件（可能已同步到 OSS 并从本地删除），只用弹幕")
            else:
                print(f"  使用缓存特征 {cache.name}")
        if args.live:
            covered = len(feats["audio_db"]) if feats is not None else int(segs[-1].offset + segs[-1].last_t)
            if covered < float(lc["min_seconds"]):
                print(f"  已录完的部分才 {covered // 60} 分钟，不到 {float(lc['min_seconds']) / 60:.0f} 分钟，先不筛")
                continue
            live_extra = {"live": True, "covered_seconds": int(covered), "segments_done": len(segs),
                          "segments_total": total_segs}
        tr0 = load_transcript(out_dir / "transcript.json")
        peaks, sig = find_top_peaks(events, segs, cfg, feats, tr0["sentences"] if tr0 else None)
        dropped = []
        if min_score is not None:      # 按分数从高到低排好的，过线的一定是前面连续的一段，序号不断
            dropped = [p for p in peaks if p.score < min_score]
            peaks = [p for p in peaks if p.score >= min_score]
        used = "+".join(sig["weights"].keys())
        write_outputs(name, segs, peaks, sig, out_dir, cfg, used, min_score, dropped, live_extra)
        if (out_dir / "transcript.json").exists():
            print("  已附上主播原话（transcript.json）")
        if sig.get("music_filtered"):
            print(f"  歌回过滤：{sig['music_filtered']} 个高峰在唱歌 / 放音乐，已去掉")
        print(f"  使用信号：{used}；弹幕延迟 {sig['delay']:.0f} 秒（{sig['delay_note']}）")
        for p in peaks:
            main = " ".join(f"{k}{v:+.1f}" for k, v in sorted(p.contrib.items(), key=lambda kv: -kv[1])[:2])
            print(f"  #{p.rank:>2} {hhmmss(p.t)}  分数 {p.score:5.2f}  [{main}]  {' | '.join(p.top_danmaku)[:36]}")
        if min_score is not None:
            print(f"  分数线 {min_score}：{len(peaks)} 个过线" + (f"，{len(dropped)} 个低于分数线未列出" if dropped else ""))
            if not peaks:
                print("  本场没有达到分数线的片段")
        print(f"  结果：{out_dir}/candidates.json、peaks.csv、curve.png")
        if args.live:
            print(f"  边播边筛：覆盖前 {live_extra['covered_seconds'] // 60} 分钟，下播后会重新筛一遍（只补最后一段）")
        else:
            print(f"  下一步：python scripts/cut.py {out_dir}/candidates.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
