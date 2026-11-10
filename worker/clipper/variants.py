"""剪辑初稿的多版本：同一个候选按不同风格档位各生成一版，审核时在编辑器里切换着比较、挑一版接着改。

档位（对应技术方案里的 纯享 / 综艺 / 整活）：
  restrained 克制：只在笑点出 1–3 个效果，最多 1 个梗，几乎不用贴图——主播本人的包袱最重要
  standard   标准：现在的默认编排
  lively     热闹：效果更密、优先用梗和贴图，仍受同一时刻最多 2 个效果、梗的数量和间隔限制

只有 AI 评分靠前的前 top_n 个候选才生成多版本（多一版 = 多一次大模型调用），其余候选只出标准版。
文件：edits/<序号>.variants.json = {chosen, chosen_by, variants:[{key, label, edit, ...}]}；
edits/<序号>.json 是当前选中的那一版（编辑器改的就是它），edits/<序号>.ai.json 也跟着换成选中的那一版（剪辑口味按它对比）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

DEFAULT = "standard"
STYLES = {
    "restrained": {"label": "克制", "brief": "克制版：整条只在笑点瞬间出 1–3 个效果，宁可什么都不加也不要干扰主播的包袱；至多 1 个梗，几乎不用贴图；不要铺垫阶段的效果。",
                   "max_items": 3, "max_memes": 1},
    "standard": {"label": "标准", "brief": "标准版：按上面的原则正常编排，铺垫克制、笑点集中、反应补一个。", "max_items": None, "max_memes": None},
    "lively": {"label": "热闹", "brief": "热闹版：综艺感拉满——笑点前后都可以加花字和反应贴图，优先把合适的梗用上（仍受梗数量和间隔限制）；运镜可以带震屏；仍然不要盖住主播的脸，也不能让同一时刻超过 2 个效果。",
               "max_items": 3, "max_memes": None},      # max_items 填的是在配置的 max_items 基础上多加几个
}


def variants_cfg(ac: dict) -> dict:
    v = {"top_n": 5, "styles": ["restrained", "standard", "lively"], **(ac.get("variants") or {})}
    v["styles"] = [k for k in v["styles"] if k in STYLES] or [DEFAULT]
    return v


def resolve(key: str, max_items: int, max_memes: int) -> dict:
    """某个档位的实际参数：{key, label, brief, max_items, max_memes}。"""
    s = STYLES[key]
    if key == "restrained":
        mi, mm = min(max_items, s["max_items"]), min(max_memes, s["max_memes"])
    elif key == "lively":
        mi, mm = max_items + s["max_items"], max_memes
    else:
        mi, mm = max_items, max_memes
    return {"key": key, "label": s["label"], "brief": s["brief"], "max_items": mi, "max_memes": mm}


def wants_variants(mode: str, new_rank: int | None, rank: int, top_n: int) -> tuple[bool, str]:
    """mode: 'off' 只出标准版 / 'auto' 前 top_n 个出多版本。返回 (是否多版本, 说明)。"""
    if mode == "off":
        return False, ""
    pos = new_rank if new_rank else rank
    if pos <= top_n:
        return True, ""
    return False, f"排名第 {pos}，不在前 {top_n} 个，只生成了标准版（要改范围调 autoedit.variants.top_n）"


def path_of(edits_dir: Path, rank: int) -> Path:
    return edits_dir / f"{rank:02d}.variants.json"


def load(edits_dir: Path, rank: int) -> dict | None:
    try:
        return json.loads(path_of(edits_dir, rank).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save(edits_dir: Path, rank: int, variants: list[dict], chosen: str, meta: dict) -> None:
    p = path_of(edits_dir, rank)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({**meta, "chosen": chosen, "chosen_by": "default", "chosen_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                               "variants": variants}, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def summary(v: dict | None) -> list[dict]:
    """给后台的概览：[{key, label, items, memes, note}]。"""
    out = []
    for x in (v or {}).get("variants") or []:
        e = x.get("edit") or {}
        out.append({"key": x["key"], "label": x.get("label", x["key"]), "items": len(e.get("items") or []),
                    "memes": sum(1 for i in e.get("items") or [] if i.get("meme")), "note": e.get("note", "")})
    return out
