"""贴图含义说明：没有说明的贴图不给 AI 用、同一个贴图整条只能用一次、自动写说明。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper.edit import limit_stickers, validate_item  # noqa: E402
from clipper.stickers import StickerMeta, describe_missing, list_files, load_meta, offer_text, offered, undescribed  # noqa: E402


def mk(d: Path, *names):
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=red:s=64x64", "-frames:v", "1", str(d / n)], check=True)


def test_offered_only_described_and_enabled(tmp_path):
    d = tmp_path / "s"
    mk(d, "a.png", "b.png", "c.png", "d.png")
    (d / "stickers.json").write_text(json.dumps({"a.png": {"meaning": "狗头", "mood": "无语"}, "b.png": {"meaning": "  "},
                                                  "c.png": {"meaning": "哭", "disabled": True}, "gone.png": {"meaning": "文件已删"}}), encoding="utf-8")
    assert [s["name"] for s in offered(d)] == ["a.png"]
    assert undescribed(d) == ["b.png", "d.png"]                                   # c 有说明（只是被禁用）
    assert "- a.png：狗头；适合：无语" in offer_text(offered(d))
    assert "没有带含义说明" in offer_text([])
    assert offered(tmp_path / "nope") == [] and list_files(tmp_path / "nope") == []


def test_limit_stickers_once_each_and_cap():
    it = lambda t, a: validate_item({"type": "sticker", "start": t, "asset": a}, 100)
    txt = validate_item({"type": "text", "start": 5, "text": "哈"}, 100)
    items = [it(30, "a.png"), it(10, "a.png"), it(20, "b.png"), txt, it(40, "c.png")]
    kept, dropped = limit_stickers(items, 2)
    st = [i for i in kept if i["type"] == "sticker"]
    assert [(i["asset"], i["start"]) for i in sorted(st, key=lambda i: i["start"])] == [("a.png", 10), ("b.png", 20)]   # 按时间先后留：a 只留最早的一个
    assert {i["asset"] for i in dropped} == {"a.png", "c.png"} and any(i["type"] == "text" for i in kept)
    assert limit_stickers(items, 0)[0] == [txt]


class Stub:
    def __init__(self, fail=()):
        self.fail, self.calls = set(fail), []

    def chat_json(self, msgs, schema, tag=""):
        self.calls.append(tag)
        content = msgs[1]["content"]
        assert any(x["type"] == "image_url" and x["image_url"]["url"].startswith("data:image/jpeg;base64,") for x in content)
        if len(self.calls) in self.fail:
            return SimpleNamespace(data=None, error="boom")
        return SimpleNamespace(data=StickerMeta(meaning=f"表情{len(self.calls)}", mood="无语"), error=None)


def test_describe_missing(tmp_path):
    d = tmp_path / "s"
    mk(d, "a.png", "b.png", "c.png")
    (d / "stickers.json").write_text(json.dumps({"a.png": {"meaning": "我手写的"}}), encoding="utf-8")
    st = Stub(fail={2})
    logs = []
    res = describe_missing(st, d, log=logs.append)
    assert res == {"b.png": True, "c.png": False} and len(st.calls) == 2           # 手写的不动；失败的不写
    meta = load_meta(d)
    assert meta["a.png"] == {"meaning": "我手写的"} and meta["b.png"]["auto"] is True and "c.png" not in meta
    assert any("识别失败" in x for x in logs)
    res = describe_missing(Stub(), d)                                                # 补上失败的那个
    assert res == {"c.png": True} and load_meta(d)["c.png"]["auto"] is True
    assert describe_missing(Stub(), d) == {}                                         # 都有了：不再调用
    res = describe_missing(Stub(), d, force=True)                                    # force：只重写自动的，手写的不动
    assert set(res) == {"b.png", "c.png"} and load_meta(d)["a.png"] == {"meaning": "我手写的"}


def test_thumb_handles_transparent_png_and_gif(tmp_path):
    from clipper.stickers import thumb
    mk(tmp_path, "t.png")
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=blue:s=40x20,fps=5", "-t", "1", str(tmp_path / "g.gif")], check=True)
    assert thumb(tmp_path / "t.png", tmp_path / "t.jpg") and thumb(tmp_path / "g.gif", tmp_path / "g.jpg")
    assert not thumb(tmp_path / "missing.png", tmp_path / "m.jpg")


def test_describe_script_list_mode(tmp_path):
    d = tmp_path / "s"
    mk(d, "a.png", "b.png")
    (d / "stickers.json").write_text(json.dumps({"a.png": {"meaning": "狗头", "auto": True}}), encoding="utf-8")
    import os
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "describe_stickers.py"), "--list"], capture_output=True, text=True,
                       env=dict(os.environ, KIYA_STICKERS=str(d)))
    assert r.returncode == 0 and "a.png" in r.stdout and "自动" in r.stdout and "没有说明（AI 不会用）" in r.stdout, r.stdout + r.stderr
