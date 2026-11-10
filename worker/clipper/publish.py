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
