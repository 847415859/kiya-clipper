"""成片渲染的 worker 部分：剪辑脚本校验、源视频裁剪、字幕时间换算、素材目录、调用渲染器。
真正的 Remotion 渲染换成一个假渲染器（复制源视频、记下 props），不需要 Node 和 Chrome。
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper.edit import EditError, validate_edit  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")


def test_validate_edit():
    e = validate_edit({"trim": {"start": 5, "end": 50}, "items": [
        {"type": "zoom", "start": 20, "duration": 2},
        {"type": "text", "start": 10, "text": "舰礼寄了内衣？！", "preset": "slam"},
        {"type": "sticker", "start": 12, "asset": "doge.png"}]}, 60)
    assert [i["type"] for i in e["items"]] == ["text", "sticker", "zoom"]           # 按时间排序
    t = e["items"][0]
    assert t["size"] == 110 and t["color"] == "#FFE14D" and t["x"] == 0.5 and t["id"]  # 补默认值
    assert e["subtitles"] == {"enabled": True}
    for bad, msg in [({"trim": {"start": 30, "end": 30.5}}, "至少要 1 秒"),
                     ({"items": [{"type": "video", "start": 1}]}, "未知效果类型"),
                     ({"items": [{"type": "text", "start": 1, "text": ""}]}, "1–40"),
                     ({"items": [{"type": "zoom", "start": 1, "scale": 9}]}, "超出范围"),
                     ({"items": [{"type": "text", "start": 1, "text": "x", "color": "red"}]}, "颜色"),
                     ({"items": [{"type": "sticker", "start": 1, "asset": "../../etc/passwd"}]}, "文件名"),
                     ({"items": [{"type": "text", "start": 99, "text": "x"}]}, "超出范围")]:
        with pytest.raises(EditError, match=msg):
            validate_edit(bad, 60)


def test_edit_check_script(tmp_path):
    f = tmp_path / "e.json"
    f.write_text(json.dumps({"items": [{"type": "zoom", "start": 3}]}), encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "edit_check.py"), str(f), "--clip-len", "60"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and json.loads(r.stdout)["edit"]["items"][0]["scale"] == 1.5
    f.write_text("{坏的", encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "edit_check.py"), str(f), "--clip-len", "60"],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "剪辑脚本有误" in r.stderr


def test_render_pipeline(tmp_path):
    # 一段 120 秒的录像，候选片段是 30–90 秒
    video = tmp_path / "seg.flv"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=10:d=120",
                    "-f", "lavfi", "-i", "sine=d=120", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", str(video)], check=True)
    sess = tmp_path / "out" / "20261112-2000"
    (sess / "edits").mkdir(parents=True)
    (sess / "candidates.json").write_text(json.dumps({"segments": [{"video": str(video), "offset": 0}],
        "candidates": [{"rank": 3, "t": 60, "clip_start": 30, "clip_end": 90}]}), encoding="utf-8")
    words = lambda s, t0: [[t0 + i * 0.3, t0 + (i + 1) * 0.3, ch] for i, ch in enumerate(s)]
    (sess / "transcript.json").write_text(json.dumps({"model": "fun-asr", "sentences": [
        {"t0": 41, "t1": 44, "text": "", "words": words("舰长寄来发件礼，", 41)},
        {"t0": 100, "t1": 102, "text": "", "words": words("裁剪范围之外", 100)}]}, ensure_ascii=False), encoding="utf-8")
    (sess / "edits" / "03.json").write_text(json.dumps({
        "trim": {"start": 5, "end": 25},                     # 成片 = 整场 35–55 秒
        "subtitles": {"enabled": True, "style": {"font_size": 70, "karaoke": True}, "style_name": "测试"},
        "items": [{"type": "text", "start": 12, "duration": 2, "text": "发舰礼？！"},
                  {"type": "zoom", "start": 11.5, "duration": 3, "scale": 1.8},
                  {"type": "sticker", "start": 13, "duration": 1.5, "asset": "doge.png"},
                  {"type": "text", "start": 40, "duration": 2, "text": "裁掉的"}]}, ensure_ascii=False), encoding="utf-8")
    stickers = tmp_path / "stickers"
    stickers.mkdir()
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=pink:s=64x64", "-frames:v", "1",
                    str(stickers / "doge.png")], check=True)
    fake = tmp_path / "fake_render.py"
    fake.write_text(
        "import sys, shutil, json, os\n"
        "job, props, out = sys.argv[1:4]\n"
        "p = json.load(open(props, encoding='utf-8'))\n"
        "assert os.path.exists(os.path.join(job, 'source.mp4'))\n"
        "for it in p['items']:\n"
        "    if it['type'] == 'sticker': assert os.path.exists(os.path.join(job, 'stickers', it['asset']))\n"
        "shutil.copy(os.path.join(job, 'source.mp4'), out)\n"
        "shutil.copy(props, out + '.props.json')\n", encoding="utf-8")
    env = dict(__import__("os").environ, KIYA_RENDER_CMD=f"{sys.executable} {fake}", KIYA_STICKERS=str(stickers),
               KIYA_OVERRIDES=str(tmp_path / "none.json"))
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "render.py"), str(sess / "candidates.json"),
                        "--rank", "3", "--height", "360"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    out = sess / "final" / "03_00-01-00_360p.mp4"
    assert out.exists() and out.with_suffix(".edit.json").exists()
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                               capture_output=True, text=True).stdout)
    assert 19.8 <= dur <= 20.3                                # 裁剪后正好 20 秒（逐帧定位）
    props = json.loads(Path(str(out.with_suffix("")) + ".part.mp4.props.json").read_text(encoding="utf-8")) \
        if Path(str(out.with_suffix("")) + ".part.mp4.props.json").exists() else \
        json.loads((sess / "render" / "job_03" / "props.json").read_text(encoding="utf-8"))
    assert props["width"] == 640 and props["height"] == 360 and props["duration"] == 20
    assert [(i["type"], i["start"]) for i in props["items"]] == [("zoom", 6.5), ("text", 7.0), ("sticker", 8.0)]
    ev = props["subtitles"]["events"]
    assert ev and ev[0]["t0"] == pytest.approx(6.0) and "发舰礼" in ev[0]["text"]      # 41 秒 → 成片第 6 秒，已纠错
    assert all(e["t1"] <= 20 for e in ev) and not any("裁剪范围之外" in e["text"] for e in ev)
    assert ev[0]["words"][0] == [6.0, 6.3, "舰"]
    assert props["subtitles"]["style"]["font_size"] == 70 and props["subtitles"]["style"]["karaoke"] is True
    assert "舰长" in props["subtitles"]["keywords"]
    assert (sess / "render" / "job_03" / "stickers" / "doge.png").exists()

    # 第二次渲染复用剪好的源视频
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "render.py"), str(sess / "candidates.json"),
                        "--rank", "3", "--height", "360"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and "源视频已剪好" not in r.stdout


def test_cut_across_segments_is_frame_accurate(tmp_path):
    """跨两个录像分段的片段：两段各自精确定位后拼接，总时长准确。"""
    sys.path.insert(0, str(ROOT / "scripts"))
    from cut import cut_one
    a, b = tmp_path / "a.flv", tmp_path / "b.flv"
    for f, c in ((a, "red"), (b, "blue")):     # GOP 很长（250 帧），旧做法会多出十几秒
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c={c}:s=320x180:r=10:d=40",
                        "-f", "lavfi", "-i", "sine=d=40", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                        "-c:a", "aac", str(f)], check=True)
    out = tmp_path / "x.mp4"
    cut_one([(a, 27.0, 40.0), (b, 0.0, 12.0)], out, False, 180)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                               capture_output=True, text=True).stdout)
    assert 24.8 <= dur <= 25.3


def test_window_beyond_candidate(tmp_path):
    """素材范围超出候选片段（按精排修正后的起止）：预览视频、渲染都从录像重新剪，时间按素材范围开头算。"""
    from clipper.edit import suggest_window
    assert suggest_window({"clip_start": 30, "clip_end": 90}, {"start": 22, "end": 97}) == {"start": 17.0, "end": 102.0}
    assert suggest_window({"clip_start": 3, "clip_end": 63}, {"start": 1, "end": 70}) == {"start": 0.0, "end": 75.0}
    w = suggest_window({"clip_start": 100, "clip_end": 160}, {"start": 100, "end": 500})
    assert w["end"] - w["start"] == 300 and w["start"] == 100
    with pytest.raises(EditError, match="素材范围"):
        validate_edit({"window": {"start": 10, "end": 12}}, 60)
    e = validate_edit({"window": {"start": 20, "end": 100}, "trim": {"start": 5, "end": 70},
                       "items": [{"type": "zoom", "start": 75, "duration": 2}]}, 60)   # 75 > 候选长度 60 也合法
    assert e["window"] == {"start": 20.0, "end": 100.0} and e["trim"]["end"] == 70

    video = tmp_path / "seg.flv"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=10:d=120",
                    "-f", "lavfi", "-i", "sine=d=120", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", str(video)], check=True)
    sess = tmp_path / "out" / "20261112-2000"
    (sess / "edits").mkdir(parents=True)
    (sess / "candidates.json").write_text(json.dumps({"segments": [{"video": str(video), "offset": 0}],
        "candidates": [{"rank": 2, "t": 60, "clip_start": 30, "clip_end": 90}]}), encoding="utf-8")
    (sess / "transcript.json").write_text(json.dumps({"model": "fun-asr", "sentences": [
        {"t0": 22, "t1": 25, "text": "片段开始前的铺垫"}, {"t0": 95, "t1": 97, "text": "片段结束后的反应"}]},
        ensure_ascii=False), encoding="utf-8")
    (sess / "rerank.json").write_text(json.dumps({"candidates": [{"rank": 2, "refined": {"start": 22, "end": 97}}]}),
                                      encoding="utf-8")
    env = dict(__import__("os").environ, KIYA_OVERRIDES=str(tmp_path / "none.json"))
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "edit_preview.py"), str(sess / "candidates.json"), "--rank", "2",
                        "--height", "180"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    pv = sess / "edit_preview" / "02_17.0_102.0.mp4"                  # 没有剪辑脚本：按精排建议的范围
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(pv)],
                               capture_output=True, text=True).stdout)
    assert 84.7 <= dur <= 85.3
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "edit_preview.py"), str(sess / "candidates.json"), "--rank", "2",
                        "--start", "10", "--end", "100", "--height", "180"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and not pv.exists() and (sess / "edit_preview" / "02_10.0_100.0.mp4").exists()   # 只留最新

    (sess / "edits" / "02.json").write_text(json.dumps({"window": {"start": 17, "end": 102}, "trim": {"start": 4, "end": 82},
        "items": [{"type": "text", "start": 79, "duration": 2, "text": "反应"}]}, ensure_ascii=False), encoding="utf-8")
    fake = tmp_path / "fake_render.py"
    fake.write_text("import sys, shutil\njob, props, out = sys.argv[1:4]\nshutil.copy(job + '/source.mp4', out)\n", encoding="utf-8")
    env.update(KIYA_RENDER_CMD=f"{sys.executable} {fake}", KIYA_STICKERS=str(tmp_path))
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "render.py"), str(sess / "candidates.json"), "--rank", "2",
                        "--height", "360"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    props = json.loads((sess / "render" / "job_02" / "props.json").read_text(encoding="utf-8"))
    assert props["duration"] == pytest.approx(78)                     # 整场 21–99 秒，超出候选片段 30–90
    assert [(i["text"], i["start"]) for i in props["items"]] == [("反应", 75.0)]
    texts = [e["text"] for e in props["subtitles"]["events"]]
    assert any("铺垫" in t for t in texts) and any("反应" in t for t in texts)
