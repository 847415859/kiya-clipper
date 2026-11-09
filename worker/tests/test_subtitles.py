"""字幕测试：样式校验、分条、ASS / SRT 生成，以及 cut.py 烧录字幕的端到端检查。"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper.subtitles import (StyleError, ass_color, build_events, chunk_sentence, get_style,  # noqa: E402
                               load_config, load_schema, lore_keywords, to_ass, to_srt, validate_style)

STYLE = validate_style({})


def words(text, t0=0.0, step=0.2):
    return [[t0 + i * step, t0 + (i + 1) * step, ch] for i, ch in enumerate(text)]


def test_schema_defaults_and_validation():
    st = validate_style({"font_size": 80})
    assert st["font_size"] == 80 and st["color"] == "#FFFFFF" and st["position"] == "bottom"
    for bad, msg in [({"font_size": 500}, "超出范围"), ({"color": "red"}, "格式不对"),
                     ({"position": "left"}, "只能是"), ({"bold": "yes"}, "true / false"), ({"fontsize": 60}, "未知字段")]:
        with pytest.raises(StyleError, match=msg):
            validate_style(bad)
    # 仓库里的每套样式都合法；schema 每个字段都有标题和默认值（后台表单要用）
    cfg = load_config()
    for name in cfg["styles"]:
        get_style(name, cfg)
    assert cfg["active"] in cfg["styles"]
    for k, p in load_schema()["properties"].items():
        assert "title" in p and "default" in p, k


def test_chunking():
    s = {"t0": 0, "t1": 6, "text": "", "words": words("有没有做过什么对不起舰长的事儿啊？这都被你们发现了。")}
    chunks = chunk_sentence(s, 16)
    texts = ["".join(w[2] for w in c["words"]) for c in chunks]
    assert texts == ["有没有做过什么对不起舰长的事儿啊？", "这都被你们发现了。"]     # 问号不跑到下一条开头
    assert chunks[1]["t0"] == pytest.approx(17 * 0.2)
    long = {"t0": 0, "t1": 10, "text": "", "words": words("一" * 40)}
    assert all(len(c["words"]) <= 16 for c in chunk_sentence(long, 16))
    # 没有词级时间戳：按字数比例分配时间
    plain = chunk_sentence({"t0": 10, "t1": 14, "text": "第一句话，第二句话说得长一点。"}, 16)
    assert len(plain) == 2 and plain[0]["t0"] == 10 and plain[-1]["t1"] == pytest.approx(14)


def test_events_window_corrections_and_punct():
    sents = [{"t0": 95, "t1": 101, "text": "", "words": words("片段开始前的话，", 95, 0.75)},
             {"t0": 100.5, "t1": 103, "text": "", "words": words("要寄发件礼，", 100.5, 0.4)},
             {"t0": 103.2, "t1": 105, "text": "", "words": words("真的吗？", 103.2, 0.4)},
             {"t0": 200, "t1": 201, "text": "", "words": words("片段之后", 200)}]
    ev = build_events(sents, 100, 160, STYLE, {"发件礼": "发舰礼"})
    assert [e["text"] for e in ev] == ["片段开始前的话", "要寄发舰礼", "真的吗？"]   # 逗号句号去掉，问号保留
    assert ev[0]["t0"] == 0 and ev[1]["t0"] == pytest.approx(0.5)   # 开头前已经在说的那句也显示
    assert ev[1]["t1"] == pytest.approx(3.2)          # 与下一条空隙 < 0.3 秒时补齐，不闪烁
    assert all(a["t1"] <= b["t0"] + 1e-9 for a, b in zip(ev, ev[1:]))


def test_ass_and_srt():
    assert ass_color("#FF7EB6") == "&H00B67EFF" and ass_color("#000000A0") == "&H5F000000"
    ev = [{"t0": 0.5, "t1": 2.0, "text": "舰长寄来{律师函}", "words": words("舰长寄来律师函", 0.5)}]
    a = to_ass(ev, STYLE, 1280, 720, keywords=["舰长", "律师函"])
    assert "PlayResX: 1920" in a and "PlayResY: 1080" in a
    assert "{\\1c&H00B67EFF&}舰长{\\1c&H00FFFFFF&}".replace("&}", "}") in a.replace("&}", "}")
    assert "｛" in a                                    # 用户文本里的花括号不会被当成 ASS 指令
    box = to_ass(ev, validate_style({"box": True}), keywords=["舰长"])
    assert box.count("Dialogue:") == 2 and ",Box," in box
    k = to_ass(ev, validate_style({"karaoke": True}))
    assert "\\kf" in k
    srt = to_srt(ev)
    assert srt.startswith("1\n00:00:00,500 --> 00:00:02,000\n舰长寄来{律师函}")


def test_lore_keywords():
    kw = lore_keywords({"lore": {"nicknames": ["kiya", "72", "猫"], "fan_name": "ki由米", "audience_call": "人类酱"},
                        "lore_words": ["律师函"]})
    assert "72" not in kw and "猫" not in kw and {"kiya", "ki由米", "人类酱", "律师函"} <= set(kw)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")
def test_cut_burns_subtitles(tmp_path):
    video = tmp_path / "seg.flv"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=0x336699:s=640x360:r=10:d=40",
                    "-f", "lavfi", "-i", "sine=d=40", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    str(video)], check=True)
    sess = tmp_path / "20261112-2000"
    sess.mkdir()
    cand = {"rank": 1, "t": 20, "clip_start": 10, "clip_end": 30}
    (sess / "candidates.json").write_text(json.dumps(
        {"segments": [{"video": str(video), "offset": 0}], "candidates": [cand]}), encoding="utf-8")
    (sess / "transcript.json").write_text(json.dumps({"model": "fun-asr", "sentences": [
        {"t0": 12, "t1": 18, "text": "舰长寄来了律师函！", "words": words("舰长寄来了律师函！", 12, 0.6)}]},
        ensure_ascii=False), encoding="utf-8")

    def frame(mp4, at):
        return subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(at), "-i", str(mp4), "-frames:v", "1",
                               "-vf", "crop=iw:ih/4:0:ih*3/4,format=gray", "-f", "rawvideo", "-"],
                              capture_output=True, check=True).stdout

    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "cut.py"), str(sess / "candidates.json"),
                        "--height", "360"], capture_output=True, text=True)
    assert r.returncode == 0 and "字幕：样式" in r.stdout, r.stdout + r.stderr
    mp4 = next((sess / "clips").glob("01_*.mp4"))
    assert "舰长寄来了律师函" in mp4.with_suffix(".srt").read_text(encoding="utf-8")
    with_sub, no_sub = frame(mp4, 5), frame(mp4, 15)      # 片段第 5 秒 = 整场 15 秒，正在说话；第 15 秒没字幕
    assert len(set(with_sub)) > 3 and len(set(no_sub)) <= 3

    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "cut.py"), str(sess / "candidates.json"),
                        "--height", "360", "--no-subs"], capture_output=True, text=True)
    assert r.returncode == 0 and len(set(frame(mp4, 5))) <= 3

    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "subtitle.py"), "preview", "--style", "box",
                        "--video", str(video), "--at", "3", "--out", str(tmp_path / "pv")], capture_output=True, text=True)
    assert r.returncode == 0 and (tmp_path / "pv" / "box.png").stat().st_size > 1000, r.stdout + r.stderr
