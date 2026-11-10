#!/usr/bin/env python3
"""kiya-clipper 监控告警：每 3 分钟由 cron 运行一次，异常时推送到手机微信。

检查项
  - 录播服务 brec 是否在运行
  - 主播开播了，但没有正在写入的录像 / 弹幕（= 漏录）
  - 硬盘使用率过高
  - OSS 同步太久没成功
通知项
  - 开播（并附录制是否正常）、下播（本场时长、录像大小、弹幕数）
  - 下播后自动跑弹幕分析，把前 5 个高峰推送过来
  - KIYA_LIVE_SCREEN=1：直播中每 30 分钟对已录完的分段粗筛一次（边播边筛），审核页提前有候选
  - KIYA_MEME_REFRESH=1（默认）：不在直播时，每天抓一次B站热门标题 / 热搜、重建梗库（失败只告警一次）

同一个问题只推送一次，持续未恢复则每 6 小时提醒一次；恢复时再推一条“已恢复”。
只用 Python 标准库，不依赖 worker 的虚拟环境（自动分析那一步除外）。

配置读取仓库根目录 .env（见 .env.example 的“监控告警”部分）。
手动测试推送：python3 kiya_monitor.py --test-notify
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
REALERT_SECONDS = 6 * 3600


# ---------------------------------------------------------------- 配置
def load_env() -> dict:
    env = {}
    p = Path(os.environ.get("KIYA_ENV_FILE", REPO / ".env"))
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
            if m:
                env[m.group(1)] = m.group(2).strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if k.startswith(("KIYA_", "REC_", "PUSH", "SERVERCHAN", "WECOM", "DINGTALK"))})
    return env


def cfg(env: dict, key: str, default: str = "") -> str:
    v = env.get(key, "")
    return v if v != "" else default


# ---------------------------------------------------------------- 推送
def _post_json(url: str, payload: dict) -> str:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode(errors="replace")[:300]


def _post_form(url: str, data: dict) -> str:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode(errors="replace")[:300]


def notify(env: dict, title: str, body: str, dry_run: bool = False) -> None:
    """推送到所有已配置的渠道；一个都没配就只打印。"""
    text = f"{title}\n{body}".strip()
    print(f"[{datetime.now():%F %T}] 通知：{title} | {body.replace(chr(10), ' / ')}")
    if dry_run:
        return
    sent = False
    try:
        if cfg(env, "PUSHPLUS_TOKEN"):
            _post_json("http://www.pushplus.plus/send",
                       {"token": env["PUSHPLUS_TOKEN"], "title": title, "content": body.replace("\n", "<br>")})
            sent = True
        if cfg(env, "SERVERCHAN_SENDKEY"):
            _post_form(f"https://sctapi.ftqq.com/{env['SERVERCHAN_SENDKEY']}.send", {"title": title, "desp": body})
            sent = True
        if cfg(env, "WECOM_WEBHOOK"):
            _post_json(env["WECOM_WEBHOOK"], {"msgtype": "text", "text": {"content": text}})
            sent = True
        if cfg(env, "DINGTALK_WEBHOOK"):
            # 钉钉机器人安全设置建议用“自定义关键词：kiya”
            _post_json(env["DINGTALK_WEBHOOK"], {"msgtype": "text", "text": {"content": "kiya " + text}})
            sent = True
    except Exception as e:  # 推送失败不能让监控本身崩掉
        print(f"推送失败：{e}", file=sys.stderr)
    if not sent:
        print("（未配置任何推送渠道，只打印到日志）")


# ---------------------------------------------------------------- 状态
class State:
    def __init__(self, path: Path):
        self.path = path
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            self.data = {}
        self.data.setdefault("alerts", {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)


def alert(env, st: State, key: str, bad: bool, title: str, body: str, recover_msg: str, now: float, dry: bool) -> None:
    """bad=True：问题存在（首次或每 6 小时推一次）；bad=False：若之前在告警则推“已恢复”。"""
    a = st.data["alerts"]
    if bad:
        last = a.get(key)
        if last is None or now - last >= REALERT_SECONDS:
            prefix = "" if last is None else "（仍未恢复）"
            notify(env, f"⚠️ {title}{prefix}", body, dry)
            a[key] = now
    elif key in a:
        notify(env, "✅ 已恢复", recover_msg, dry)
        del a[key]


# ---------------------------------------------------------------- 检查项
def service_active(env: dict) -> bool:
    cmd = cfg(env, "KIYA_SYSTEMCTL", "systemctl")
    try:
        r = subprocess.run([cmd, "is-active", cfg(env, "KIYA_SERVICE", "brec")], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() == "active"
    except Exception:
        return False


def live_status(env: dict) -> tuple[int | None, str]:
    """返回 (live_status, title)。1=直播中，0=未开播，2=轮播；接口失败返回 (None, 错误)。"""
    room = cfg(env, "KIYA_ROOM_ID", "1724773744")
    url = cfg(env, "KIYA_LIVE_API", "https://api.live.bilibili.com/room/v1/Room/get_info") + f"?room_id={room}"
    headers = {"User-Agent": UA, "Referer": f"https://live.bilibili.com/{room}"}
    # 机房 IP 被风控（412）时，带上 B站小号 Cookie（与 yt-dlp 共用的 Netscape 格式文件）
    ck_file = Path(cfg(env, "KIYA_COOKIE_FILE", "/root/bili_cookies.txt"))
    try:
        if ck_file.exists():
            pairs = [l.split("\t") for l in ck_file.read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")]
            headers["Cookie"] = "; ".join(f"{p[5]}={p[6]}" for p in pairs if len(p) >= 7)
    except OSError:
        pass
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode())
        if d.get("code") != 0:
            return None, f"code {d.get('code')} {d.get('message') or d.get('msg')}"
        return int(d["data"]["live_status"]), d["data"].get("title", "")
    except Exception as e:
        return None, str(e)


def newest(rec_dir: Path, pattern: str) -> tuple[Path | None, float]:
    best, mt = None, 0.0
    for p in rec_dir.rglob(pattern):
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if m > mt:
            best, mt = p, m
    return best, mt


def session_files(rec_dir: Path, since: float, pattern: str) -> list[Path]:
    out = []
    for p in rec_dir.rglob(pattern):
        try:
            if p.stat().st_mtime >= since - 60:
                out.append(p)
        except OSError:
            pass
    return out


def count_danmaku(xmls: list[Path]) -> int:
    n = 0
    for x in xmls:
        try:
            n += x.read_text(encoding="utf-8", errors="replace").count("<d p=")
        except OSError:
            pass
    return n


def sync_age_hours(log: Path) -> float | None:
    """距上次“sync done”多少小时；日志不存在返回 None（未启用同步）。"""
    if not log.exists():
        return None
    last = None
    try:
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]:
            m = re.match(r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] sync done", line)
            if m:
                last = m.group(1)
    except OSError:
        return None
    if not last:
        return float("inf")
    return (time.time() - datetime.strptime(last, "%Y-%m-%d %H:%M:%S").timestamp()) / 3600


def fmt_dur(sec: float) -> str:
    sec = int(max(0, sec))
    return f"{sec // 3600} 小时 {sec % 3600 // 60} 分" if sec >= 3600 else f"{sec // 60} 分钟"


def run_analysis(env: dict, rec_dir: Path) -> str:
    """下播后自动粗筛（阶段 1：弹幕 + 音频 + 画面），返回前 5 个候选的摘要。
    KIYA_ASR=1 时先做整场语音转写（百炼，每场约 ¥1–3），候选会带上主播原话。"""
    worker = REPO / "worker"
    py = worker / ".venv" / "bin" / "python"
    if not py.exists():
        return "（worker/.venv 不存在，跳过自动分析）"
    screen = worker / "scripts" / "screen.py"
    script = screen if screen.exists() else worker / "scripts" / "analyze_danmaku.py"
    asr_note = ""
    transcribe = worker / "scripts" / "transcribe.py"
    if cfg(env, "KIYA_ASR", "0") == "1" and transcribe.exists():
        # 先转写，粗筛时候选就能带上主播原话；转写失败不影响粗筛
        try:
            r = subprocess.run([str(py), str(transcribe), str(rec_dir), "--latest", "--out", str(worker / "output")],
                               capture_output=True, text=True, timeout=int(cfg(env, "KIYA_ASR_TIMEOUT", "3600")))
            done = next((l.strip() for l in r.stdout.splitlines() if "完成：" in l or "跳过" in l), "")
            asr_note = f"语音转写：{done}" if r.returncode == 0 else \
                "语音转写失败：" + ((r.stderr or r.stdout).strip().splitlines() or ["未知错误"])[-1][:200]
        except Exception as e:
            asr_note = f"语音转写失败：{e}"
    args = [str(py), str(script), str(rec_dir), "--latest", "--out", str(worker / "output")]   # 候选数按 config 的 top_n
    if cfg(env, "KIYA_ANALYZE_NO_VIDEO", "0") == "1" and script == screen:
        args.append("--no-video")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=int(cfg(env, "KIYA_ANALYZE_TIMEOUT", "1800")))
    except Exception as e:
        return f"自动分析失败：{e}"
    if r.returncode != 0:
        return "自动分析失败：" + (r.stderr.strip().splitlines() or ["未知错误"])[-1][:200]
    out = r.stdout.splitlines()
    tops = [l.strip() for l in out if re.match(r"\s*#\s*\d+\s", l)][:5]
    head = next((l.strip() for l in out if l.startswith("==")), "")
    extra = next((l.strip() for l in out if "使用信号" in l), "")
    line = next((l.strip() for l in out if l.strip().startswith("分数线")), "")
    if any("没有达到分数线" in l for l in out):
        return "\n".join(x for x in [head, asr_note, line, "本场没有值得剪的片段，不用审核"] if x)
    if not tops:
        return "\n".join(x for x in [asr_note, "自动分析完成，但没有找到高峰", r.stdout[-300:]] if x)
    return "\n".join(x for x in [head, extra, asr_note, line, "前 5 个候选："] + tops if x)


def run_live_screen(env: dict, rec_dir: Path) -> tuple[str, str]:
    """KIYA_LIVE_SCREEN=1：直播中只对已录完的分段做一次粗筛（screen.py --live），让审核页提前有候选。
    不转写、不精排、不剪辑（2 核机器录制时 CPU 吃紧）；下播后正式粗筛会整体覆盖这次的结果。
    返回 (状态, 说明)：ok 有候选 / empty 筛了但没有过线的 / skip 还没有可筛的（录完的分段不够）/ error 出错了。"""
    worker = REPO / "worker"
    py = worker / ".venv" / "bin" / "python"
    screen = worker / "scripts" / "screen.py"
    if not (py.exists() and screen.exists()):
        return "error", "worker/.venv 或 screen.py 不存在"
    args = ["nice", "-n", "10", str(py), str(screen), str(rec_dir), "--latest", "--live",
            "--out", str(worker / "output")]
    if cfg(env, "KIYA_ANALYZE_NO_VIDEO", "0") == "1":
        args.append("--no-video")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=int(cfg(env, "KIYA_LIVE_SCREEN_TIMEOUT", "900")))
    except Exception as e:
        return "error", str(e)[:200]
    if r.returncode != 0:
        return "error", ((r.stderr or r.stdout).strip().splitlines() or ["未知错误"])[-1][:200]
    out = r.stdout.splitlines()
    cov = next((l.strip() for l in out if "边播边筛：覆盖前" in l), "")
    if not cov:
        return "skip", next((l.strip() for l in out if "先不筛" in l or "还没有录完" in l or "跳过" in l), "没有可筛的分段")
    tops = [l.strip() for l in out if re.match(r"\s*#\s*\d+\s", l)]
    return ("ok" if tops else "empty"), f"{cov.split('，')[0].replace('边播边筛：', '')}，{len(tops)} 个候选"


def run_meme_refresh(env: dict) -> tuple[str, str]:
    """KIYA_MEME_REFRESH=1（默认开）：每天抓一次B站热门标题 / 热搜、重建梗库（worker/scripts/memes.py refresh）。
    只抓文本、低优先级运行，不在直播时跑。返回 (状态, 说明)：ok / skip（脚本不在）/ error。"""
    worker = REPO / "worker"
    py = worker / ".venv" / "bin" / "python"
    script = worker / "scripts" / "memes.py"
    if not (py.exists() and script.exists()):
        return "skip", "worker/.venv 或 memes.py 不存在"
    try:
        r = subprocess.run(["nice", "-n", "10", str(py), str(script), "--out", str(worker / "output"), "refresh"],
                           capture_output=True, text=True, timeout=int(cfg(env, "KIYA_MEME_TIMEOUT", "300")))
    except Exception as e:
        return "error", str(e)[:200]
    if r.returncode != 0:
        return "error", ((r.stderr or r.stdout).strip().splitlines() or ["未知错误"])[-1][:200]
    return "ok", next((l.strip() for l in r.stdout.splitlines() if l.startswith("梗库共")), "完成")


def latest_candidates(worker: Path, max_age: float = 3600) -> Path | None:
    """刚粗筛完的场次（最近一小时写出的 candidates.json）。"""
    files = [p for p in (worker / "output").glob("*/candidates.json") if time.time() - p.stat().st_mtime < max_age]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def run_learn(env: dict) -> str | None:
    """更新精排记忆（梗、审核例子、投稿数据，见 worker/scripts/learn.py）。KIYA_LEARN=0 关闭。返回一行摘要。"""
    if cfg(env, "KIYA_LEARN", "1") != "1":
        return None
    worker = REPO / "worker"
    py = worker / ".venv" / "bin" / "python"
    script = worker / "scripts" / "learn.py"
    if not (py.exists() and script.exists()):
        return None
    try:
        r = subprocess.run(["nice", "-n", "10", str(py), str(script)], capture_output=True, text=True,
                           timeout=int(cfg(env, "KIYA_LEARN_TIMEOUT", "900")))
    except Exception as e:
        return f"精排记忆更新失败：{e}"
    if r.returncode != 0:
        return "精排记忆更新失败：" + ((r.stderr or r.stdout).strip().splitlines() or ["未知错误"])[-1][:200]
    lines = [l.strip() for l in r.stdout.splitlines() if l.startswith(("场次 ", "权重：", "注意："))]
    return "精排记忆：" + "；".join(lines)[:300] if lines else None


def run_rerank(env: dict) -> str | None:
    """KIYA_RERANK=1 时，粗筛后用多模态大模型精排（阶段 2）。需要先有转写；返回推送内容，没做时返回 None。"""
    if cfg(env, "KIYA_RERANK", "0") != "1":
        return None
    worker = REPO / "worker"
    py = worker / ".venv" / "bin" / "python"
    script = worker / "scripts" / "rerank.py"
    cands = latest_candidates(worker)
    if not (py.exists() and script.exists() and cands):
        return None
    if not (cands.parent / "transcript.json").exists():
        return "跳过精排：这场没有转写（KIYA_ASR=1 才会自动转写）"
    try:
        if not json.loads(cands.read_text(encoding="utf-8")).get("candidates"):
            return None
        r = subprocess.run(["nice", "-n", "10", str(py), str(script), str(cands)], capture_output=True, text=True,
                           timeout=int(cfg(env, "KIYA_RERANK_TIMEOUT", "1800")))
    except Exception as e:
        return f"精排失败：{e}"
    if r.returncode != 0:
        return "精排失败：" + ((r.stderr or r.stdout).strip().splitlines() or ["未知错误"])[-1][:200]
    out = r.stdout.splitlines()
    tops = [l.rstrip() for l in out if re.match(r"\s*\d+ #\d+", l)][:5]
    usage = next((l.strip() for l in out if l.startswith("用时")), "")
    return "\n".join([f"场次 {cands.parent.name} 精排完成，新的前 5（新名次 #原序号）："] + tops + ([usage] if usage else []))


def run_cut(env: dict) -> str | None:
    """粗筛后自动把候选剪成带字幕的小视频，打开标注页就能直接审。返回推送内容；没东西可剪时返回 None。"""
    worker = REPO / "worker"
    py = worker / ".venv" / "bin" / "python"
    cut = worker / "scripts" / "cut.py"
    cands = latest_candidates(worker)
    if not (py.exists() and cut.exists() and cands):
        return None
    try:
        n = len(json.loads(cands.read_text(encoding="utf-8")).get("candidates", []))
    except (OSError, ValueError):
        return None
    if not n:
        return None
    height = cfg(env, "KIYA_CUT_HEIGHT", "480")
    t0 = time.time()
    # nice：降低优先级，万一主播马上又开播，不和录制抢 CPU
    args = ["nice", "-n", "10", str(py), str(cut), str(cands), "--height", height]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=int(cfg(env, "KIYA_CUT_TIMEOUT", "5400")))
    except Exception as e:
        return f"自动剪切失败：{e}"
    out = r.stdout.splitlines()
    done = next((l.strip() for l in reversed(out) if l.startswith("完成")), "")
    sub = next((l.strip() for l in out if l.startswith("字幕：") or l.startswith("提示：")), "没有字幕")
    if r.returncode != 0 and not done:
        return "自动剪切失败：" + ((r.stderr or r.stdout).strip().splitlines() or ["未知错误"])[-1][:200]
    return "\n".join([f"场次 {cands.parent.name}：{done.split('，')[0] if done else '已完成'}，"
                      f"用时 {fmt_dur(time.time() - t0)}", sub,
                      "审核：cd /opt/kiya-clipper/worker && .venv/bin/python scripts/review.py，电脑上建隧道后打开 "
                      "http://localhost:8765"])


# ---------------------------------------------------------------- 主流程
def check(env: dict, dry: bool = False) -> int:
    now = time.time()
    rec_dir = Path(cfg(env, "REC_DIR", "/data/rec"))
    st = State(Path(cfg(env, "KIYA_STATE_FILE", "/var/lib/kiya-monitor/state.json")))
    name = cfg(env, "KIYA_STREAMER_NAME", "娜由纪雅kiya")
    room = cfg(env, "KIYA_ROOM_ID", "1724773744")
    fresh_video = float(cfg(env, "KIYA_VIDEO_STALE_SECONDS", "180"))
    fresh_xml = float(cfg(env, "KIYA_DANMAKU_STALE_SECONDS", "900"))
    grace = float(cfg(env, "KIYA_START_GRACE_SECONDS", "240"))

    # 1. 录播服务
    svc = service_active(env)
    alert(env, st, "service", not svc, "录播服务没有运行",
          "brec 不是 active 状态，开播将不会录制。\n排查：systemctl status brec；journalctl -u brec -n 50",
          "录播服务 brec 已恢复运行", now, dry)

    # 2. 开播状态
    live, info = live_status(env)
    if live is None:
        fails = st.data.get("api_fail", 0) + 1
        st.data["api_fail"] = fails
        alert(env, st, "api", fails >= 5, "查询开播状态失败",
              f"连续 {fails} 次查询 B站直播间状态失败：{info[:150]}", "开播状态查询已恢复", now, dry)
    else:
        st.data["api_fail"] = 0
        alert(env, st, "api", False, "", "", "开播状态查询已恢复", now, dry)
        was_live = st.data.get("live", False)
        is_live = live == 1
        vid, vid_m = newest(rec_dir, "*.flv")
        xml, xml_m = newest(rec_dir, "*.xml")
        recording = vid is not None and now - vid_m < fresh_video

        if is_live and not was_live:
            st.data.update(live=True, live_since=now, live_title=info)
            notify(env, f"🔴 {name} 开播了", f"《{info}》\nhttps://live.bilibili.com/{room}\n"
                   + ("录制正常 ✅" if recording else "录像文件还没开始写入，几分钟后会再检查"), dry)
        elif not is_live and was_live:
            since = st.data.get("live_since", now)
            vids = session_files(rec_dir, since, "*.flv")
            xmls = session_files(rec_dir, since, "*.xml")
            size = sum(p.stat().st_size for p in vids if p.exists()) / 1e9
            body = (f"《{st.data.get('live_title', '')}》时长约 {fmt_dur(now - since)}\n"
                    f"录像 {len(vids)} 段，共 {size:.1f} GB；弹幕 {count_danmaku(xmls)} 条")
            notify(env, f"⚪ {name} 下播了", body, dry)
            st.data.update(live=False, live_since=None, live_screen_at=None, live_screen_notified=None)
            st.data["alerts"].pop("live_screen", None)
            for k in ("not_recording", "danmaku_stale"):
                st.data["alerts"].pop(k, None)
            if cfg(env, "KIYA_AUTO_ANALYZE", "1") == "1":
                st.save()  # 分析可能较慢，先存状态，避免下一轮重复触发
                notify(env, f"📈 {name} 弹幕分析", run_analysis(env, rec_dir), dry)
                if not dry:
                    learned = run_learn(env)             # 先更新记忆，精排就能用上刚下播这场的梗
                    msg = run_rerank(env)
                    if msg or learned:
                        notify(env, f"🤖 {name} 大模型精排", "\n".join(x for x in [msg, learned] if x), dry)
                if cfg(env, "KIYA_AUTO_CUT", "1") == "1" and not dry:
                    msg = run_cut(env)
                    if msg:
                        notify(env, f"✂️ {name} 片段已剪好", msg, dry)

        if is_live and was_live and recording and not dry and cfg(env, "KIYA_LIVE_SCREEN", "0") == "1":
            every = float(cfg(env, "KIYA_LIVE_SCREEN_MINUTES", "30")) * 60
            last = st.data.get("live_screen_at") or st.data.get("live_since") or now
            if now - last >= every:
                st.data["live_screen_at"] = now
                st.save()                        # 筛一次要几分钟，先存状态，下一轮 cron 不会重复触发
                status, msg = run_live_screen(env, rec_dir)
                alert(env, st, "live_screen", status == "error", "边播边筛失败",
                      f"{msg}\n不影响录制，下播后仍会正常粗筛。", "边播边筛已恢复", time.time(), dry)
                if status == "skip":
                    st.data["live_screen_at"] = now - every + 300       # 录完的分段还不够，5 分钟后再看
                elif status == "ok" and st.data.get("live_screen_notified") != st.data.get("live_since"):
                    st.data["live_screen_notified"] = st.data.get("live_since")
                    notify(env, f"👀 {name} 边播边筛", f"{msg}\n审核页已经能看到了；下播后会重新筛一遍，序号可能变，"
                           "已经标的会按时间位置对应过去。", dry)

        if is_live:
            since = st.data.get("live_since", now)
            in_grace = now - since < grace
            alert(env, st, "not_recording", not recording and not in_grace, "开播了但没有在录制",
                  f"{name} 正在直播，但 {fmt_dur(fresh_video)}内没有录像写入。\n"
                  "排查：systemctl status brec；录播姬管理界面里房间是否开了自动录制；Cookie 是否失效",
                  "录像已恢复写入", now, dry)
            xml_ok = xml is not None and now - xml_m < fresh_xml
            alert(env, st, "danmaku_stale", recording and not xml_ok and not in_grace, "弹幕可能没在录",
                  f"录像在写入，但 {fmt_dur(fresh_xml)}内弹幕文件没有更新。\n"
                  "如果直播间本来就很安静可忽略；否则检查录播姬弹幕设置和 Cookie",
                  "弹幕文件已恢复更新", now, dry)

    # 2.5 梗库每日刷新：不在直播时、每个日历日一次；失败当天不重试（告警一次），第二天再来
    if (cfg(env, "KIYA_MEME_REFRESH", "1") == "1" and not dry and not st.data.get("live")
            and st.data.get("meme_refresh_day") != datetime.now().strftime("%F")):
        st.data["meme_refresh_day"] = datetime.now().strftime("%F")
        st.save()
        status, msg = run_meme_refresh(env)
        alert(env, st, "meme_refresh", status == "error", "梗库刷新失败",
              f"{msg}\n不影响录制和剪辑，梗库沿用上次的结果；明天会再试。手动试：worker/.venv/bin/python worker/scripts/memes.py refresh",
              "梗库刷新已恢复", now, dry)

    # 3. 硬盘
    try:
        du = shutil.disk_usage(rec_dir if rec_dir.exists() else "/")
        pct = du.used / du.total * 100
        limit = float(cfg(env, "KIYA_DISK_ALERT_PERCENT", "80"))
        alert(env, st, "disk", pct >= limit, "硬盘快满了",
              f"{rec_dir} 所在磁盘已用 {pct:.0f}%，剩余 {du.free / 1e9:.1f} GB。\n写满后会漏录：检查 OSS 同步是否正常，或手动清理",
              f"硬盘使用率已降到 {pct:.0f}%", now, dry)
    except OSError:
        pass

    # 4. OSS 同步
    age = sync_age_hours(Path(cfg(env, "KIYA_SYNC_LOG", "/var/log/kiya-sync.log")))
    if age is not None:
        limit_h = float(cfg(env, "KIYA_SYNC_STALE_HOURS", "3"))
        alert(env, st, "sync", age > limit_h, "OSS 同步太久没成功",
              ("同步日志里还没有成功记录" if age == float("inf") else f"上次同步成功是 {age:.1f} 小时前")
              + "。\n排查：tail -50 /var/log/kiya-sync.log；rclone lsd oss:",
              "OSS 同步已恢复", now, dry)

    st.data["last_check"] = datetime.now().strftime("%F %T")
    st.save()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--test-notify", action="store_true", help="发一条测试消息，确认推送配置正确")
    ap.add_argument("--dry-run", action="store_true", help="只打印要推送的内容，不真的推送")
    args = ap.parse_args()
    env = load_env()
    if args.test_notify:
        notify(env, "kiya 监控测试", f"推送配置正常 {datetime.now():%F %T}", args.dry_run)
        return 0
    return check(env, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
