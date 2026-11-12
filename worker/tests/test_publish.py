"""发布包：标签 / 简介 / 断行、封面图、publish_pack.py 端到端（含录像被移走后的画面兜底、后台改过的不覆盖）。"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper.publish import H, W, build_description, build_tags, clean_title, render_cover, wrap  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("fc-match"), reason="需要 ffmpeg 和 fontconfig")


def test_text_helpers():
    assert clean_title("《怪不得叫金油！》") == "怪不得叫金油！" and len(clean_title("啊" * 100)) == 80
    tags = build_tags(["虚拟主播", "直播切片", "虚拟主播"], "娜由纪雅kiya", "破防", ["金油", "做局", "律师函"],
                      "怪不得叫金油 这下做局了")
    assert tags[:4] == ["娜由纪雅kiya", "虚拟主播", "直播切片", "破防"] and set(tags[4:]) == {"金油", "做局"}
    assert len(build_tags([f"标签{i}" for i in range(20)], "x", "", [], "")) == 10
    d = build_description(None, what_happened="手滑", streamer="kiya", space_url="https://s", live_url="https://l",
                          date="2026-11-09", time="01:25:25")
    assert d.startswith("手滑") and "非官方切片" in d and "{" not in d
    assert build_description("{what_happened}\n\n\n\n{unknown}结尾", what_happened="") == "结尾"
    assert wrap("怪不得叫金油！手滑到连鼠标都D不住了", 12) == ["怪不得叫金油！", "手滑到连鼠标都D不住了"]
    assert wrap("短标题", 9) == ["短标题"]


def test_render_cover(tmp_path):
    frame = tmp_path / "f.jpg"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720", "-frames:v", "1",
                    str(frame)], check=True)
    out = tmp_path / "c.jpg"
    render_cover(frame, out, "怪不得叫金油！手滑到连鼠标都D不住了", "Noto Sans CJK SC:bold")
    from PIL import Image
    im = Image.open(out)
    assert im.size == (W, H)
    px = im.convert("RGB").load()
    yellow = sum(1 for x in range(160, W - 160, 4) for y in range(H // 2, H, 4)
                 if px[x, y][0] > 200 and px[x, y][1] > 190 and px[x, y][2] < 120)
    assert yellow > 500                                   # 下半部分有黄色大字
    edge = sum(1 for x in list(range(0, 150, 4)) + list(range(W - 150, W, 4)) for y in range(H // 2, H, 4)
               if px[x, y][0] > 200 and px[x, y][1] > 190 and px[x, y][2] < 120)
    assert edge == 0                                      # 字在 4:3 安全区里，两边没有字


def test_publish_pack(tmp_path):
    video = tmp_path / "seg.flv"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=10:d=120",
                    "-f", "lavfi", "-i", "sine=d=120", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", str(video)], check=True)
    sess = tmp_path / "output" / "20261109-2014"
    sess.mkdir(parents=True)
    (sess / "candidates.json").write_text(json.dumps({"segments": [{"video": str(video), "offset": 0}],
        "candidates": [{"rank": 1, "t": 60, "clip_start": 30, "clip_end": 90}]}), encoding="utf-8")
    (sess / "rerank.json").write_text(json.dumps({"candidates": [{"rank": 1, "llm": {
        "type": "破防", "what_happened": "抹精油手滑", "punch_time": 29, "title_candidates": ["《怪不得叫金油！》", "做局失败"]}}]},
        ensure_ascii=False), encoding="utf-8")
    (sess / "transcript.json").write_text(json.dumps({"sentences": [{"t0": 55, "t1": 58, "text": "怪不得叫金油"}]},
                                                     ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ, KIYA_MEMORY_DECISIONS=str(tmp_path / "none.json"))
    # 简介模板是用户会改的配置：测试里去掉它，用代码里的默认模板，免得用户改了模板测试就红
    import yaml
    cfg = yaml.safe_load((ROOT.parent / "config" / "kiya.yaml").read_text(encoding="utf-8"))
    cfg["publish"].pop("desc_template", None)
    cfg_path = tmp_path / "kiya.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")

    def run(*a):
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "publish_pack.py"), str(sess / "candidates.json"),
                               "--rank", "1", "--config", str(cfg_path), *map(str, a)], capture_output=True, text=True, env=env)

    r = run()
    assert r.returncode == 0, r.stdout + r.stderr
    pack = json.loads(r.stdout.strip().splitlines()[-1])
    assert pack["title"] == "怪不得叫金油！" and pack["title_candidates"] == ["怪不得叫金油！", "做局失败"]
    assert pack["description"].startswith("抹精油手滑") and "2026-11-09" in pack["description"]
    assert "破防" in pack["tags"] and pack["copyright"] == "转载" and pack["source"].startswith("https://live")
    assert pack["cover_t"] == 59 and pack["cover_source"] == "录像" and (sess / "publish" / "01_cover.jpg").exists()

    # 后台改过标题：重跑不覆盖；换封面时间和文字
    pack["title"] = "我自己改的标题"
    (sess / "publish" / "01.json").write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    r = run("--cover-t", 70, "--cover-text", "金油！")
    p2 = json.loads(r.stdout.strip().splitlines()[-1])
    assert p2["title"] == "我自己改的标题" and p2["cover_t"] == 70 and p2["cover_text"] == "金油！"
    r = run("--refresh", "--no-cover")
    assert json.loads(r.stdout.strip().splitlines()[-1])["title"] == "怪不得叫金油！"

    # 录像被移走：改用审核片段截图；封面时间超出成片范围时夹回范围内
    (sess / "clips").mkdir()
    shutil.copy(video, sess / "clips" / "01_00-01-00.mp4")
    video.unlink()
    r = run("--cover-t", 500)
    assert r.returncode == 0, r.stderr
    p3 = json.loads(r.stdout.strip().splitlines()[-1])
    assert p3["cover_source"] == "审核片段" and p3["cover_t"] == pytest.approx(89.9)
    shutil.rmtree(sess / "clips")
    r = run("--cover-t", 60)
    assert r.returncode == 1 and "找不到可以截图的视频" in r.stderr


def test_cover_prefers_rerank_cover_time(tmp_path):
    """精排 v4 选了封面帧（cover_time）就用它，不再用笑点时刻。"""
    video = tmp_path / "seg.flv"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=10:d=120",
                    "-f", "lavfi", "-i", "sine=d=120", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", str(video)], check=True)
    sess = tmp_path / "output" / "20261109-2014"
    sess.mkdir(parents=True)
    (sess / "candidates.json").write_text(json.dumps({"segments": [{"video": str(video), "offset": 0}],
        "candidates": [{"rank": 1, "t": 60, "clip_start": 30, "clip_end": 90}]}), encoding="utf-8")
    (sess / "rerank.json").write_text(json.dumps({"candidates": [{"rank": 1, "llm": {
        "type": "破防", "what_happened": "抹精油手滑", "punch_time": 29, "cover_time": 12, "title_candidates": ["做局失败"]}}]},
        ensure_ascii=False), encoding="utf-8")
    (sess / "transcript.json").write_text(json.dumps({"sentences": []}), encoding="utf-8")
    env = dict(os.environ, KIYA_MEMORY_DECISIONS=str(tmp_path / "none.json"))
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "publish_pack.py"), str(sess / "candidates.json"), "--rank", "1"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout.strip().splitlines()[-1])["cover_t"] == 42        # 30 + 12，而不是笑点的 59
