"""解析录播姬（BililiveRecorder）输出的 XML 弹幕文件。

录播姬每个分段一个 .flv 和一个同名 .xml。XML 里：
  <BililiveRecorderRecordInfo ... start_time="2026-11-09T20:00:00.0000000+08:00"/>
  <d p="秒数,模式,字号,颜色,毫秒时间戳,弹幕池,用户哈希,弹幕ID" user="昵称">内容</d>
  <gift ts="秒数" user=".." uid=".." giftname=".." giftcount="1"/>
  <sc ts="秒数" user=".." uid=".." price="30" time="60">内容</sc>
  <guard ts="秒数" user=".." uid=".." level="3" count="1"/>
时间都是相对于该分段开始的秒数。

录制中断时 XML 可能没有闭合标签，这里先尝试标准解析，失败再用正则兜底。
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# 上舰金额（元/月），用于估算礼物价值；1=总督 2=提督 3=舰长
GUARD_PRICE = {1: 19998.0, 2: 1998.0, 3: 198.0}


@dataclass
class Event:
    t: float                 # 相对于整场开始的秒数
    kind: str                # danmaku / gift / sc / guard
    text: str = ""
    value: float = 0.0       # 礼物个数、SC 金额、上舰金额
    file_index: int = 0      # 来自第几个分段
    local_t: float = 0.0     # 在该分段内的秒数（用于剪切）


@dataclass
class Segment:
    xml_path: Path
    start_time: datetime | None
    offset: float = 0.0      # 该分段相对整场开始的秒数
    last_t: float = 0.0      # 该分段最后一个事件的时间（无视频时长时用来估算）
    events: list[Event] = field(default_factory=list)

    @property
    def video_path(self) -> Path:
        return self.xml_path.with_suffix(".flv")


_RE_INFO = re.compile(r"<BililiveRecorderRecordInfo[^>]*start_time=\"([^\"]+)\"")
_RE_D = re.compile(r"<d\s+p=\"([^\"]*)\"[^>]*>(.*?)</d>", re.S)
_RE_TAG = re.compile(r"<(gift|sc|guard)\s([^>]*?)(?:/>|>(.*?)</\1>)", re.S)
_RE_ATTR = re.compile(r"(\w+)=\"([^\"]*)\"")


def _parse_time(s: str | None) -> datetime | None:
    if not s:
        return None
    # 录播姬时间格式如 2026-11-09T20:00:00.1234567+08:00，小数位超过 6 位要截断
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?([+-]\d\d:\d\d|Z)?", s.strip())
    if not m:
        return None
    base, frac, tz = m.groups()
    frac = (frac or ".0")[:7]
    tz = "+00:00" if tz in (None, "Z") else tz
    try:
        return datetime.fromisoformat(f"{base}{frac}{tz}")
    except ValueError:
        return None


def _to_float(s: str | None, default: float = 0.0) -> float:
    try:
        return float(s) if s not in (None, "") else default
    except ValueError:
        return default


def _event_from_attrs(tag: str, attrs: dict, text: str) -> Event | None:
    if tag == "d":
        p = attrs.get("p", "").split(",")
        if not p or p[0] == "":
            return None
        return Event(t=_to_float(p[0]), kind="danmaku", text=text.strip())
    ts = _to_float(attrs.get("ts"))
    if tag == "gift":
        return Event(t=ts, kind="gift", text=attrs.get("giftname", ""),
                     value=_to_float(attrs.get("giftcount"), 1.0))
    if tag == "sc":
        return Event(t=ts, kind="sc", text=text.strip(), value=_to_float(attrs.get("price")))
    if tag == "guard":
        level = int(_to_float(attrs.get("level"), 3))
        count = _to_float(attrs.get("count"), 1.0)
        return Event(t=ts, kind="guard", text=f"guard{level}",
                     value=GUARD_PRICE.get(level, 198.0) * count)
    return None


def parse_xml(path: str | Path) -> Segment:
    path = Path(path)
    raw = path.read_text(encoding="utf-8", errors="replace")
    events: list[Event] = []
    start_time = None
    try:
        root = ET.fromstring(raw)
        info = root.find("BililiveRecorderRecordInfo")
        if info is not None:
            start_time = _parse_time(info.get("start_time"))
        for el in root:
            if el.tag in ("d", "gift", "sc", "guard"):
                ev = _event_from_attrs(el.tag, el.attrib, el.text or "")
                if ev:
                    events.append(ev)
    except ET.ParseError:
        # 兜底：录制中断导致 XML 不完整
        m = _RE_INFO.search(raw)
        start_time = _parse_time(m.group(1)) if m else None
        for p, body in _RE_D.findall(raw):
            ev = _event_from_attrs("d", {"p": p}, html.unescape(body))
            if ev:
                events.append(ev)
        for tag, attr_str, body in _RE_TAG.findall(raw):
            attrs = {k: html.unescape(v) for k, v in _RE_ATTR.findall(attr_str)}
            ev = _event_from_attrs(tag, attrs, html.unescape(body or ""))
            if ev:
                events.append(ev)
    events.sort(key=lambda e: e.t)
    last_t = events[-1].t if events else 0.0
    return Segment(xml_path=path, start_time=start_time, last_t=last_t, events=events)


def load_session(xml_paths: list[str | Path], durations: dict[str, float] | None = None) -> list[Segment]:
    """把同一场直播的多个分段按开始时间拼成一条时间轴。

    有 start_time 时按真实墙钟时间计算偏移（断流重连也对得上）；
    没有时按顺序拼接，用视频时长（durations，可选）或最后一条事件时间估算。
    """
    segs = [parse_xml(p) for p in xml_paths]
    if all(s.start_time for s in segs):
        segs.sort(key=lambda s: s.start_time)
        t0 = segs[0].start_time
        for s in segs:
            s.offset = (s.start_time - t0).total_seconds()
    else:
        segs.sort(key=lambda s: s.xml_path.name)
        acc = 0.0
        for s in segs:
            s.offset = acc
            dur = (durations or {}).get(str(s.xml_path)) or s.last_t
            acc += dur
    for i, s in enumerate(segs):
        for e in s.events:
            e.local_t = e.t
            e.t = e.t + s.offset
            e.file_index = i
    return segs


def all_events(segs: list[Segment]) -> list[Event]:
    out = [e for s in segs for e in s.events]
    out.sort(key=lambda e: e.t)
    return out
