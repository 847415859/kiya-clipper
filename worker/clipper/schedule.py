"""Confirmed weekly plans and publish-time snapshots, shared by description and video outro."""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))
WEEKDAYS = "一二三四五六日"
MAX_ENTRIES = 14
CATEGORIES = ("chat", "game", "watch", "submission", "sing", "fitness", "radio", "collab", "special", "other")
REPO = Path(__file__).resolve().parents[2]


def parse_at(value=None) -> datetime:
    if value is None or value == "":
        return datetime.now(TZ)
    if isinstance(value, bool):
        raise ValueError("公开时间格式不对")
    if isinstance(value, (int, float)) and math.isfinite(value):
        return datetime.fromtimestamp(value, TZ)
    if not isinstance(value, str):
        raise ValueError("公开时间格式不对")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)


def parse_day(value) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("日期须为 YYYY-MM-DD")
    return date.fromisoformat(value)


def week_start(value=None) -> date:
    day = parse_day(value) if value else datetime.now(TZ).date()
    return day - timedelta(days=day.weekday())


def validate_plan(raw: dict, week: str) -> dict:
    start = parse_day(week)
    if start.weekday() != 0:
        raise ValueError("周起始日须为周一")
    if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list) or len(raw["entries"]) > MAX_ENTRIES:
        raise ValueError(f"每周最多 {MAX_ENTRIES} 条日程")
    image = raw.get("image_id") or ""
    if not isinstance(image, str) or (image and not re.fullmatch(r"[a-f0-9]{32}", image)):
        raise ValueError("周表图片标识不合法")
    portrait = raw.get("portrait_id") or ""
    if not isinstance(portrait, str) or (portrait and not re.fullmatch(r"[a-f0-9]{32}", portrait)):
        raise ValueError("片尾人物图片标识不合法")
    rows, ids = [], set()
    for i, row in enumerate(raw["entries"]):
        if not isinstance(row, dict):
            raise ValueError("日程格式不对")
        day = parse_day(row.get("date"))
        if not start <= day < start + timedelta(days=7):
            raise ValueError("日程日期须在所选周内")
        status = row.get("status", "planned")
        category = row.get("category", "other")
        if category not in CATEGORIES:
            raise ValueError("日程分类不合法")
        if status not in ("planned", "tbd", "rest", "cancelled"):
            raise ValueError("日程状态不合法")
        title, note = row.get("title", ""), row.get("note", "")
        if not isinstance(title, str) or len(title) > 60 or not isinstance(note, str) or len(note) > 300:
            raise ValueError("标题最多 60 字，备注最多 300 字")
        title = re.sub(r"[\r\n]+", " ", title).strip()
        if any(ord(c) < 32 and c not in "\r\n\t" for c in title + note):
            raise ValueError("日程内容不能包含控制字符")
        if not title and status == "rest":
            title = "休息"
        if not title:
            raise ValueError("请填写日程内容")
        a, b = row.get("start_time") or "", row.get("end_time") or ""
        for value in (a, b):
            if value and (not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value)):
                raise ValueError("时间须为 24 小时制 HH:mm")
        if b and not a:
            raise ValueError("填写结束时间前须填写开始时间")
        if a and b and a == b:
            raise ValueError("开始与结束时间不能相同")
        if status == "rest":
            a = b = ""
        elif not a and status == "planned":
            status = "tbd"
        ident = row.get("id") or str(i + 1)
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", ident) or ident in ids:
            raise ValueError("日程标识重复或不合法")
        ids.add(ident)
        rows.append({"id": ident, "date": day.isoformat(), "start_time": a, "end_time": b,
                     "title": title, "note": note.strip(), "status": status, "category": category})
    rows.sort(key=lambda r: (r["date"], r["start_time"] or "99:99", r["id"]))
    return {"week_start": start.isoformat(), "timezone": "Asia/Shanghai", "image_id": image, "portrait_id": portrait, "entries": rows}


def read_state(root: Path, week: str) -> dict:
    from . import schedule_db
    if schedule_db.enabled():
        return schedule_db.import_week(root,week)[0]
    try:
        value = json.loads((root / "_schedule" / (week + ".json")).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("日程文件格式错误")
        return value
    except FileNotFoundError:
        return {}


def calendar_history(root: Path) -> dict:
    from . import schedule_db
    weeks, warnings = [], []
    files = sorted((root / "_schedule").glob("????-??-??.json"), reverse=True)
    if schedule_db.enabled():
        warnings.extend(schedule_db.migrate(root))
        files=[root/'_schedule'/f'{key}.json' for key,_ in schedule_db.list_records(root,'week')]
    for path in files[:520]:
        try:
            state = read_state(root, path.stem)
            plans = {key: validate_plan(state[key], path.stem) if state.get(key) else None for key in ("published", "draft")}
            weeks.append({"week_start": path.stem, "active": bool(state.get("active")), **plans})
        except (ValueError, TypeError, KeyError, OSError) as e:
            warnings.append(f"{path.stem} 读取失败：{e}")
    return {"weeks": weeks, "warnings": warnings, "truncated": len(files) > 520}


def snapshot(root: Path, at=None, enabled=True, streamer_name="主播", live_url="") -> dict:
    now = parse_at(at)
    week = week_start(now.date().isoformat()).isoformat()
    empty = {"enabled": False, "week_start": week, "timezone": "Asia/Shanghai", "as_of": now.isoformat(),
             "entries": [], "description_block": "", "duration": 0, "fingerprint": "none", "reason": "未启用日程介绍"}
    if enabled is False:
        return empty
    state = read_state(root, week)
    published = state.get("published")
    if not state.get("active") or not isinstance(published, dict):
        return dict(empty, reason="这一周还没有确认用于发布的日程")
    plan = validate_plan(published, week)
    rows = []
    for row in plan["entries"]:
        day = parse_day(row["date"])
        if row["status"] == "cancelled" or day > now.date() + timedelta(days=6):
            continue
        if row["start_time"]:
            a = datetime.combine(day, time.fromisoformat(row["start_time"]), TZ)
            if row["end_time"]:
                b = datetime.combine(day, time.fromisoformat(row["end_time"]), TZ)
                if b < a:
                    b += timedelta(days=1)
                if b <= now:
                    continue
            elif a <= now:
                continue  # With no end time, do not advertise a possibly finished stream.
        elif day < now.date():
            continue
        date_label = f"{day.month}月{day.day}日 周{WEEKDAYS[day.weekday()]}"
        time_label = "休息" if row["status"] == "rest" else row["start_time"] or "时间待定"
        if row["end_time"]:
            time_label += "–" + row["end_time"] + ("（次日）" if row["end_time"] < row["start_time"] else "")
        if row["status"] == "tbd" and row["start_time"]:
            time_label += "（待定）"
        rows.append({**row, "date_label": date_label, "time_label": time_label})
    if not rows:
        return dict(empty, reason="本周没有剩余的已确认安排")
    heading = f"{streamer_name or '主播'}本周后续日程（北京时间）"
    block = "【" + heading + "】\n" + "\n".join(f"{r['date_label']} {r['time_label']}｜{r['title']}" for r in rows)
    block += "\n安排如有调整，以主播最新公告为准。"
    content = {"style": "lavender-journal-v1", "heading": heading, "portrait_id": plan["portrait_id"],
               "entries": [{k:v for k,v in r.items() if k not in ("id", "note", "category")} for r in rows]}
    fingerprint = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {**empty, "enabled": True, "reason": "", "entries": rows, "description_block": block,
            "heading": heading, "live_url": live_url, "fingerprint": fingerprint,
            "streamer_name": streamer_name or "主播", "portrait_id": plan["portrait_id"], "style": "lavender-journal-v1",
            "version": published.get("version", ""), "duration": 7 * ((min(len(rows), 8) + 1) // 2),
            "more_count": max(0, len(rows) - 8)}


def append_description(base: str, schedule: dict) -> str:
    block = schedule.get("description_block") or ""
    value = base.rstrip() + ("\n\n" + block if block else "")
    if len(value) > 2000:
        raise ValueError("简介加上日程后超过 2000 字，请缩短简介或关闭本作品的日程介绍")
    return value


def for_pack(root: Path, pack: dict, cfg: dict, at=None) -> dict:
    who = cfg.get("streamer") or {}
    result = snapshot(root, at if at is not None else pack.get("schedule_publish_at"),
                      pack.get("include_schedule", True), who.get("name", "主播"), who.get("live_url", ""))
    if not result["enabled"]:
        return result
    settings = cfg.get("schedule") or {}
    image, crop = None, "center"
    if result["portrait_id"]:
        image = root / "_schedule/images" / (result["portrait_id"] + ".jpg")
    elif settings.get("portrait_file"):
        image = REPO / settings["portrait_file"]
        crop = settings.get("portrait_crop") or "center"
    if image is not None:
        if not image.is_file() or image.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            raise ValueError("片尾人物图不存在或格式不支持，请重新上传或检查主播配置")
        if crop not in ("center", "weekly-left"):
            raise ValueError("片尾人物图取景设置不合法")
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        result["fingerprint"] = hashlib.sha256((result["fingerprint"] + digest + crop).encode()).hexdigest()
        result.update(_portrait_file=str(image.resolve()), portrait_crop=crop)
    return result


def check_rendered_schedule(video: Path, current: dict) -> None:
    file = video.with_suffix(".schedule.json")
    try:
        old = json.loads(file.read_text(encoding="utf-8")) if file.exists() else {}
    except (OSError, ValueError) as e:
        raise ValueError("成片日程记录无法读取，请重新渲染后发布") from e
    if not isinstance(old, dict):
        raise ValueError("成片日程记录格式错误，请重新渲染后发布")
    if "video_size" in old and (old["video_size"] != video.stat().st_size or old.get("video_mtime_ns") != video.stat().st_mtime_ns):
        raise ValueError("成片文件与日程记录不匹配，请重新渲染后发布")
    if old.get("fingerprint", "none") != current["fingerprint"]:
        raise ValueError("成片片尾与预计公开时的剩余日程不一致，请调整预计公开时间并重新渲染，或关闭日程介绍后重新渲染")
