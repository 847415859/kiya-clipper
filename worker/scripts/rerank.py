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
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from clipper.kcfg import find_taboo, load_cfg  # noqa: E402
from clipper.streamer import decisions_path, resolve_config  # noqa: E402
from clipper.asr import load_transcript  # noqa: E402
from clipper.boundary import refine_bounds  # noqa: E402
from clipper.peaks import emotion_matcher  # noqa: E402
from clipper.context import danmaku_lines, extract_frames, load_events, profile, speech_lines  # noqa: E402
from clipper.llm import LLMClient, fill, image_part, load_prompt, text_part  # noqa: E402
from clipper.memory import active_weights, context_text, final_score, load_memory  # noqa: E402
from clipper.peaks import hhmmss  # noqa: E402

TYPES = ("破防", "口胡", "互动", "金句", "高能", "整活", "反差", "其他", "无")


class Story(BaseModel):
    setup: str = ""
    punch: str = ""
    reaction: str = ""


class Evidence(BaseModel):
    t: float = 0
    quote: str = ""
    src: str = "原话"

    @field_validator("t", mode="before")
    @classmethod
    def _t(cls, v):
        return _as_num(v)                  # 原话 / 弹幕 / 画面

    @field_validator("src")
    @classmethod
    def _src(cls, v: str) -> str:
        return v if v in ("原话", "弹幕", "画面") else "原话"


def _as_num(v):
    """大模型偶尔把秒数写成“约 20 秒”“20s”：取出里面的第一个数字，省一次重试；取不出来原样返回，让校验报错。"""
    if isinstance(v, str):
        m = re.search(r"-?\d+(?:\.\d+)?", v)
        return float(m.group()) if m else v
    return v


FAIL_REASONS = ("not_funny", "need_context", "read_sc", "repeat_meme", "taboo", "incomplete", "weak_hook", "other")
TIERS = ("推荐", "备选", "淘汰")


class RerankOut(BaseModel):
    """精排输出结构（与 prompts/rerank_v1.md 的字段说明一致）。"""
    funny: bool
    score: float = Field(ge=0, le=10)
    type: str = "其他"
    standalone: bool = False
    needs_context: bool = False       # v3：要知道前情 / 圈内梗才懂（旧结果没有这个字段）
    complete: bool = True             # v3：有没有完整的 铺垫→笑点→反应
    what_happened: str = ""
    reason: str = ""
    punch_time: float = -1
    start_time: float | None = None
    end_time: float | None = None
    story: Story = Field(default_factory=Story)
    title_candidates: list[str] = Field(default_factory=list)
    # ---- v4：把关与交接（旧提示词不会输出这些，全部有默认值，旧结果照常读）
    confidence: float = Field(default=1.0, ge=0, le=1)       # 大模型对自己判断的把握
    evidence: list[Evidence] = Field(default_factory=list)   # 笑点证据：原话 + 秒数
    asr_doubt: bool = False                                  # 笑点依赖的词可能被语音识别听错
    risk_level: str = "none"                                 # none / low / high（审核 / 主播不想发）
    risks: list[str] = Field(default_factory=list)
    hook: str = ""                                           # 前 3 秒钩子：强 / 弱 / 无（旧结果为空）
    hook_ok: bool = True                                     # 由 hook 推出：弱 / 无 = False（旧结果 / 旧提示词用它）
    hook_note: str = ""
    in_character: bool = True                                # 单独发出去会不会让路人误读她的性格 / 损害形象
    in_character_note: str = ""
    pace: str = ""                                           # 单笑点 / 连续 / 渐强
    types: list[str] = Field(default_factory=list)           # 笑点类型（1–2 个，第一个 = type），剪辑按类型选手法
    start_reason: str = ""                                   # 为什么从这里切 / 到这里停（审核拖边界时的依据）
    end_reason: str = ""
    highlight_times: list[float] = Field(default_factory=list)   # 表情最夸张 / 最有戏的时刻（给放大用）
    edit_hints: list[str] = Field(default_factory=list)      # 带秒数的具体剪辑建议
    highlight_danmaku: list[str] = Field(default_factory=list)
    fail_reason: str = ""                                    # 不推荐时的原因，取值同审核页原因标签 + incomplete / weak_hook / other
    punch_line: str = ""
    context_caption: str = ""                                # 开头补的前情字幕（≤20 字）
    beats: list[str] = Field(default_factory=list)           # 2–4 个节拍，给剪辑用
    cover_time: float = -1                                   # 封面帧（相对片段开头；-1 = 没有）
    tier: str = ""                                           # 同场二次比较后写入：推荐 / 备选 / 淘汰

    @field_validator("punch_time", "start_time", "end_time", "cover_time", mode="before")
    @classmethod
    def _secs(cls, v):
        return _as_num(v)

    @field_validator("risk_level")
    @classmethod
    def _risk_level(cls, v: str) -> str:
        return v if v in ("none", "low", "high") else "low"

    @field_validator("fail_reason")
    @classmethod
    def _fail_reason(cls, v: str) -> str:
        v = (v or "").strip()
        return v if v in FAIL_REASONS or not v else "other"

    @field_validator("tier")
    @classmethod
    def _tier(cls, v: str) -> str:
        return v if v in TIERS else ""

    @field_validator("hook")
    @classmethod
    def _hook(cls, v: str) -> str:
        return v if v in ("强", "弱", "无") else ""

    @field_validator("pace")
    @classmethod
    def _pace(cls, v: str) -> str:
        return v if v in ("单笑点", "连续", "渐强") else ""

    @model_validator(mode="after")
    def _hook_ok(self):
        if self.hook:                                        # 三档为准：弱 / 无都算“开头不够抓人”
            self.hook_ok = self.hook == "强"
        return self

    @field_validator("evidence", mode="before")
    @classmethod
    def _evidence(cls, v):
        out = []
        for x in v or []:
            if isinstance(x, str):
                out.append({"t": 0, "quote": x})
            elif isinstance(x, dict):
                out.append(x)
        return out[:3]

    @field_validator("types", mode="before")
    @classmethod
    def _types(cls, v):
        out = []
        for x in v or []:
            x = str(x).strip()
            if x in TYPES and x != "无" and x not in out:
                out.append(x)
        return out[:2]

    @field_validator("highlight_times", mode="before")
    @classmethod
    def _ht(cls, v):
        out = []
        for x in v or []:
            try:
                out.append(float(x))
            except (TypeError, ValueError):
                pass
        return out[:3]

    @field_validator("start_reason", "end_reason")
    @classmethod
    def _reason(cls, v: str) -> str:
        return (v or "").strip()[:80]

    @field_validator("highlight_danmaku", mode="before")
    @classmethod
    def _dm(cls, v):
        return [str(x).strip()[:40] for x in (v or []) if str(x).strip()][:5]

    @field_validator("risks", "beats", "edit_hints", mode="before")
    @classmethod
    def _strs(cls, v):
        return [str(x).strip()[:80] for x in (v or []) if str(x).strip()][:6]

    @field_validator("context_caption", "punch_line", "hook_note")
    @classmethod
    def _short(cls, v: str) -> str:
        return (v or "").strip()[:60]

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        return v if v in TYPES else "其他"

    @field_validator("title_candidates")
    @classmethod
    def _titles(cls, v: list[str]) -> list[str]:
        clean = [str(t).strip().strip("《》「」\"“”").strip() for t in v]
        return [t[:40] for t in clean if t][:5]


def is_pick(llm: dict | None, taboo: bool, pick_score: float = 8.0, min_conf: float = 0.0) -> bool:
    """推荐发布：判为有趣 + 能单独看懂 + 起承转合完整 + 分数达标，且没命中禁区话题。
    v4 起再加：风险不能是 high、把握（confidence）达标；跑过同场二次比较的，还要被判“推荐”档。"""
    if not llm or taboo:
        return False
    if not (llm.get("funny") and llm.get("standalone") and llm.get("complete", True)
            and not llm.get("needs_context") and float(llm.get("score", 0)) >= pick_score):
        return False
    if llm.get("risk_level") == "high" or float(llm.get("confidence", 1.0)) < min_conf:
        return False
    if llm.get("in_character") is False:                     # 单独发出去会被误读成真失态 / 真冒犯：留给人看，不自动推荐
        return False
    return (llm.get("tier") or "推荐") == "推荐"           # 没跑二次比较（tier 为空）按单条结论


class ListItem(BaseModel):
    rank: int
    tier: str = "备选"
    reason: str = ""
    dup_of: int | None = None

    @field_validator("tier")
    @classmethod
    def _tier(cls, v: str) -> str:
        return v if v in TIERS else "备选"


class ListOut(BaseModel):
    items: list[ListItem] = Field(default_factory=list)


def listwise_pool(recs: list[dict], min_score: float, top_n: int) -> list[dict]:
    """进入同场二次比较的候选：判为有趣、分数够、没命中禁区，按分数（再按粗筛名次）取前 top_n 条。"""
    pool = [r for r in recs if r.get("llm") and r["llm"].get("funny") and not r.get("taboo")
            and float(r["llm"].get("score", 0)) >= min_score]
    pool.sort(key=lambda r: (-float(r["llm"]["score"]), r["rank"]))
    return pool[:top_n]


def list_item_text(r: dict) -> str:
    o = r["llm"]
    rf = r.get("refined") or {}
    dur = f"{rf['end'] - rf['start']:.0f}" if rf else "?"
    ev = "；".join(f"{e['quote']}" for e in o.get("evidence") or []) or "（没有引用）"
    flags = []
    if o.get("asr_doubt"):
        flags.append("笑点依赖的词可能被语音识别听错")
    if not o.get("hook_ok", True):
        flags.append(f"开头{o.get('hook') or '弱'}：" + (o.get("hook_note") or ""))
    if o.get("in_character") is False:
        flags.append("不符合人设 / 可能被误读：" + (o.get("in_character_note") or ""))
    if o.get("needs_context"):
        flags.append("要前情")
    if not o.get("complete", True):
        flags.append("结构不完整")
    if o.get("risk_level", "none") != "none":
        flags.append(f"风险 {o['risk_level']}：" + "；".join(o.get("risks") or []))
    return (f"#{r['rank']}｜时长 {dur} 秒｜{o['score']:g} 分｜{o['type']}｜把握 {float(o.get('confidence', 1)):.1f}\n"
            f"  发生了什么：{o['what_happened']}\n  笑点原话：{o.get('punch_line') or '（无）'}\n  证据：{ev}\n"
            f"  缺点 / 风险：{'；'.join(flags) or '无'}\n  编辑的理由：{o['reason']}")


def run_listwise(client, recs: list[dict], cfg: dict, rc: dict) -> str:
    """同场二次比较：给通过单条评审的候选定 推荐 / 备选 / 淘汰。失败或候选不足 2 条时什么也不改，返回说明。"""
    pool = listwise_pool(recs, float(rc["listwise_min_score"]), int(rc["listwise_top_n"]))
    if len(pool) < 2:
        return "候选不足 2 条，跳过二次比较"
    version, system, user = load_prompt(rc["listwise_prompt"])
    max_pick = int(rc["listwise_max_pick"])
    text = fill(user, profile=profile(cfg), items="\n\n".join(list_item_text(r) for r in pool))
    msgs = [{"role": "system", "content": fill(system, max_pick=str(max_pick))}, {"role": "user", "content": text}]
    r = client.chat_json(msgs, ListOut, tag=version)
    if r.data is None:
        return f"二次比较失败（沿用单条结论）：{(r.error or '')[:60]}"
    got = {it.rank: it for it in r.data.items}
    for rec in pool:
        it = got.get(rec["rank"])
        rec["llm"]["tier"] = it.tier if it else "备选"
        rec["listwise"] = {"reason": it.reason if it else "", "dup_of": it.dup_of if it else None}
    top = sorted((x for x in pool if x["llm"]["tier"] == "推荐"), key=lambda x: (-float(x["llm"]["score"]), x["rank"]))
    for x in top[max_pick:]:
        x["llm"]["tier"] = "备选"                                     # 推荐不超过 max_pick 条
        x["listwise"]["reason"] = (x["listwise"]["reason"] + "（超过本场推荐上限，降为备选）").strip()
    return f"二次比较 {len(pool)} 条：推荐 {sum(1 for x in pool if x['llm']['tier'] == '推荐')}、备选 {sum(1 for x in pool if x['llm']['tier'] == '备选')}、淘汰 {sum(1 for x in pool if x['llm']['tier'] == '淘汰')}"


def build_messages(c: dict, segs: list[dict], sents: list[dict], events, delay: float, cfg: dict,
                   rc: dict, frames_dir: Path, memory: str = "") -> tuple[list, str, int]:
    version, system, user = load_prompt(rc["prompt"])
    a, b = c["clip_start"], c["clip_end"]
    before = float(rc["context_before"])
    frames = extract_frames(segs, a, b, int(rc["frames"]), frames_dir, int(rc["width"]), peak=c["t"], mode=rc.get("frames_mode", "uniform"))
    hit = emotion_matcher(cfg.get("emotion_words", {}), cfg.get("lore_words", [])) if rc.get("danmaku_smart", True) else None
    dm, gifts = danmaku_lines(events, a, b, a, delay, limit=int(rc.get("danmaku_limit", 60)), peak=c["t"] if hit else None, hit=hit)
    extra = {"memory": memory or "（还没有积累）"} if "{{memory}}" in user else {}
    after = float(rc.get("context_after", 45))
    if "{{after_speech}}" in user:           # v3：给笑点之后的反应（v2 没有这个变量）
        extra["after_speech"] = speech_lines(sents, b, b + after, a, limit=1600) if sents else "（这场还没有转写）"
        extra["context_after"] = f"{after:.0f}"
    text = fill(user, profile=profile(cfg), **extra, window=f"{b - a:.0f}", peak=f"{c['t'] - a:.0f}",
                context_before=f"{before:.0f}",
                before_speech=speech_lines(sents, a - before, a, a, limit=1600) if sents else "（这场还没有转写）",
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
          "frames_mode": "uniform", "danmaku_smart": True, "danmaku_limit": 60, "min_len": 10, "max_len": 240, "context_after": 45, "pick_score": 8, "pick_confidence": 0.6,
          "listwise": False, "listwise_prompt": "rerank_list_v1", "listwise_min_score": 7, "listwise_top_n": 10,
          "listwise_max_pick": 3, "price_in": None, "price_out": None,
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
        end = a + min(o.end_time, (b - a) + float(rc["context_after"])) if o.end_time is not None else None
        rec["llm"] = o.model_dump()
        rec["pick"] = is_pick(rec["llm"], bool(rec["taboo"]), float(rc["pick_score"]), float(rc["pick_confidence"]))
        rec["refined"] = refine_bounds(sents, c["t"], punch, start, end, float(rc["min_len"]), float(rc["max_len"]))
        return rec

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, int(rc["workers"]))) as ex:
        recs = list(ex.map(one, cands))
    if args.dry_run:
        print(f"dry-run：提示词预览在 {sess / 'rerank_preview'}，截图在 {sess / 'rerank_frames'}")
        return 0

    lw_msg = ""
    if rc["listwise"]:
        lw_msg = run_listwise(client, recs, cfg, rc)
        for r in recs:
            if r.get("llm"):
                r["pick"] = is_pick(r["llm"], bool(r.get("taboo")), float(rc["pick_score"]), float(rc["pick_confidence"]))
        print(lw_msg)
    for r in recs:
        r["final"] = round(final_score(r.get("llm"), r["rank"], n_total, w, bool(r.get("taboo"))), 4)
    recs.sort(key=lambda r: -r["final"])
    for i, r in enumerate(recs, 1):
        r["new_rank"] = i

    cost = client.cost(rc["price_in"], rc["price_out"])
    out = {"session": sess.name, "model": client.model, "prompt": rc["prompt"], "memory_version": mem_version,
           "generated_at": time.strftime("%F %T"), "listwise": lw_msg,
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
        print(f"{r['new_rank']:>3} #{r['rank']:<2}  {hhmmss(r['t']):<9}{o['score']:>5.1f}{'★' if r.get('pick') else ' '} {'是' if o['funny'] else '否':<4}"
              f"{o['type']:<5}{hhmmss(rf['start'])}–{hhmmss(rf['end'])}（{rf['end'] - rf['start']:.0f}s）  {note_of(r)}{o['what_happened'][:40]}")
    picks = [r for r in out["candidates"] if r.get("pick")]
    print(f"\n推荐发布 {len(picks)} 条（★：有趣 + 能单独看懂 + 起承转合完整 + 分数 ≥ {rc['pick_score']:g}）"
          + ("：" + "、".join(f"#{r['rank']}" for r in picks) if picks else "：本场没有 AI 认为值得发的片段"))
    t = client.total
    print(f"\n用时 {time.time() - t0:.0f} 秒；请求 {t['calls']} 次（缓存命中 {t['cached']}），tokens 输入 {t['prompt_tokens']} / 输出 {t['completion_tokens']}"
          + (f"，约 ¥{cost:.3f}" if cost is not None else "（在 config 的 rerank.price_in / price_out 填单价可估算费用）"))
    print(f"结果：{old}")
    return 0


def note_of(r: dict) -> str:
    """结果表里一句话前面的小标注：二次比较的档位、风险、不推荐的原因。"""
    o = r.get("llm") or {}
    bits = []
    if o.get("tier"):
        bits.append(o["tier"])
    if o.get("risk_level") == "high":
        bits.append("风险高")
    if o.get("asr_doubt"):
        bits.append("听写存疑")
    if not r.get("pick") and o.get("fail_reason"):
        bits.append(o["fail_reason"])
    return "[" + "·".join(bits) + "] " if bits else ""


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
        c["pick"] = bool(c.get("pick")) and not c["taboo"]
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
