"""封面文案候选与生成结果质量检查。"""
from __future__ import annotations

from pathlib import Path
import re


def _clean_text(value: object, limit: int = 16) -> str:
    text = re.sub(r"\s+", "", str(value or "")).strip("，。！？!?、:：；; ")
    return text[:limit]


def cover_text_options(llm: dict, title: str = "") -> list[dict]:
    """生成三种短文案方向；只改写结构，不凭空添加事件事实。"""
    source = _clean_text(llm.get("punch_line") or llm.get("what_happened") or title, 16)
    if not source:
        source = "这也太突然了"
    emotion = _clean_text(llm.get("emotion") or llm.get("reaction") or "", 8)
    options = [
        {"kind": "emotion", "label": "情绪型", "text": _clean_text(f"{emotion}{source}" if emotion else source)},
        {"kind": "suspense", "label": "悬念型", "text": _clean_text(f"然后发生了什么？{source}")},
        {"kind": "contrast", "label": "反差型", "text": _clean_text(f"本来以为{source}")},
    ]
    seen = set()
    result = []
    for item in options:
        if item["text"] and item["text"] not in seen:
            item["preferred_length"] = 8 <= len(item["text"]) <= 16
            result.append(item)
            seen.add(item["text"])
    return result


def inspect_reference_frame(path: Path) -> dict:
    """检查参考帧可用性；无法解析时给出人工复核提示，不阻止保存图片。"""
    result = {"status": "unchecked", "issues": [], "recommendation": ""}
    try:
        from PIL import Image, ImageStat
        with Image.open(path) as image:
            image = image.convert("RGB")
            width, height = image.size
            result.update(width=width, height=height)
            if width < 640 or height < 360:
                result["issues"].append("画面分辨率偏低")
            brightness = ImageStat.Stat(image).mean
            if max(brightness) < 35 or min(brightness) > 235:
                result["issues"].append("画面过暗或过曝，表情和文字区域可能不清晰")
            gray = image.convert("L").resize((min(320, width), min(180, height)))
            contrast = ImageStat.Stat(gray).stddev[0]
            if contrast < 18:
                result["issues"].append("画面对比度偏低，人物表情可能不明显")
            result["status"] = "review" if result["issues"] else "pass"
            if result["issues"]:
                result["recommendation"] = "建议回看前后 1～2 秒，选择人物无遮挡、表情更清楚的时间点"
    except (OSError, ValueError, ImportError) as exc:
        result.update(status="failed", error=str(exc), recommendation="无法自动检查，请人工确认人物无遮挡、表情和清晰度")
    return result


def inspect_cover_text(path: Path, expected: str) -> dict:
    """优先使用 pytesseract 检查封面字；依赖缺失或识别失败只标记状态。"""
    result = {"status": "unchecked", "expected": expected or "", "recognized": "", "issues": []}
    if not expected:
        result.update(status="skipped", reason="未指定封面文案")
        return result
    try:
        import pytesseract
        from PIL import Image
        with Image.open(path) as image:
            recognized = str(pytesseract.image_to_string(image, lang="chi_sim+eng") or "")
        normalized = re.sub(r"\s+", "", recognized)
        result["recognized"] = recognized.strip()
        if not normalized:
            result["issues"].append("未识别到封面文字")
        elif expected not in normalized:
            result["issues"].append("识别文字与指定文案不一致，可能漏字或错字")
        if re.search(r"(.)\1{2,}", normalized):
            result["issues"].append("疑似出现重复文字")
        result["status"] = "review" if result["issues"] else "pass"
    except ImportError:
        result.update(status="unavailable", reason="未安装 OCR 依赖，保留图片并等待人工核对")
    except (OSError, RuntimeError, ValueError) as exc:
        result.update(status="failed", reason=f"OCR 检查失败：{exc}")
    return result
