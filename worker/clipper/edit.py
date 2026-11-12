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
NARRATION_STYLES = ("strip", "bubble")                      # 吐槽旁白：条带 / 气泡
FAKEUI_KINDS = ("popup", "chat", "achievement", "notice")    # 伪 UI：系统弹窗 / 聊天消息 / 成就解锁 / 任务通知条
FAKEUI_DEFAULTS = {                                          # 每种伪 UI 的默认标题、宽度（占画面宽度的比例）和位置
    "popup": {"title": "系统提示", "scale": 0.34, "x": 0.5, "y": 0.4},
    "chat": {"title": "路人", "scale": 0.36, "x": 0.27, "y": 0.3},
    "achievement": {"title": "成就解锁", "scale": 0.4, "x": 0.5, "y": 0.14},
    "notice": {"title": "任务", "scale": 0.5, "x": 0.5, "y": 0.1},
}
ITEM_TYPES = ("text", "zoom", "sticker", "narration", "fakeui")
COLOR = re.compile(r"^#([0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
ASSET = re.compile(r"^[\w\-.一-鿿]{1,80}\.(png|jpg|jpeg|gif|webp)$", re.I)
MAX_ITEMS = 100
MAX_WINDOW = 500.0       # 素材范围最长 500 秒（成片最长 480 秒，留出前后余量）
MIN_WINDOW = 5.0


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
    if t not in ITEM_TYPES:
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
    elif t == "narration":
        text = it.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 50:
            raise EditError("旁白内容 1–50 个字")
        tag = it.get("tag", "吐槽")
        if not isinstance(tag, str) or len(tag.strip()) > 8:
            raise EditError("旁白小标签最多 8 个字（可以留空）")
        out.update(text=text.strip("\n"), tag=tag.strip(), style=_choice(it, "style", NARRATION_STYLES, "strip"),
                   preset=_choice(it, "preset", STICKER_PRESETS, "fade"), x=_num(it, "x", 0, 1, 0.5),
                   y=_num(it, "y", 0, 1, 0.16), size=_num(it, "size", 24, 120, 54), color=_color(it, "color", "#FF6B8B"))
    elif t == "fakeui":
        kind = it.get("kind")
        if kind not in FAKEUI_KINDS:
            raise EditError(f"伪 UI 的 kind 只能是 {', '.join(FAKEUI_KINDS)}")
        d = FAKEUI_DEFAULTS[kind]
        text = it.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 40:
            raise EditError("伪 UI 内容 1–40 个字")
        title = it.get("title", d["title"])
        if not isinstance(title, str) or len(title.strip()) > 16:
            raise EditError("伪 UI 标题最多 16 个字")
        out.update(kind=kind, title=title.strip(), text=text.strip("\n"), preset=_choice(it, "preset", STICKER_PRESETS, "pop"),
                   x=_num(it, "x", 0, 1, d["x"]), y=_num(it, "y", 0, 1, d["y"]), scale=_num(it, "scale", 0.15, 0.8, d["scale"]))
    else:
        asset = it.get("asset")
        if not isinstance(asset, str) or not ASSET.match(asset):
            raise EditError("贴图文件名不合法")
        out.update(asset=asset, preset=_choice(it, "preset", STICKER_PRESETS, "pop"), x=_num(it, "x", 0, 1, 0.8),
                   y=_num(it, "y", 0, 1, 0.65), scale=_num(it, "scale", 0.03, 1, 0.22),
                   rotate=_num(it, "rotate", -45, 45, 0))
    meme = it.get("meme")                       # 梗库里的梗（可选，只有花字 / 贴图能挂）
    if t in ("text", "sticker", "narration") and isinstance(meme, str) and meme.strip():
        out["meme"] = meme.strip()[:40]
        if isinstance(it.get("meme_reason"), str) and it["meme_reason"].strip():
            out["meme_reason"] = it["meme_reason"].strip()[:80]
    return out


def validate_window(w) -> dict:
    """素材范围（整场秒数）。可以超出粗筛的候选片段，成片从录像里重新剪。"""
    if not isinstance(w, dict):
        raise EditError("window 格式不对")
    start = _num(w, "start", 0, 24 * 3600)
    end = _num(w, "end", 0, 24 * 3600)
    if not MIN_WINDOW <= end - start <= MAX_WINDOW:
        raise EditError(f"素材范围要在 {MIN_WINDOW:.0f} 秒到 {MAX_WINDOW:.0f} 秒之间")
    return {"start": round(start, 1), "end": round(end, 1)}      # 一位小数：预览文件名在 Java / Python 两边一致


def edit_window(edit: dict, cand: dict) -> tuple[float, float]:
    """剪辑脚本的素材范围；旧脚本没有 window 就是候选片段本身。"""
    w = edit.get("window")
    if isinstance(w, dict) and "start" in w and "end" in w:
        return float(w["start"]), float(w["end"])
    return float(cand["clip_start"]), float(cand["clip_end"])


def suggest_window(cand: dict, refined: dict | None, pad: float = 5.0) -> dict:
    """默认素材范围 = 候选片段 ∪ 精排修正后的起止（前后各留 pad 秒，方便手动微调），最长 MAX_WINDOW。"""
    a, b = float(cand["clip_start"]), float(cand["clip_end"])
    if refined:
        rs, re_ = float(refined["start"]), float(refined["end"])
        a, b = min(a, rs - pad), max(b, re_ + pad)
        if b - a > MAX_WINDOW:                      # 太长：以精排的起止为准
            a, b = rs - pad, rs - pad + MAX_WINDOW
            if b < re_:
                a, b = rs, rs + MAX_WINDOW
    a = max(0.0, a)
    return {"start": round(a, 1), "end": round(max(b, a + MIN_WINDOW), 1)}


def validate_music(m) -> dict | None:
    """背景音乐设置：track = 曲库里的 id；start = 素材范围开头对应曲子的第几秒；duck = 说话时压低；snap = 效果吸附鼓点。"""
    if m is None or (isinstance(m, dict) and not m.get("track")):
        return None
    if not isinstance(m, dict) or not isinstance(m.get("track"), str) or not m["track"].strip() or len(m["track"]) > 80:
        raise EditError("music.track 不合法")
    return {"track": m["track"].strip(), "start": round(_num(m, "start", 0, 3600, 0.0), 2),
            "volume": round(_num(m, "volume", 0.02, 1, 0.18), 3), "duck": bool(m.get("duck", True)), "snap": bool(m.get("snap", False))}


CALLBACK_RELATIONS = ("flag_payoff", "reversal")
CALLBACK_MODES = ("clip", "card")
SESSION_NAME = re.compile(r"^[\w\-.]{1,64}$")


def validate_callback(c) -> dict | None:
    """跨场次呼应：成片开头插一段旧场次的画面（mode=clip）或只放一张文字卡（mode=card）。
    session = 旧场次名；start / end = 旧场次录像里的秒数（1.5–12 秒）；label = 屏幕上的标签（≤40 字，由程序用校验过的原话拼）。"""
    if c is None or (isinstance(c, dict) and not c.get("session")):
        return None
    if not isinstance(c, dict):
        raise EditError("callback 格式不对")
    session = c.get("session")
    if not isinstance(session, str) or not SESSION_NAME.match(session):
        raise EditError("callback.session 不合法")
    start = _num(c, "start", 0, 10 ** 6, 0.0)
    end = _num(c, "end", 0, 10 ** 6, start + 4)
    if not 1.5 <= end - start <= 12:
        raise EditError("callback 旧画面要 1.5–12 秒")
    label = c.get("label")
    if not isinstance(label, str) or not label.strip() or len(label.strip()) > 40:
        raise EditError("callback.label 1–40 个字")
    out = {"session": session, "start": round(start, 2), "end": round(end, 2), "label": label.strip(),
           "relation": _choice(c, "relation", CALLBACK_RELATIONS, "flag_payoff"), "mode": _choice(c, "mode", CALLBACK_MODES, "clip"),
           "enabled": bool(c.get("enabled", True))}
    for k, n in (("date", 12), ("quote", 120), ("current_quote", 120), ("note", 200)):
        v = c.get(k, "")
        if not isinstance(v, str) or len(v) > n:
            raise EditError(f"callback.{k} 不合法")
        out[k] = v
    conf = c.get("confidence")
    if isinstance(conf, (int, float)) and not isinstance(conf, bool):
        out["confidence"] = round(float(conf), 2)
    return out


def validate_edit(raw: dict, clip_len: float) -> dict:
    """校验并补默认值。时间都相对素材范围开头；有 window 时 clip_len 取素材范围长度，否则是候选片段长度。"""
    if not isinstance(raw, dict):
        raise EditError("剪辑脚本格式不对")
    window = validate_window(raw["window"]) if raw.get("window") is not None else None
    if window:
        clip_len = window["end"] - window["start"]
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
    if window:
        out["window"] = window
    music = validate_music(raw.get("music"))
    if music:
        out["music"] = music
    cb = validate_callback(raw.get("callback"))
    if cb:
        out["callback"] = cb
    if isinstance(subs.get("style"), dict):          # 后台保存时把选中的样式整份写进来
        out["subtitles"]["style"] = subs["style"]
        out["subtitles"]["style_name"] = str(subs.get("style_name") or "")[:64]
    for k in ("session", "rank", "updated_at", "generated_by", "note"):
        if k in raw:
            out[k] = raw[k]
    return out


def default_edit(clip_len: float) -> dict:
    return {"version": 1, "trim": {"start": 0.0, "end": clip_len}, "subtitles": {"enabled": True}, "items": []}


# ---------------------------------------------------------------- 密度约束
MAX_CONCURRENT = 2       # 同一时刻最多两个效果（花字 / 贴图 / 运镜都算）
ZOOM_GAP = 10.0          # 两次推镜开始至少隔 10 秒


def density_problems(edit: dict) -> list[str]:
    """检查效果是否太密，返回问题描述（不改脚本）。"""
    items = sorted(edit.get("items") or [], key=lambda i: i["start"])
    out = []
    for i, it in enumerate(items):
        t = it["start"]
        active = [x for x in items if x["start"] <= t < x["start"] + x["duration"]]
        if len(active) > MAX_CONCURRENT:
            out.append(f"{t:.1f} 秒处同时有 {len(active)} 个效果（建议最多 {MAX_CONCURRENT} 个）")
    zooms = [i for i in items if i["type"] == "zoom"]
    for a, b in zip(zooms, zooms[1:]):
        if b["start"] - a["start"] < ZOOM_GAP:
            out.append(f"{a['start']:.1f} 秒和 {b['start']:.1f} 秒两次推镜间隔不到 {ZOOM_GAP:.0f} 秒")
    return list(dict.fromkeys(out))


def enforce_density(edit: dict) -> tuple[dict, list[dict]]:
    """按时间顺序保留效果，丢掉会让画面太乱的（自动生成的脚本用）。返回 (新脚本, 被丢掉的效果)。"""
    kept, dropped = [], []
    last_zoom = None
    for it in sorted(edit.get("items") or [], key=lambda i: i["start"]):
        active = [x for x in kept if x["start"] <= it["start"] < x["start"] + x["duration"]]
        if len(active) >= MAX_CONCURRENT:
            dropped.append(it)
            continue
        if it["type"] == "zoom":
            if last_zoom is not None and it["start"] - last_zoom < ZOOM_GAP:
                dropped.append(it)
                continue
            last_zoom = it["start"]
        kept.append(it)
    return dict(edit, items=kept), dropped


def limit_stickers(items: list[dict], max_per_clip: int = 2) -> tuple[list[dict], list[dict]]:
    """贴图：同一个贴图整条只能用一次，整条最多 max_per_clip 个（按时间先后留，其余丢掉）。返回 (新列表, 被丢掉的)。"""
    kept, dropped, seen, n = [], [], set(), 0
    for it in sorted(items, key=lambda i: i["start"]):
        if it["type"] == "sticker":
            if it["asset"] in seen or n >= max_per_clip:
                dropped.append(it)
                continue
            seen.add(it["asset"])
            n += 1
        kept.append(it)
    return kept, dropped


def ensure_caption(items: list[dict], caption: str, trim: dict) -> list[dict]:
    """精排给了前情字幕，但大模型没放（或放歪了）：在成片开头补一条。不需要字幕、或开头 6 秒内已有这段字就原样返回。"""
    caption = (caption or "").strip()[:40]
    if not caption or caption.startswith("（"):
        return items
    head = caption[:4]
    t0, t1 = float(trim["start"]), float(trim["end"])
    if any(i["type"] == "text" and head in i.get("text", "") and i["start"] < t0 + 6 for i in items):
        return items
    dur = round(min(3.0, max(1.0, t1 - t0 - 0.4)), 2)
    cap = validate_item({"type": "text", "text": caption, "start": round(t0 + 0.2, 2), "duration": dur, "preset": "fade",
                         "x": 0.5, "y": 0.12, "size": 70, "color": "#FFFFFF"}, t1)
    return [cap] + [i for i in items]


SNAP_TOL = 0.2          # 效果开始时刻离最近的鼓点不超过 0.2 秒才吸附（再远就是故意放在别处）


def snap_to_beats(items: list[dict], beats: list[float], offset: float, clip_len: float, tol: float = SNAP_TOL) -> tuple[list[dict], int]:
    """把效果的开始时刻挪到最近的鼓点（时长不变，不动原话）。beats 是曲子里的时刻，offset = 素材范围开头对应曲子的第几秒。
    返回 (新列表, 挪动了几个)。重复调用结果不变。"""
    import bisect
    bt = sorted(round(b - offset, 3) for b in beats if 0 <= b - offset <= clip_len)
    if not bt:
        return items, 0
    out, moved = [], 0
    for it in items:
        i = bisect.bisect_left(bt, it["start"])
        near = min((bt[j] for j in (i - 1, i) if 0 <= j < len(bt)), key=lambda b: abs(b - it["start"]))
        if abs(near - it["start"]) <= tol and abs(near - it["start"]) > 1e-6:
            it = dict(it, start=min(near, max(0.0, clip_len - 0.2)))
            moved += 1
        out.append(it)
    out.sort(key=lambda i: i["start"])
    return out, moved


MAX_NARRATION = 2        # AI 初稿：吐槽旁白最多 2 条、间隔至少 8 秒；伪 UI 最多 2 个
NARRATION_GAP = 8.0
MAX_FAKEUI = 2


def limit_overlays(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """AI 初稿里新模板（旁白 / 伪 UI）不能泛滥：按时间先后保留，其余丢掉。返回 (新列表, 被丢掉的)。"""
    kept, dropped, last_narr, n_narr, n_ui = [], [], None, 0, 0
    for it in sorted(items, key=lambda i: i["start"]):
        if it["type"] == "narration":
            if n_narr >= MAX_NARRATION or (last_narr is not None and it["start"] - last_narr < NARRATION_GAP):
                dropped.append(it)
                continue
            n_narr += 1
            last_narr = it["start"]
        elif it["type"] == "fakeui":
            if n_ui >= MAX_FAKEUI:
                dropped.append(it)
                continue
            n_ui += 1
        kept.append(it)
    return kept, dropped


def missing_assets(edit: dict, sticker_dir) -> list[str]:
    from pathlib import Path
    d = Path(sticker_dir)
    return sorted({i["asset"] for i in edit.get("items") or [] if i["type"] == "sticker" and not (d / i["asset"]).exists()})
