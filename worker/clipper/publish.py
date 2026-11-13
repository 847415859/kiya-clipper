"""发布包：封面、标题、简介、标签（B站投稿时手动复制粘贴，上传仍由人来做）。

封面 1920×1200（16:10，B站推荐比例），标题字压在中间 4:3 的安全区里，首页 4:3 裁切时字不会被切掉。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

W, H = 1920, 1200
SAFE_W = 1600                      # 中间 4:3 区域
TITLE_MAX = 80                     # B站标题上限
TAG_MAX, TAGS_MAX = 20, 10         # 单个标签字数、标签个数上限
DESC_MAX = 2000

DEFAULT_DESC = ("{what_happened}\n\n"
                "主播：{streamer} {space_url}\n"
                "直播间：{live_url}\n"
                "出自 {date} 的直播（约 {time}）\n\n"
                "非官方切片，仅为推广主播；如有不妥请联系删除。")
DEFAULT_TAGS = ["虚拟主播", "直播切片", "VTuber"]


def clean_title(t: str) -> str:
    t = re.sub(r"\s+", " ", str(t or "")).strip().strip("《》「」\"“”")
    return t[:TITLE_MAX]


def build_description(tpl: str | None, **v) -> str:
    text = (tpl or DEFAULT_DESC)
    for k, val in v.items():
        text = text.replace("{" + k + "}", str(val or ""))
    text = re.sub(r"\{[a-z_]+\}", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:DESC_MAX]


def build_tags(fixed: list[str], streamer: str, kind: str, lore: list[str], text: str) -> list[str]:
    """固定标签 + 主播名 + 类型 + 片段里出现过的梗（按长度优先，最多 10 个）。"""
    out: list[str] = []
    hits = sorted({w for w in lore if w and len(w) >= 2 and w.lower() in text.lower()}, key=len, reverse=True)
    for t in [streamer, *fixed, kind if kind and kind not in ("其他", "无") else "", *hits]:
        t = re.sub(r"[#,，\s]", "", str(t or ""))[:TAG_MAX]
        if t and t.lower() not in {x.lower() for x in out}:
            out.append(t)
        if len(out) >= TAGS_MAX:
            break
    return out


def find_font(spec: str) -> tuple[str, int]:
    """fc-match 找字体文件和 TTC 里的序号（Noto CJK 的 TTC 第 0 个是日文字形，简体要选 SC）。"""
    r = subprocess.run(["fc-match", "-f", "%{file}|%{index}", spec], capture_output=True, text=True)
    file, _, idx = r.stdout.partition("|")
    if not file or not Path(file).exists():
        raise RuntimeError(f"找不到字体：{spec}")
    return file, int(idx or 0)


def wrap(text: str, per_line: int, max_lines: int = 2) -> list[str]:
    text = text.strip()
    if len(text) <= per_line:
        return [text]
    for sep in ("！", "？", "：", "，", " ", "!", "?", ":", ","):       # 优先在标点处断行
        i = text.find(sep, len(text) // 3)
        if 0 < i < per_line + 3 and len(text) - i - 1 <= per_line + 3:   # 标点断行允许稍长，字号会自动缩
            return [text[: i + 1].strip(), text[i + 1:].strip()]
    lines = [text[i:i + per_line] for i in range(0, len(text), per_line)]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-1] + "…"
    return lines


def render_cover(frame: Path, out: Path, text: str, font_spec: str, color: str = "#FFE14D",
                 stroke: str = "#E0457B") -> None:
    """截图铺满 1920×1200，下方压暗，标题大字（描边 + 阴影）放在 4:3 安全区内。"""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    img = Image.open(frame).convert("RGB")
    s = max(W / img.width, H / img.height)
    img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    x0, y0 = (img.width - W) // 2, (img.height - H) // 2
    img = img.crop((x0, y0, x0 + W, y0 + H))
    shade = Image.new("L", (1, H))
    for y in range(H):                                   # 下半部分渐暗，让字更清楚
        shade.putpixel((0, y), int(max(0, (y - H * 0.45) / (H * 0.55)) * 170))
    img.paste(Image.new("RGB", (W, H), (0, 0, 0)), (0, 0), shade.resize((W, H)))
    text = text.strip()
    if text:
        file, idx = find_font(font_spec)
        lines = wrap(text, min(14, max(8, -(-len(text) // 2))))
        size = 210 if max(map(len, lines)) <= 7 else 170 if max(map(len, lines)) <= 9 else 128
        font = ImageFont.truetype(file, size, index=idx)
        draw = ImageDraw.Draw(img)
        while max(draw.textlength(l, font=font) for l in lines) > SAFE_W - 80 and size > 60:
            size -= 8
            font = ImageFont.truetype(file, size, index=idx)
        gap = int(size * 0.18)
        heights = [font.getbbox(l)[3] - font.getbbox(l)[1] for l in lines]
        y = H - 90 - sum(heights) - gap * (len(lines) - 1)
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow)
        yy = y
        for l, h in zip(lines, heights):
            tw = draw.textlength(l, font=font)
            sd.text(((W - tw) / 2 + 10, yy + 12), l, font=font, fill=(0, 0, 0, 180),
                    stroke_width=max(6, size // 10), stroke_fill=(0, 0, 0, 180))
            yy += h + gap
        img.paste(shadow.filter(ImageFilter.GaussianBlur(8)), (0, 0), shadow.filter(ImageFilter.GaussianBlur(8)))
        draw = ImageDraw.Draw(img)
        for l, h in zip(lines, heights):
            tw = draw.textlength(l, font=font)
            draw.text(((W - tw) / 2, y - font.getbbox(l)[1]), l, font=font, fill=color,
                      stroke_width=max(6, size // 12), stroke_fill=stroke)
            y += h + gap
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "JPEG", quality=90)


# 主题化封面：封面不是把标题放到截图上，而是把精排结论转成一个视觉隐喻。
COVER_THEMES = {
    "contrast": {"label": "反差现场", "accent": "#FF3D71", "second": "#FFD43B", "symbol": "?!"},
    "meltdown": {"label": "情绪超载", "accent": "#FF4D22", "second": "#FFE14D", "symbol": "!!!"},
    "wordplay": {"label": "语言事故", "accent": "#8B5CF6", "second": "#67E8F9", "symbol": "?"},
    "game_fail": {"label": "操作失误", "accent": "#16A34A", "second": "#FACC15", "symbol": "MISS"},
    "social_death": {"label": "当场社死", "accent": "#EC4899", "second": "#F8FAFC", "symbol": "LIVE"},
    "chaos": {"label": "抽象事件", "accent": "#F97316", "second": "#38BDF8", "symbol": "???"},
}


def cover_concept(llm: dict | None, title: str = "", speech: str = "") -> dict:
    """从精排结果生成稳定、可解释的视觉概念，不依赖额外的图片生成服务。"""
    llm = llm or {}
    text = " ".join(str(x) for x in (title, llm.get("what_happened", ""), speech)).lower()
    kind = str(llm.get("type") or "").lower()
    if any(x in text for x in ("口胡", "说错", "念错", "误会", "错字")) or "口胡" in kind:
        theme = "wordplay"
    elif any(x in text for x in ("破防", "崩溃", "失控", "尖叫")) or "破防" in kind:
        theme = "meltdown"
    elif any(x in text for x in ("社死", "尴尬", "被发现")):
        theme = "social_death"
    elif any(x in text for x in ("游戏", "操作", "翻车", "失败", "寄")):
        theme = "game_fail"
    elif any(x in text for x in ("反差", "结果", "居然", "原来", "寄成")) or "反差" in kind:
        theme = "contrast"
    else:
        theme = "chaos"
    preset = COVER_THEMES[theme]
    headline = str(llm.get("cover_text") or title or llm.get("punch_line") or "这也能发生？").strip()
    headline = re.sub(r"^[《【「『]|[》】」』]$", "", headline)[:18]
    if len(headline) > 10 and any(x in headline for x in ("！", "？", "？！", "!?")):
        headline = headline[:10] + "？"
    sub = str(llm.get("what_happened") or llm.get("reason") or preset["label"]).strip()[:26]
    return {"theme": theme, "label": preset["label"], "headline": headline or preset["label"],
            "subline": sub, "accent": preset["accent"], "second": preset["second"], "symbol": preset["symbol"]}


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _font(font_spec: str, size: int):
    from PIL import ImageFont
    file, idx = find_font(font_spec)
    return ImageFont.truetype(file, size, index=idx)


def _text_width(draw, text: str, font) -> float:
    if hasattr(draw, "textlength"):
        return draw.textlength(text, font=font)
    box = draw.textbbox((0, 0), text, font=font) if hasattr(draw, "textbbox") else draw.textsize(text, font=font)
    return box[2] - box[0] if len(box) >= 4 else box[0]


def _text_height(font, text: str) -> int:
    if hasattr(font, "getbbox"):
        box = font.getbbox(text)
        return box[3] - box[1]
    return font.getsize(text)[1]


def _fit_font(draw, text: str, font_spec: str, max_size: int, max_width: int):
    size = max_size
    while size >= 48:
        f = _font(font_spec, size)
        if _text_width(draw, text, f) <= max_width:
            return f
        size -= 8
    return _font(font_spec, 48)


def render_theme_cover(frame: Path, out: Path, concept: dict, font_spec: str,
                       variant: str = "hero") -> None:
    """生成主题化封面。variant: hero（人物海报）、comic（漫画冲击）、abstract（抽象事件卡）。"""
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

    img = Image.open(frame).convert("RGB")
    accent, second = _hex(concept.get("accent", "#FF3D71")), _hex(concept.get("second", "#FFD43B"))
    scale = max(W / img.width, H / img.height)
    lanczos = getattr(Image, "Resampling", Image).LANCZOS
    base = img.resize((round(img.width * scale), round(img.height * scale)), lanczos)
    x0, y0 = (base.width - W) // 2, (base.height - H) // 2
    base = base.crop((x0, y0, x0 + W, y0 + H))
    base = ImageEnhance.Contrast(ImageEnhance.Color(base).enhance(1.25)).enhance(1.12)
    canvas = Image.new("RGB", (W, H), (18, 18, 24))
    if variant == "abstract":
        blurred = base.filter(ImageFilter.GaussianBlur(18))
        canvas.paste(blurred, (0, 0))
        shade = Image.new("RGBA", (W, H), (*accent, 145))
        canvas = Image.alpha_composite(canvas.convert("RGBA"), shade)
        canvas.paste(base.crop((250, 115, 1670, 1085)).resize((1420, 970), lanczos), (250, 115))
    elif variant == "comic":
        canvas.paste(base, (0, 0))
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        for x in range(-H, W, 85):
            od.line((x, H, x + H, 0), fill=(*second, 55), width=18)
        canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay)
    else:
        canvas.paste(base, (0, 0))
    draw = ImageDraw.Draw(canvas)
    # 左侧主题条和右上角符号，形成每类内容稳定的识别系统。
    if hasattr(draw, "rounded_rectangle"):
        draw.rounded_rectangle((70, 70, 430, 142), radius=24, fill=accent)
    else:
        draw.rectangle((70, 70, 430, 142), fill=accent)
    label_font = _fit_font(draw, concept.get("label", "抽象事件"), font_spec, 38, 300)
    draw.text((105, 84), concept.get("label", "抽象事件"), font=label_font, fill=(255, 255, 255))
    symbol_font = _fit_font(draw, concept.get("symbol", "?!"), font_spec, 190, 430)
    draw.text((W - 470, 30), concept.get("symbol", "?!"), font=symbol_font, fill=(*second, 235), stroke_width=8, stroke_fill=accent)
    # 事件卡用大色块承载抽象隐喻，人物和背景仍然可见。
    if variant == "abstract":
        draw.polygon([(0, 790), (W, 650), (W, H), (0, H)], fill=(*accent, 225))
    elif variant == "comic":
        draw.polygon([(0, 850), (W, 690), (W, H), (0, H)], fill=(10, 10, 16, 205))
    else:
        draw.rectangle((0, 760, W, H), fill=(8, 8, 14, 195))
    headline = concept.get("headline", "")
    lines = wrap(headline, 9, 2)
    size = 205 if max(map(len, lines)) <= 6 else 165
    headline_font = _fit_font(draw, max(lines, key=len), font_spec, size, SAFE_W - 100)
    heights = [_text_height(headline_font, line) for line in lines]
    gap = 12
    y = 805
    for line, height in zip(lines, heights):
        tw = _text_width(draw, line, headline_font)
        draw.text(((W - tw) / 2 + 10, y + 12), line, font=headline_font, fill=(0, 0, 0), stroke_width=16, stroke_fill=(0, 0, 0))
        draw.text(((W - tw) / 2, y), line, font=headline_font, fill=second, stroke_width=9, stroke_fill=accent)
        y += height + gap
    sub = concept.get("subline", "")
    if sub:
        sf = _fit_font(draw, sub, font_spec, 46, SAFE_W - 160)
        tw = _text_width(draw, sub, sf)
        draw.text(((W - tw) / 2, min(H - 70, y + 6)), sub, font=sf, fill=(255, 255, 255))
    # 角标和边框使缩略图在信息流中仍有明确轮廓。
    draw.line((35, 35, W - 35, 35), fill=second, width=8)
    draw.line((35, H - 35, W - 35, H - 35), fill=accent, width=8)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out, "JPEG", quality=93, subsampling=0)
