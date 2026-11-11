"""贴图库的含义说明。

AI 编排只能看到贴图的文件名，而文件名通常是下载来的 `1743…png` 这种，根本看不出是什么表情——
结果就是它随便挑一个，同一个贴图一直被用。所以每个贴图要有一句含义说明（assets/stickers/stickers.json），
没有说明的贴图不给 AI 用。说明可以手写，也可以用 scripts/describe_stickers.py 让多模态模型看图自动写（标 auto，之后可手改）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from pydantic import BaseModel

REPO = Path(__file__).resolve().parents[2]
META_FILE = "stickers.json"
EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


class StickerMeta(BaseModel):
    meaning: str                 # 这个贴图画的是什么、表达什么（一句话）
    mood: str = ""               # 适合的情绪 / 场景，例如“震惊、无语、吐槽”
    avoid: str = ""              # 不适合的场合（可空）


def sticker_dir() -> Path:
    return Path(os.environ.get("KIYA_STICKERS") or REPO / "assets" / "stickers")


def list_files(d: Path) -> list[str]:
    return sorted(p.name for p in d.iterdir() if p.suffix.lower() in EXTS) if d.is_dir() else []


def load_meta(d: Path) -> dict:
    try:
        data = json.loads((d / META_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_meta(d: Path, meta: dict) -> None:
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (META_FILE + ".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(d / META_FILE)


def offered(d: Path) -> list[dict]:
    """可以给 AI 用的贴图：文件在、有含义说明、没被标 disabled。"""
    meta = load_meta(d)
    out = []
    for name in list_files(d):
        m = meta.get(name)
        if isinstance(m, dict) and str(m.get("meaning") or "").strip() and not m.get("disabled"):
            out.append({"name": name, **{k: str(m.get(k) or "").strip() for k in ("meaning", "mood", "avoid")}})
    return out


def undescribed(d: Path) -> list[str]:
    meta = load_meta(d)
    return [n for n in list_files(d) if not (isinstance(meta.get(n), dict) and str(meta[n].get("meaning") or "").strip())]


def offer_text(items: list[dict]) -> str:
    if not items:
        return "（贴图库里没有带含义说明的贴图，不要用贴图）"
    lines = []
    for s in items:
        extra = (f"；适合：{s['mood']}" if s["mood"] else "") + (f"；不适合：{s['avoid']}" if s["avoid"] else "")
        lines.append(f"- {s['name']}：{s['meaning']}{extra}")
    return "\n".join(lines)


def thumb(src: Path, dst: Path, size: int = 256) -> bool:
    """贴图 → 白底 JPG 缩略图（透明部分填白；gif / webp 取第一帧），给多模态模型看。"""
    if not shutil.which("ffmpeg"):
        return False
    r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=white:s={size}x{size}",
                        "-i", str(src), "-filter_complex",
                        f"[1:v]scale={size}:{size}:force_original_aspect_ratio=decrease[fg];[0:v][fg]overlay=(W-w)/2:(H-h)/2:shortest=1,format=yuvj420p",
                        "-frames:v", "1", "-q:v", "3", str(dst)], capture_output=True)
    return r.returncode == 0 and dst.exists()


def describe_missing(client, d: Path, force: bool = False, log=print) -> dict:
    """给没有含义说明的贴图（force：连自动写的也重写，手写的不动）让多模态模型看图写一句。返回 {文件名: 是否成功}。"""
    from .llm import image_part, load_prompt, text_part
    version, system, user = load_prompt("sticker_v1")
    meta = load_meta(d)
    todo = []
    for n in list_files(d):
        m = meta.get(n)
        have = isinstance(m, dict) and str(m.get("meaning") or "").strip()
        if not have or (force and isinstance(m, dict) and m.get("auto")):
            todo.append(n)
    res = {}
    with tempfile.TemporaryDirectory() as tmp:
        for n in todo:
            t = Path(tmp) / (n + ".jpg")
            if not thumb(d / n, t):
                log(f"{n}：生成缩略图失败（需要 ffmpeg，且图片没损坏）")
                res[n] = False
                continue
            r = client.chat_json([{"role": "system", "content": system},
                                  {"role": "user", "content": [image_part(t), text_part(user)]}], StickerMeta, tag=version)
            if r.data is None:
                log(f"{n}：识别失败：{(r.error or '')[:60]}")
                res[n] = False
                continue
            meta[n] = {**r.data.model_dump(), "auto": True}
            res[n] = True
            log(f"{n}：{r.data.meaning}（适合：{r.data.mood or '—'}）")
    if any(res.values()):
        save_meta(d, meta)
    return res
