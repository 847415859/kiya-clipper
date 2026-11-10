"""阶段 1 端到端测试：合成一场带“真高光”和“干扰项”的模拟直播，跑完整流程。

模拟一场 30 分钟、3 个分段的直播：
  真高光（300、900、1500 秒）：主播声音突然变大 + 画面切换，7 秒后弹幕刷屏
  干扰 A（1200 秒）：只有弹幕刷屏（比如开播打招呼），没有声音和画面变化
  干扰 B（600 秒）：只有声音变大，没有观众反应
期望：前 3 名是三个真高光；弹幕延迟自动估计为约 7 秒；剪切、评测都能跑通。
需要 ffmpeg；整个测试约 30 秒。
"""
import csv
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_xml import make_xml  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")

SEG = 600
TRUE_EVENTS = [300, 900, 1500]
DANMAKU_ONLY = 1200
AUDIO_ONLY = 600
DELAY = 7
START = datetime(2026, 11, 10, 20, 0, 0, tzinfo=timezone(timedelta(hours=8)))


def make_segment(path: Path, seg_start: int) -> None:
    """生成一个分段视频：低音量正弦波，事件处 4 秒大音量；真高光处切换画面颜色。"""
    loud = [t for t in TRUE_EVENTS + [AUDIO_ONLY] if seg_start <= t < seg_start + SEG]
    cuts = sorted(t - seg_start for t in TRUE_EVENTS if seg_start <= t < seg_start + SEG)
    colors = ["blue", "red", "green", "yellow", "purple"]
    bounds = [0] + cuts + [SEG]
    inputs, chains = [], []
    for i in range(len(bounds) - 1):
        inputs += ["-f", "lavfi", "-i", f"color=c={colors[i % len(colors)]}:s=160x90:r=5:d={bounds[i + 1] - bounds[i]}"]
        chains.append(f"[{i}:v]")
    nv = len(bounds) - 1
    vol = "+".join(f"between(t,{t - seg_start},{t - seg_start + 4})" for t in loud) or "0"
    inputs += ["-f", "lavfi", "-i", f"sine=f=330:r=16000:d={SEG}"]
    fc = "".join(chains) + f"concat=n={nv}:v=1:a=0[v];[{nv}:a]volume='if({vol},1.0,0.02)':eval=frame[a]"
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", *inputs, "-filter_complex", fc,
           "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-g", "10",
           "-c:a", "aac", str(path)]
    subprocess.run(cmd, check=True)


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    rec = tmp_path_factory.mktemp("rec") / "1724773744-娜由纪雅kiya"
    rec.mkdir()
    for i in range(3):
        st = START + timedelta(seconds=i * SEG)
        name = f"录制-1724773744-{st.strftime('%Y%m%d-%H%M%S')}-000-测试"
        bursts = [(t + DELAY - i * SEG, "哈哈哈哈草") for t in TRUE_EVENTS if i * SEG <= t + DELAY < (i + 1) * SEG]
        bursts += [(DANMAKU_ONLY - i * SEG, "晚上好晚上好")] if i * SEG <= DANMAKU_ONLY < (i + 1) * SEG else []
        (rec / f"{name}.xml").write_text(make_xml(st, SEG, bursts, base_rate=0.5, seed=i), encoding="utf-8")
        make_segment(rec / f"{name}.flv", i * SEG)
    return rec


@pytest.fixture(scope="module")
def screened(session, tmp_path_factory):
    out = tmp_path_factory.mktemp("out")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "screen.py"), str(session.parent), "--latest",
                        "--out", str(out), "--top", "10"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return out / "20261110-2000", r.stdout


def test_multi_signal_ranking(screened):
    d, log = screened
    data = json.loads((d / "candidates.json").read_text(encoding="utf-8"))
    assert data["signals_used"] == "density+emotion+gift+audio+scene"
    assert abs(data["delay_seconds"] - DELAY) <= 2, log
    top3 = sorted(c["t"] for c in data["candidates"][:3])
    for got, want in zip(top3, TRUE_EVENTS):
        assert abs(got - want) <= 20, (top3, log)
    for c in data["candidates"][:3]:
        assert c["contrib"]["audio"] > 0
    assert (d / "curve.png").stat().st_size > 5000
    assert (d / "features.npz").exists()


def test_cache_reused_and_danmaku_only_mode(screened, session):
    d, _ = screened
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "screen.py"), str(session.parent), "--latest",
                        "--out", str(d.parent), "--top", "10"], capture_output=True, text=True)
    assert "使用缓存特征" in r.stdout
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "screen.py"), str(session.parent), "--latest",
                        "--out", str(d.parent), "--top", "10", "--danmaku-only"], capture_output=True, text=True)
    assert r.returncode == 0 and "density+emotion+gift" in r.stdout
    # 只看弹幕时，“打招呼刷屏”干扰项会混进前列——这正是加音频 / 画面信号的意义
    data = json.loads((d / "candidates.json").read_text(encoding="utf-8"))
    assert any(abs(c["t"] - DANMAKU_ONLY) <= 20 for c in data["candidates"][:4])


def test_cut_precise_and_fast(screened, session):
    d, _ = screened
    subprocess.run([sys.executable, str(ROOT / "scripts" / "screen.py"), str(session.parent), "--latest",
                    "--out", str(d.parent), "--top", "10"], check=True, capture_output=True)
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "cut.py"), str(d / "candidates.json"),
                        "--ranks", "1", "2", "--height", "90"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    clips = sorted((d / "clips").glob("*.mp4"))
    assert len(clips) == 2
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                "default=nw=1:nk=1", str(clips[0])], capture_output=True, text=True).stdout)
    assert 59.5 <= dur <= 60.5                  # 逐帧定位：不会因为关键帧多出一两秒
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "cut.py"), str(d / "candidates.json"),
                        "--ranks", "3", "--fast"], capture_output=True, text=True)
    assert r.returncode == 0 and list((d / "clips").glob("03_*.flv"))


def test_evaluate(screened, tmp_path):
    d, _ = screened
    ann = tmp_path / "annotations.csv"
    rows = ["session,start,end,type,source,quality,note"]
    for t in TRUE_EVENTS:
        rows.append(f"20261110-2000,{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d},"
                    f"{(t + 20) // 3600:02d}:{(t + 20) % 3600 // 60:02d}:{(t + 20) % 60:02d},高能,self,3,真高光")
    ann.write_text("\n".join(rows) + "\n", encoding="utf-8")
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "evaluate.py"), "--out", str(d.parent),
                        "--annotations", str(ann), "--reviews", str(reviews)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "3/3 = 100%" in r.stdout          # 召回率：3 个真高光都被覆盖


def test_min_score_no_padding(screened, session, tmp_path):
    """分数线：只留过线的候选，不硬凑；全都不过线时候选为空，剪切直接跳过。"""
    d, _ = screened
    subprocess.run([sys.executable, str(ROOT / "scripts" / "screen.py"), str(session.parent), "--latest",
                    "--out", str(d.parent), "--top", "10"], check=True, capture_output=True)
    scores = [c["score"] for c in json.loads((d / "candidates.json").read_text(encoding="utf-8"))["candidates"]]
    line = (scores[2] + scores[3]) / 2           # 前 3 个是真高光，线划在第 3、4 名之间
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "screen.py"), str(session.parent), "--latest",
                        "--out", str(d.parent), "--top", "10", "--min-score", f"{line:.4f}"], capture_output=True, text=True)
    assert r.returncode == 0 and "3 个过线" in r.stdout, r.stdout + r.stderr
    data = json.loads((d / "candidates.json").read_text(encoding="utf-8"))
    assert [c["rank"] for c in data["candidates"]] == [1, 2, 3]
    assert len(data["below_threshold"]) == 7 and all(b["score"] < line for b in data["below_threshold"])
    with open(d / "peaks.csv", encoding="utf-8-sig") as f:
        assert len(list(csv.DictReader(f))) == 3

    # 标注：前 3 个 Y，其余 N → --sweep 建议的分数线保住 3 个有趣、少看 7 个
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    rows = [["序号", "片段起", "片段止", "分数", "是否有趣(Y/N)"]]
    for i, s in enumerate(scores, 1):
        rows.append([i, "00:00:00", "00:00:10", f"{s:.2f}", "Y" if i <= 3 else "N"])
    with open(reviews / "20261110-2000.csv", "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows(rows)
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "evaluate.py"), "--sweep", "--out", str(d.parent),
                        "--annotations", str(tmp_path / "none.csv"), "--reviews", str(reviews)],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "建议分数线" in r.stdout and "保住全部 3 个有趣片段" in r.stdout, r.stdout + r.stderr
    rec = float(r.stdout.split("建议分数线：")[1].split("（")[0])
    assert scores[3] < rec + 0.5 and rec <= scores[2]

    # 线划得比最高分还高：没有候选，提示不用审核，剪切也不报错
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "screen.py"), str(session.parent), "--latest",
                        "--out", str(d.parent), "--top", "10", "--min-score", "999"], capture_output=True, text=True)
    assert r.returncode == 0 and "没有达到分数线" in r.stdout
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "cut.py"), str(d / "candidates.json")],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "没有要剪的候选" in r.stdout
