"""生成仿录播姬格式的弹幕 XML，用于测试（不依赖真实录像）。"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from xml.sax.saxutils import escape, quoteattr

HEADER = '<?xml version="1.0" encoding="utf-8"?>\n<i>\n<chatserver>chat.bilibili.com</chatserver>\n'


def make_xml(start: datetime, duration: float, bursts: list[tuple[float, str]] | None = None,
             base_rate: float = 0.5, seed: int = 0, truncated: bool = False) -> str:
    """base_rate：平时每秒弹幕数；bursts：[(相对本分段秒数, 高峰弹幕内容)]，高峰持续约 20 秒、10 倍密度。"""
    rnd = random.Random(seed)
    normal = ["晚上好", "来了", "主播好", "好听", "在吗", "今天玩什么", "kiya", "签到"]
    lines = [HEADER,
             f'<BililiveRecorderRecordInfo roomid="1724773744" shortid="0" name="娜由纪雅kiya" '
             f'title="测试" areanameparent="虚拟主播" areanamechild="虚拟主播" '
             f'start_time="{start.isoformat()}" />\n']
    t = 0.0
    while t < duration:
        t += rnd.expovariate(base_rate)
        if t >= duration:
            break
        lines.append(f'<d p="{t:.3f},1,25,16777215,0,0,abc,0" user="u">{escape(rnd.choice(normal))}</d>\n')
    for bt, text in bursts or []:
        for _ in range(int(base_rate * 20 * 10)):
            tt = bt + rnd.uniform(0, 20)
            lines.append(f'<d p="{tt:.3f},1,25,16777215,0,0,abc,0" user="u">{escape(text)}</d>\n')
        lines.append(f'<sc ts="{bt + 5:.3f}" user="u" uid="1" price="50" time="60">{escape(text)}</sc>\n')
    lines.append('<gift ts="10.0" user="u" uid="1" giftname="小心心" giftcount="3"></gift>\n')
    lines.append('<guard ts="20.0" user="u" uid="1" level="3" count="1"></guard>\n')
    lines.append('<d p="30.0,1,25,16777215,0,0,abc,0" user=' + quoteattr('a&b') + '>&lt;符号&gt;测试</d>\n')
    if not truncated:
        lines.append("</i>\n")
    return "".join(lines)


def write_session(tmp_dir, start: datetime, seg_seconds: float, n_segments: int,
                  bursts: dict[int, list[tuple[float, str]]], gap_after: dict[int, float] | None = None):
    """写出一场多个分段（.xml + 空 .flv），返回 xml 路径列表。gap_after：第 i 段之后断流多少秒。"""
    paths = []
    st = start
    for i in range(n_segments):
        name = f"录制-1724773744-{st.strftime('%Y%m%d-%H%M%S')}-000-测试"
        xml = tmp_dir / f"{name}.xml"
        xml.write_text(make_xml(st, seg_seconds, bursts.get(i), seed=i), encoding="utf-8")
        (tmp_dir / f"{name}.flv").write_bytes(b"")
        paths.append(xml)
        st = st + timedelta(seconds=seg_seconds + (gap_after or {}).get(i, 0))
    return paths
