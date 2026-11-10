"""剪辑脚本（成片包装）：一个候选片段的裁剪范围、字幕设置和按时间排布的效果（花字 / 放大运镜 / 贴图）。

保存在 output/<场次>/edits/<序号>.json，由后台编辑器生成；render.py 按它调用 Remotion 渲染成片。
字段说明见 schema/edit_script.schema.json。时间都是“相对候选片段开头”的秒数（与审核页播放的片段一致）。
这里的校验是唯一标准：后台保存时也调用它（scripts/edit_check.py）。
"""
from __future__ import annotations

import re
import uuid

TEXT_PRESETS = ("pop", "slam", "shake", "bounce", "fade")
STICKER_PRESETS = ("pop", "slide", "shake", "bounce", "fade", "slam")
COLOR = re.compile(r"^#([0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
ASSET = re.compile(r"^[\w\-.一-鿿]{1,80}\.(png|jpg|jpeg|gif|webp)$", re.I)
MAX_ITEMS = 100


class EditError(ValueError):
    pass


def _num(d: dict, k: str, lo: float, hi: float, default=None) -> float:
    v = d.get(k, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise EditError(f"{k} 应为数字")
    if not lo <= v <= hi:
        raise EditError(f"{k} 超出范围 {lo}–{hi}：{v}")
    return float(v)


def _color(d: dict, k: str, default: str) -> str:
    v = d.get(k, default)
    if not isinstance(v, str) or not COLOR.match(v):
        raise EditError(f"{k} 颜色格式不对（#RRGGBB 或 #RRGGBBAA）")
    return v.upper()


def _choice(d: dict, k: str, options, default: str) -> str:
    v = d.get(k, default)
    if v not in options:
        raise EditError(f"{k} 只能是 {', '.join(options)}")
    return v


def validate_item(it: dict, clip_len: float) -> dict:
    if not isinstance(it, dict):
        raise EditError("效果格式不对")
    t = it.get("type")
    if t not in ("text", "zoom", "sticker"):
        raise EditError(f"未知效果类型：{t}")
    out = {"id": str(it.get("id") or uuid.uuid4().hex[:8])[:32], "type": t,
           "start": _num(it, "start", 0, clip_len), "duration": _num(it, "duration", 0.2, 30, 2.0)}
    if t == "text":
        text = it.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 40:
            raise EditError("花字内容 1–40 个字")
        out.update(text=text.strip("\n"), preset=_choice(it, "preset", TEXT_PRESETS, "pop"),
                   x=_num(it, "x", 0, 1, 0.5), y=_num(it, "y", 0, 1, 0.3), size=_num(it, "size", 24, 300, 110),
                   color=_color(it, "color", "#FFE14D"), stroke=_color(it, "stroke", "#E0457B"),
                   stroke_width=_num(it, "stroke_width", 0, 30, 8), rotate=_num(it, "rotate", -45, 45, 0))
        font = it.get("font") or "Noto Sans CJK SC"
        if not isinstance(font, str) or len(font) > 64:
            raise EditError("font 不合法")
        out["font"] = font
    elif t == "zoom":
        out.update(scale=_num(it, "scale", 1.05, 3, 1.5), x=_num(it, "x", 0, 1, 0.5), y=_num(it, "y", 0, 1, 0.45),
                   shake=_num(it, "shake", 0, 1, 0), ease_in=_num(it, "ease_in", 0.05, 2, 0.25),
                   ease_out=_num(it, "ease_out", 0.05, 2, 0.3))
    else:
        asset = it.get("asset")
        if not isinstance(asset, str) or not ASSET.match(asset):
            raise EditError("贴图文件名不合法")
        out.update(asset=asset, preset=_choice(it, "preset", STICKER_PRESETS, "pop"), x=_num(it, "x", 0, 1, 0.8),
                   y=_num(it, "y", 0, 1, 0.65), scale=_num(it, "scale", 0.03, 1, 0.22),
                   rotate=_num(it, "rotate", -45, 45, 0))
    return out


def validate_edit(raw: dict, clip_len: float) -> dict:
    """校验并补默认值。clip_len = 候选片段长度（秒）。"""
    if not isinstance(raw, dict):
        raise EditError("剪辑脚本格式不对")
    trim = raw.get("trim") or {}
    start = _num(trim, "start", 0, clip_len, 0.0)
    end = _num(trim, "end", 0, clip_len, clip_len)
    if end - start < 1:
        raise EditError("裁剪后至少要 1 秒")
    subs = raw.get("subtitles") or {}
    if not isinstance(subs, dict):
        raise EditError("subtitles 格式不对")
    items = raw.get("items") or []
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise EditError(f"效果最多 {MAX_ITEMS} 个")
    out_items = [validate_item(it, clip_len) for it in items]
    out_items.sort(key=lambda i: i["start"])
    out = {"version": 1, "trim": {"start": start, "end": end},
           "subtitles": {"enabled": bool(subs.get("enabled", True))}, "items": out_items}
    if isinstance(subs.get("style"), dict):          # 后台保存时把选中的样式整份写进来
        out["subtitles"]["style"] = subs["style"]
        out["subtitles"]["style_name"] = str(subs.get("style_name") or "")[:64]
    for k in ("session", "rank", "updated_at"):
        if k in raw:
            out[k] = raw[k]
    return out


def default_edit(clip_len: float) -> dict:
    return {"version": 1, "trim": {"start": 0.0, "end": clip_len}, "subtitles": {"enabled": True}, "items": []}
