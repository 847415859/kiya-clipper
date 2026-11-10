#!/usr/bin/env python3
"""精排：多模态大模型逐个判断候选片段（画面 + 主播原话 + 弹幕 + 礼物），打分、找笑点、建议起止，再和粗筛合成排序。

用法：
  python scripts/rerank.py output/20261109-2014/candidates.json             # 全部候选
  python scripts/rerank.py output/20261109-2014/candidates.json --ranks 1 3  # 只看几个
  python scripts/rerank.py output/20261109-2014/candidates.json --dry-run    # 只抽帧、拼提示词，不调用接口

输出 output/<场次>/rerank.json：每个候选的大模型结论、修正后的起止（对齐句子，30 秒 ~ 3 分钟）、最终分和新名次。
配置在 config/kiya.yaml 的 rerank 段（提示词版本、抽帧数、权重、单价）。同样的输入走缓存，重跑不花钱。
效果对比：python scripts/evaluate.py --rerank（和你的 Y/N 标注比，看精排后前几名是不是更准）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from clipper.asr import load_transcript  # noqa: E402
from clipper.boundary import refine_bounds  # noqa: E402
from clipper.context import danmaku_lines, extract_frames, load_events, profile, speech_lines  # noqa: E402
from clipper.llm import LLMClient, fill, image_part, load_prompt, text_part  # noqa: E402
from clipper.peaks import hhmmss  # noqa: E402

TYPES = ("破防", "口胡", "互动", "金句", "高能", "整活", "反差", "其他", "无")


class Story(BaseModel):
    setup: str = ""
    punch: str = ""
    reaction: str = ""


class RerankOut(BaseModel):
    """精排输出结构（与 prompts/rerank_v1.md 的字段说明一致）。"""
    funny: bool
    score: float = Field(ge=0, le=10)
    type: str = "其他"
    standalone: bool = False
    what_happened: str = ""
    reason: str = ""
    punch_time: float = -1
    start_time: float | None = None
    end_time: float | None = None
    story: Story = Field(default_factory=Story)
    title_candidates: list[str] = Field(default_factory=list)

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        return v if v in TYPES else "其他"

    @field_validator("title_candidates")
    @classmethod
    def _titles(cls, v: list[str]) -> list[str]:
        return [str(t).strip()[:40] for t in v if str(t).strip()][:5]


def build_messages(c: dict, segs: list[dict], sents: list[dict], events, delay: float, cfg: dict,
                   rc: dict, frames_dir: Path) -> tuple[list, str, int]:
    version, system, user = load_prompt(rc["prompt"])
    a, b = c["clip_start"], c["clip_end"]
    before = float(rc["context_before"])
    frames = extract_frames(segs, a, b, int(rc["frames"]), frames_dir, int(rc["width"]))
    dm, gifts = danmaku_lines(events, a, b, a, delay)
    text = fill(user, profile=profile(cfg), window=f"{b - a:.0f}", peak=f"{c['t'] - a:.0f}",
                context_before=f"{before:.0f}",
                before_speech=speech_lines(sents, a - before, a, a, limit=1200) if sents else "（这场还没有转写）",
                speech=speech_lines(sents, a, b, a) if sents else "（这场还没有转写）",
                danmaku=dm, gifts=gifts,
                frames_note=(f"共 {len(frames)} 张，见上方（每张标了秒数）" if frames else "（录像不在本地，没有截图，只根据文字判断）"))
    content = []
    for t, p in frames:
        content += [text_part(f"截图（第 {t:.0f} 秒）："), image_part(p)]
    content.append(text_part(text))
    return [{"role": "system", "content": system}, {"role": "user", "content": content}], version, len(frames)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("candidates")
    ap.add_argument("--ranks", type=int, nargs="+")
    ap.add_argument("--config", default=str(REPO / "config" / "kiya.yaml"))
    ap.add_argument("--dry-run", action="store_true", help="只抽帧、拼提示词，存到 rerank_preview/，不调用接口")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    rc = {"prompt": "rerank_v1", "model": "", "frames": 8, "width": 640, "context_before": 60, "workers": 3,
          "weights": {"llm": 0.8, "screen": 0.2}, "min_len": 30, "max_len": 180, "price_in": None, "price_out": None,
          **(cfg.get("rerank") or {})}
    cpath = Path(args.candidates)
    sess = cpath.parent
    data = json.loads(cpath.read_text(encoding="utf-8"))
    cands = [c for c in data["candidates"] if not args.ranks or c["rank"] in args.ranks]
    if not cands:
        print("没有候选（这场没有达到分数线的片段）")
        return 0
    segs = data["segments"]
    tr = load_transcript(sess / "transcript.json")
    sents = tr["sentences"] if tr else []
    if not tr:
        print("提示：这场还没有转写，精排只能看画面和弹幕，效果会差很多。先运行 transcribe.py")
    events = load_events(segs)
    delay = float(data.get("delay_seconds") or 0)
    client = LLMClient(model=rc["model"] or None)
    n_total = len(data["candidates"])
    print(f"精排 {len(cands)} 个候选，模型 {client.model}，提示词 {rc['prompt']}，每个 {rc['frames']} 张图")

    def one(c: dict) -> dict:
        msgs, version, nframes = build_messages(c, segs, sents, events, delay, cfg, rc,
                                                sess / "rerank_frames" / f"{c['rank']:02d}")
        rec = {"rank": c["rank"], "t": c["t"], "clip_start": c["clip_start"], "clip_end": c["clip_end"],
               "screen_score": c["score"], "frames": nframes, "prompt_version": version}
        if args.dry_run:
            prev = [m if isinstance(m["content"], str) else {"role": m["role"], "content": [
                x if x["type"] == "text" else {"type": "image_url", "image_url": "<图片省略>"} for x in m["content"]]}
                for m in msgs]
            out = sess / "rerank_preview"
            out.mkdir(exist_ok=True)
            (out / f"{c['rank']:02d}.json").write_text(json.dumps(prev, ensure_ascii=False, indent=1), encoding="utf-8")
            return rec
        r = client.chat_json(msgs, RerankOut, tag=version)
        rec.update(cached=r.cached, error=r.error, usage=r.usage)
        if r.data is None:
            rec["llm"] = None
            return rec
        o = r.data
        a, b = c["clip_start"], c["clip_end"]
        punch = a + o.punch_time if o.punch_time >= 0 else None
        start = a + max(o.start_time, -float(rc["context_before"])) if o.start_time is not None else None
        end = a + min(o.end_time, (b - a) + 30) if o.end_time is not None else None
        rec["llm"] = o.model_dump()
        rec["refined"] = refine_bounds(sents, c["t"], punch, start, end, float(rc["min_len"]), float(rc["max_len"]))
        return rec

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, int(rc["workers"]))) as ex:
        recs = list(ex.map(one, cands))
    if args.dry_run:
        print(f"dry-run：提示词预览在 {sess / 'rerank_preview'}，截图在 {sess / 'rerank_frames'}")
        return 0

    w = rc["weights"]
    for r in recs:
        screen = 1 - (r["rank"] - 1) / max(1, n_total - 1)          # 粗筛名次换成 0~1（第 1 名 = 1）
        if r.get("llm"):
            f = w["llm"] * r["llm"]["score"] / 10 + w["screen"] * screen
            if not r["llm"]["funny"]:
                f *= 0.6
        else:
            f = w["screen"] * screen * 0.5                            # 大模型失败的排后面，但保留
        r["final"] = round(f, 4)
    recs.sort(key=lambda r: -r["final"])
    for i, r in enumerate(recs, 1):
        r["new_rank"] = i

    cost = client.cost(rc["price_in"], rc["price_out"])
    out = {"session": sess.name, "model": client.model, "prompt": rc["prompt"], "generated_at": time.strftime("%F %T"),
           "weights": w, "tokens": client.total, "cost_yuan": round(cost, 4) if cost is not None else None,
           "candidates": recs}
    # 只精排了部分序号时，和已有结果合并
    old = sess / "rerank.json"
    if args.ranks and old.exists():
        prev = json.loads(old.read_text(encoding="utf-8"))
        keep = [r for r in prev.get("candidates", []) if r["rank"] not in args.ranks]
        out["candidates"] = sorted(keep + recs, key=lambda r: -r["final"])
        for i, r in enumerate(out["candidates"], 1):
            r["new_rank"] = i
    old.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"{'新':>3} {'原':>3}  {'高峰':<9}{'分':>5}  {'有趣':<4}{'类型':<5}{'修正后起止':<20}  一句话")
    for r in out["candidates"]:
        o = r.get("llm")
        if not o:
            print(f"{r['new_rank']:>3} #{r['rank']:<2}  {hhmmss(r['t']):<9}{'—':>5}  失败：{r.get('error', '')[:60]}")
            continue
        rf = r["refined"]
        print(f"{r['new_rank']:>3} #{r['rank']:<2}  {hhmmss(r['t']):<9}{o['score']:>5.1f}  {'是' if o['funny'] else '否':<4}"
              f"{o['type']:<5}{hhmmss(rf['start'])}–{hhmmss(rf['end'])}（{rf['end'] - rf['start']:.0f}s）  {o['what_happened'][:40]}")
    t = client.total
    print(f"\n用时 {time.time() - t0:.0f} 秒；请求 {t['calls']} 次（缓存命中 {t['cached']}），tokens 输入 {t['prompt_tokens']} / 输出 {t['completion_tokens']}"
          + (f"，约 ¥{cost:.3f}" if cost is not None else "（在 config 的 rerank.price_in / price_out 填单价可估算费用）"))
    print(f"结果：{old}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
