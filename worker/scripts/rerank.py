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
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from clipper.kcfg import find_taboo, load_cfg  # noqa: E402
from clipper.streamer import decisions_path, resolve_config  # noqa: E402
from clipper.asr import load_transcript  # noqa: E402
from clipper.boundary import refine_bounds  # noqa: E402
from clipper.context import danmaku_lines, extract_frames, load_events, profile, speech_lines  # noqa: E402
from clipper.llm import LLMClient, fill, image_part, load_prompt, text_part  # noqa: E402
from clipper.memory import active_weights, context_text, final_score, load_memory  # noqa: E402
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
        clean = [str(t).strip().strip("《》「」\"“”").strip() for t in v]
        return [t[:40] for t in clean if t][:5]


def build_messages(c: dict, segs: list[dict], sents: list[dict], events, delay: float, cfg: dict,
                   rc: dict, frames_dir: Path, memory: str = "") -> tuple[list, str, int]:
    version, system, user = load_prompt(rc["prompt"])
    a, b = c["clip_start"], c["clip_end"]
    before = float(rc["context_before"])
    frames = extract_frames(segs, a, b, int(rc["frames"]), frames_dir, int(rc["width"]))
    dm, gifts = danmaku_lines(events, a, b, a, delay)
    extra = {"memory": memory or "（还没有积累）"} if "{{memory}}" in user else {}
    text = fill(user, profile=profile(cfg), **extra, window=f"{b - a:.0f}", peak=f"{c['t'] - a:.0f}",
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
    ap.add_argument("--config", default=None, help="主播配置；不填时按 candidates.json 所在目录推导主播")
    ap.add_argument("--dry-run", action="store_true", help="只抽帧、拼提示词，存到 rerank_preview/，不调用接口")
    ap.add_argument("--rescore", action="store_true", help="不调大模型，按当前权重重算已有 rerank.json 的排序")
    args = ap.parse_args()

    cpath = Path(args.candidates)
    sess = cpath.parent
    cfg = load_cfg(resolve_config(args.config, sess))
    rc = {"prompt": "rerank_v1", "model": "", "frames": 8, "width": 640, "context_before": 60, "workers": 3,
          "min_len": 30, "max_len": 180, "price_in": None, "price_out": None,
          **(cfg.get("rerank") or {})}
    data = json.loads(cpath.read_text(encoding="utf-8"))
    dec_file = Path(os.environ.get("KIYA_MEMORY_DECISIONS") or decisions_path())
    try:
        decisions = json.loads(dec_file.read_text(encoding="utf-8")) if dec_file.exists() else {}
    except ValueError:
        decisions = {}
    w = active_weights(cfg, decisions)
    n_total = len(data["candidates"])
    taboo = cfg.get("taboo") or []
    if args.rescore:
        tr0 = load_transcript(sess / "transcript.json")
        return rescore(sess / "rerank.json", w, n_total, taboo, tr0["sentences"] if tr0 else [])
    mem_file = Path(os.environ.get("KIYA_MEMORY_FILE") or sess.parent / "_memory" / "memory.json")
    memory, mem_version = context_text(load_memory(mem_file), sess.name, cfg, decisions)
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
    client = LLMClient(model=rc["model"] or None, cache_dir=sess.parent / "_llm_cache")
    print(f"精排 {len(cands)} 个候选，模型 {client.model}，提示词 {rc['prompt']}，每个 {rc['frames']} 张图；"
          f"精排记忆 {mem_version or '（无）'}")

    def one(c: dict) -> dict:
        msgs, version, nframes = build_messages(c, segs, sents, events, delay, cfg, rc,
                                                sess / "rerank_frames" / f"{c['rank']:02d}", memory)
        rec = {"rank": c["rank"], "t": c["t"], "clip_start": c["clip_start"], "clip_end": c["clip_end"],
               "screen_score": c["score"], "frames": nframes, "prompt_version": version, "memory_version": mem_version,
               "taboo": taboo_hits(sents, c["clip_start"], c["clip_end"], taboo)}
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

    for r in recs:
        r["final"] = round(final_score(r.get("llm"), r["rank"], n_total, w, bool(r.get("taboo"))), 4)
    recs.sort(key=lambda r: -r["final"])
    for i, r in enumerate(recs, 1):
        r["new_rank"] = i

    cost = client.cost(rc["price_in"], rc["price_out"])
    out = {"session": sess.name, "model": client.model, "prompt": rc["prompt"], "memory_version": mem_version,
           "generated_at": time.strftime("%F %T"),
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
        if r.get("taboo"):
            print(f"{r['new_rank']:>3} #{r['rank']:<2}  {hhmmss(r['t']):<9}  命中禁区话题：{'、'.join(r['taboo'])}（排到最后）")
            continue
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


def taboo_hits(sents: list[dict], a: float, b: float, words: list[str]) -> list[str]:
    """片段范围内主播原话里命中的禁区词。"""
    if not words or not sents:
        return []
    return find_taboo("".join(s["text"] for s in sents if s["t1"] > a and s["t0"] < b), words)


def rescore(path: Path, w: dict, n_total: int, taboo: list[str] | None = None, sents: list[dict] | None = None) -> int:
    """后台确认新权重后：不重新请求大模型，只按新权重重排。"""
    if not path.exists():
        print("这场还没有精排结果", file=sys.stderr)
        return 1
    d = json.loads(path.read_text(encoding="utf-8"))
    before = [c["rank"] for c in sorted(d["candidates"], key=lambda c: c.get("new_rank", 999))]
    for c in d["candidates"]:
        c["taboo"] = taboo_hits(sents or [], c.get("clip_start", 0), c.get("clip_end", -1), taboo or [])     # 禁区词改了，这里一并刷新
        c["final"] = round(final_score(c.get("llm"), c["rank"], n_total, w, bool(c["taboo"])), 4)
    d["candidates"].sort(key=lambda c: -c["final"])
    for i, c in enumerate(d["candidates"], 1):
        c["new_rank"] = i
    d["weights"] = w
    d["rescored_at"] = time.strftime("%F %T")
    path.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    after = [c["rank"] for c in d["candidates"]]
    print(f"按权重 {w} 重排：前 10 名（原序号）{before[:10]} → {after[:10]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
