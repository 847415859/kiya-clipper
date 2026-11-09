"""字幕（技术方案“模块三 风格化包装”的第一步）：转写结果 → 按样式生成 ASS / SRT → 剪切时烧录。

样式结构由 config/schema/subtitle_style.schema.json 定义（默认值、取值范围都在里面），
config/subtitles.yaml 里只写和默认值不同的字段。以后后台管理用同一个 schema 编辑样式。
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO / "config" / "schema" / "subtitle_style.schema.json"
CONFIG_PATH = REPO / "config" / "subtitles.yaml"
FONTS_DIR = REPO / "fonts"

BREAK_PUNCT = "，。？！、；：,.?!;:…"
STRIP_PUNCT = "，。、；：,.;:"
PLAY_RES_Y = 1080


class StyleError(ValueError):
    pass


# ---------------------------------------------------------------- 样式
def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_style(raw: dict, schema: dict | None = None) -> dict:
    """按 schema 补默认值、校验类型 / 范围 / 枚举 / 颜色格式，返回完整样式。"""
    schema = schema or load_schema()
    props = schema["properties"]
    unknown = set(raw or {}) - set(props)
    if unknown:
        raise StyleError(f"未知字段：{', '.join(sorted(unknown))}")
    out = {}
    for k, p in props.items():
        v = (raw or {}).get(k, p.get("default"))
        t = p["type"]
        if t == "integer" and not (isinstance(v, int) and not isinstance(v, bool)):
            raise StyleError(f"{k}（{p['title']}）应为整数：{v!r}")
        if t == "number" and not (isinstance(v, (int, float)) and not isinstance(v, bool)):
            raise StyleError(f"{k}（{p['title']}）应为数字：{v!r}")
        if t == "boolean" and not isinstance(v, bool):
            raise StyleError(f"{k}（{p['title']}）应为 true / false：{v!r}")
        if t == "string":
            if not isinstance(v, str):
                raise StyleError(f"{k}（{p['title']}）应为文字：{v!r}")
            if "pattern" in p and not re.match(p["pattern"], v):
                raise StyleError(f"{k}（{p['title']}）格式不对：{v!r}（颜色写成 #RRGGBB 或 #RRGGBBAA）")
            if "enum" in p and v not in p["enum"]:
                raise StyleError(f"{k}（{p['title']}）只能是 {p['enum']}：{v!r}")
        if t == "array":
            if not isinstance(v, list):
                raise StyleError(f"{k}（{p['title']}）应为列表：{v!r}")
            v = [str(x) for x in v]
        if t in ("integer", "number"):
            if "minimum" in p and v < p["minimum"] or "maximum" in p and v > p["maximum"]:
                raise StyleError(f"{k}（{p['title']}）超出范围 {p.get('minimum')}–{p.get('maximum')}：{v}")
        out[k] = v
    return out


def load_config(path: Path | None = None) -> dict:
    return yaml.safe_load(Path(path or CONFIG_PATH).read_text(encoding="utf-8")) or {}


def get_style(name: str | None = None, cfg: dict | None = None) -> tuple[str, dict]:
    cfg = cfg if cfg is not None else load_config()
    styles = cfg.get("styles") or {}
    name = name or cfg.get("active") or "default"
    if name not in styles:
        raise StyleError(f"没有样式 {name}，可选：{', '.join(styles) or '（空）'}")
    try:
        return name, validate_style(styles[name])
    except StyleError as e:
        raise StyleError(f"样式 {name}：{e}") from None


def lore_keywords(streamer_cfg: dict | None) -> list[str]:
    if not streamer_cfg:
        return []
    lore = streamer_cfg.get("lore") or {}
    words = [str(w) for w in (lore.get("nicknames") or []) + (lore.get("world") or [])]
    words += [str(lore[k]) for k in ("fan_name", "audience_call") if lore.get(k)]
    words += [str(w) for w in streamer_cfg.get("lore_words") or []]
    words += [str(w) for w in lore.get("recurring_topics") or []]
    # 纯数字 / 单字容易误伤（例如 “72” 出现在数字里），只留 2 个字以上、不是纯数字的词
    return sorted({w for w in words if len(w) >= 2 and not w.isdigit()}, key=len, reverse=True)


# ---------------------------------------------------------------- 分条
def apply_corrections(text: str, corrections: dict | None) -> str:
    for a, b in (corrections or {}).items():
        text = text.replace(str(a), str(b))
    return text


def _split_text(text: str, max_chars: int) -> list[str]:
    parts, cur = [], ""
    for ch in text:
        if not cur and parts and ch in BREAK_PUNCT:   # 标点不放到下一条开头
            parts[-1] += ch
            continue
        cur += ch
        if ch in BREAK_PUNCT and len(cur) >= 4 or len(cur) >= max_chars:
            parts.append(cur)
            cur = ""
    if cur:
        if parts and len(cur) <= 2:
            parts[-1] += cur
        else:
            parts.append(cur)
    return parts


def chunk_sentence(sent: dict, max_chars: int) -> list[dict]:
    """一句话拆成若干条（每条 ≤ max_chars 字），优先在标点处断开；有词级时间戳就按词对齐。"""
    words = [w for w in sent.get("words") or [] if w[2]]
    if not words:
        pieces = _split_text(sent["text"], max_chars)
        total = sum(len(p) for p in pieces) or 1
        t, out = sent["t0"], []
        for p in pieces:
            d = (sent["t1"] - sent["t0"]) * len(p) / total
            out.append({"t0": t, "t1": t + d, "words": [[t, t + d, p]]})
            t += d
        return out
    # 长句平均拆：一句 30 字、上限 16 → 拆两条各约 15 字，而不是 16 + 14 在词中间断开
    total = len("".join(w[2] for w in words))
    target = total / max(1, math.ceil(total / max_chars))
    chunks, cur = [], []
    for w in words:
        if all(ch in BREAK_PUNCT for ch in w[2]):     # 单独的标点跟着前一个词，不放到下一条开头
            (cur or (chunks[-1] if chunks else cur)).append(w)
            continue
        n = len("".join(x[2] for x in cur))
        if cur and (n + len(w[2]) > max_chars or (n >= target and n + len(w[2]) > target * 1.25)):
            chunks.append(cur)
            cur = []
        cur.append(w)
        n = len("".join(x[2] for x in cur))
        if w[2][-1] in BREAK_PUNCT and n >= max(4, target * 0.5):
            chunks.append(cur)
            cur = []
    if cur:
        if chunks and len("".join(x[2] for x in cur)) <= 2 and \
                len("".join(x[2] for x in chunks[-1] + cur)) <= max_chars + 2:
            chunks[-1] += cur
        else:
            chunks.append(cur)
    return [{"t0": c[0][0], "t1": c[-1][1], "words": c} for c in chunks]


def build_events(sentences: list[dict], start: float, end: float, style: dict,
                 corrections: dict | None = None) -> list[dict]:
    """片段 [start, end]（整场秒数）内的字幕条，时间换算成片段内秒数。"""
    events = []
    for s in sentences:
        if s["t1"] <= start or s["t0"] >= end:
            continue
        for c in chunk_sentence(s, style["max_chars"]):
            if c["t1"] <= start or c["t0"] >= end:
                continue
            ws = [[max(0.0, a - start), max(0.0, b - start), t] for a, b, t in c["words"]]
            events.append({"t0": max(0.0, c["t0"] - start), "t1": min(end, c["t1"]) - start, "words": ws})
    events.sort(key=lambda e: e["t0"])
    # 时长下限 + 相邻条之间的小空隙补齐（减少闪烁），且不与下一条重叠
    for i, e in enumerate(events):
        nxt = events[i + 1]["t0"] if i + 1 < len(events) else end - start
        if nxt - e["t1"] < 0.3:
            e["t1"] = nxt
        e["t1"] = min(max(e["t1"], e["t0"] + style["min_duration"]), max(nxt, e["t0"] + 0.2))
    for e in events:
        text = "".join(w[2] for w in e["words"])
        text = apply_corrections(text, corrections)
        if style["strip_punct"]:
            text = text.strip().rstrip(STRIP_PUNCT).replace("，", " ").replace(",", " ").replace("。", " ")
        e["text"] = re.sub(r"\s+", " ", text).strip()
    return [e for e in events if e["text"]]


# ---------------------------------------------------------------- ASS / SRT
def ass_color(hex_color: str) -> str:
    """#RRGGBB[AA]（AA 是不透明度）→ &HAABBGGRR（AA 是透明度）。"""
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    alpha = 255 - int(h[6:8], 16) if len(h) == 8 else 0
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "｛").replace("}", "｝").replace("\n", " ")


def _highlight(text: str, words: list[str], color: str, base: str) -> str:
    out = _esc(text)
    if not words:
        return out
    pat = re.compile("|".join(re.escape(_esc(w)) for w in words), re.I)
    # 只换颜色再换回来（不用 \r 重置，否则底框样式会被切成几段）
    return pat.sub(lambda m: f"{{\\1c{color}}}{m.group(0)}{{\\1c{base}}}", out)


def _karaoke(event: dict, corrections: dict | None, strip: bool) -> str:
    parts, t = [], event["t0"]
    for a, b, w in event["words"]:
        w = apply_corrections(w, corrections)
        if strip:
            w = w.rstrip(STRIP_PUNCT)
        if not w:
            continue
        gap = max(0.0, a - t)
        if gap > 0.05:
            parts.append(f"{{\\k{int(gap * 100)}}}")
        parts.append(f"{{\\kf{max(1, int((b - max(a, t)) * 100))}}}{_esc(w)}")
        t = max(t, b)
    return "".join(parts)


def to_ass(events: list[dict], style: dict, width: int = 1920, height: int = 1080,
           keywords: list[str] | None = None, corrections: dict | None = None) -> str:
    play_x = round(PLAY_RES_Y * width / height) if height else 1920
    align = {"bottom": 2, "middle": 5, "top": 8}[style["position"]]
    primary, secondary = style["color"], style["color"]
    if style["karaoke"]:
        primary, secondary = style["karaoke_color"], style["color"]   # \kf：未读显示 Secondary，读过变 Primary
    def style_line(name, border_style, outline_c, outline, shadow, prim):
        f = [name, style["font"], style["font_size"], ass_color(prim), ass_color(secondary),
             ass_color(outline_c), ass_color(style["shadow_color"]), -1 if style["bold"] else 0, 0, 0, 0,
             100, 100, style["spacing"], 0, border_style, outline, shadow, align,
             style["margin_h"], style["margin_h"], style["margin_v"], 1]
        return "Style: " + ",".join(str(x) for x in f)

    if style["box"]:
        # 底框单独画一层（文字透明），文字另一层画在上面：关键词换色时底框不会被切成几段
        styles = [style_line("Default", 1, style["box_color"], 0, 0, primary),
                  style_line("Box", 3, style["box_color"], style["box_padding"], style["shadow"], "#FFFFFF00")]
    else:
        styles = [style_line("Default", 1, style["outline_color"], style["outline"], style["shadow"], primary)]
    head = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {play_x}", f"PlayResY: {PLAY_RES_Y}",
        "ScaledBorderAndShadow: yes", "WrapStyle: 0", "YCbCr Matrix: TV.709", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        *styles, "",
        "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    kw = []
    if style["highlight"]:
        kw = sorted({*(keywords or []), *style["highlight_words"]}, key=len, reverse=True)
    hl = ass_color(style["highlight_color"])
    lines = []
    for e in events:
        text = _karaoke(e, corrections, style["strip_punct"]) if style["karaoke"] else _highlight(e["text"], kw, hl, ass_color(primary))
        t0, t1 = ass_time(e["t0"]), ass_time(e["t1"])
        if style["box"]:
            lines.append(f"Dialogue: 0,{t0},{t1},Box,,0,0,0,,{_esc(e['text'])}")
        lines.append(f"Dialogue: 1,{t0},{t1},Default,,0,0,0,,{text}")
    return "\n".join(head + lines) + "\n"


def to_srt(events: list[dict]) -> str:
    return "".join(f"{i}\n{srt_time(e['t0'])} --> {srt_time(e['t1'])}\n{e['text']}\n\n"
                   for i, e in enumerate(events, 1))


# ---------------------------------------------------------------- 字体 / 烧录
def font_available(family: str) -> bool:
    """字体是否可用：系统字体（fc-list）或仓库 fonts/ 目录里的字体文件。"""
    fams = []
    if shutil.which("fc-list"):
        r = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True)
        fams += r.stdout.splitlines()
    if FONTS_DIR.exists() and shutil.which("fc-scan"):
        for f in FONTS_DIR.iterdir():
            if f.suffix.lower() in (".ttf", ".otf", ".ttc"):
                r = subprocess.run(["fc-scan", "--format", "%{family}\n", str(f)], capture_output=True, text=True)
                fams += r.stdout.splitlines()
    names = {n.strip().lower() for line in fams for n in line.split(",")}
    return family.lower() in names


def _filter_path(p: Path) -> str:
    s = p.as_posix()
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def ass_filter(ass_path: Path) -> str:
    f = f"ass=filename={_filter_path(ass_path)}"
    if FONTS_DIR.exists():
        f += f":fontsdir={_filter_path(FONTS_DIR)}"
    return f
