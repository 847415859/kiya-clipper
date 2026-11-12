#!/usr/bin/env python3
"""一键发布到 B站：用 biliup 命令行把成片 + 封面 + 发布包里的标题 / 简介 / 标签投上去。

用法：
  python scripts/publish_bili.py output/<场次>/candidates.json --rank 3 --mode hold            # 先放着（默认）
  python scripts/publish_bili.py output/<场次>/candidates.json --rank 3 --mode now             # 立即公开发布
  python scripts/publish_bili.py output/<场次>/candidates.json --rank 3 --mode timed --at 2026-11-20T20:00
  python scripts/publish_bili.py output/<场次>/candidates.json --rank 3 --dry-run              # 只打印命令，不上传
  python scripts/publish_bili.py --status                                                      # biliup 装了没 / 登录了没

发布方式：
  hold     定时发布设到 hold_days 天后（默认 14 天，B站定时发布最长约 15 天）；创作中心里可以改时间 / 撤回，相当于草稿
  now      立即公开
  timed    自选时间，必须在 4 小时之后、15 天之内
  private  仅自己可见：biliup 没有这个参数，只有在 config 的 publish.bili.private_args 里填了验证过的参数才可用

安全闸：违禁词命中默认拒绝（--allow-taboo 放行）、已成功投过默认拒绝（--again 重投）、两次上传之间有最短间隔、
必须有渲染好的成片、biliup 的参数不认识就停下不猜。登录由你在服务器上自己 `biliup login`，这里不碰密码。
结果写到 output/<场次>/publish/<序号>.bili.json；最后一行输出这份 JSON。
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
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clipper.kcfg import load_cfg  # noqa: E402
from clipper.kcfg import decisions_path  # noqa: E402
from clipper.streamer import resolve_config, streamer_from_session_dir  # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")   # biliup 输出里的颜色控制码
MIN_AHEAD = 4 * 3600 + 600          # B站要求定时发布晚于提交 4 小时以上，多留 10 分钟余量
MAX_AHEAD = 15 * 24 * 3600 - 3600   # 上限约 15 天，少 1 小时留余量
MODES = ("hold", "now", "timed", "private")
DEFAULTS = {"hold_days": 14, "min_interval_sec": 600, "tid": 21, "line": "", "submit": "", "limit": 3, "retry": 0,
            "timeout_sec": 3600, "height": 720}


class Refuse(Exception):
    """不满足发布条件（不是程序错误）。"""


def read(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
    except (OSError, ValueError):
        return default


def bili_cfg(cfg: dict) -> dict:
    out = dict(DEFAULTS)
    out.update({k: v for k, v in ((cfg.get("publish") or {}).get("bili") or {}).items() if v is not None})
    return out


def biliup_bin() -> str | None:
    p = os.environ.get("KIYA_BILIUP") or "biliup"
    return shutil.which(p) or (p if Path(p).is_file() and os.access(p, os.X_OK) else None)


def cookie_path(bc: dict) -> Path:
    return Path(os.environ.get("KIYA_BILIUP_COOKIE") or bc.get("cookie") or "~/.config/biliup/cookies.json").expanduser()


def status(cfg: dict) -> dict:
    bc = bili_cfg(cfg)
    ck = cookie_path(bc)
    return {"biliup": biliup_bin() is not None, "logged_in": ck.is_file() and ck.stat().st_size > 2,
            "cookie": str(ck), "hold_days": bc["hold_days"], "private": bool(bc.get("private_args")),
            "tid": bc["tid"], "height": int(bc["height"]), "min_interval_sec": bc["min_interval_sec"]}


def find_video(sess: Path, rank: int, height: int = 720) -> Path:
    """发布用的成片：不超过 height 的最高一份；都比 height 高就用最接近的那份。没有渲染好的成片就拒绝
    （审核页的低清片段不能直接发）。"""
    finals = []
    for f in (sess / "final").glob(f"{rank:02d}_*p.mp4") if (sess / "final").exists() else []:
        try:
            finals.append((int(f.stem.rsplit("_", 1)[1][:-1]), f))
        except ValueError:
            continue
    if not finals:
        raise Refuse("还没有渲染好的成片：先在编辑页点“渲染成片”（审核页的低清片段不能直接发）")
    fit = [x for x in finals if x[0] <= int(height)]
    return max(fit)[1] if fit else min(finals)[1]


def resolve_dtime(mode: str, at: str | None, hold_days: float, now: float | None = None) -> int | None:
    now = time.time() if now is None else now
    if mode == "now":
        return None
    if mode == "hold":
        t = now + min(float(hold_days) * 86400, MAX_AHEAD)
        return int(max(t, now + MIN_AHEAD))
    if mode == "timed":
        if not at:
            raise Refuse("定时发布需要指定时间（--at 2026-11-20T20:00）")
        try:
            t = datetime.fromisoformat(at).timestamp()
        except ValueError:
            raise Refuse(f"时间格式不对：{at}（要像 2026-11-20T20:00）")
        if t < now + MIN_AHEAD:
            raise Refuse("定时发布的时间要比现在晚 4 小时以上")
        if t > now + MAX_AHEAD:
            raise Refuse("定时发布最多设到 15 天以内")
        return int(t)
    return None


def build_cmd(bin_: str, cookie: Path, video: Path, pack: dict, cover: Path | None, bc: dict, dtime: int | None,
              extra: list[str]) -> list[str]:
    cmd = [bin_, "-u", str(cookie), "upload", str(video), "--copyright", "2", "--source", pack.get("source") or "",
           "--tid", str(bc["tid"]), "--title", pack["title"], "--desc", pack.get("description") or "",
           "--tag", ",".join(pack.get("tags") or [])]
    if bc.get("limit"):
        cmd += ["--limit", str(bc["limit"])]
    if bc.get("retry"):                                   # 有的 biliup 版本没有 --retry，默认不传（配置里填了才传）
        cmd += ["--retry", str(bc["retry"])]
    if bc.get("submit"):
        cmd += ["--submit", str(bc["submit"])]
    if bc.get("line"):
        cmd += ["--line", str(bc["line"])]
    if cover:
        cmd += ["--cover", str(cover)]
    if dtime:
        cmd += ["--dtime", str(dtime)]
    return cmd + list(extra)


def check_flags(bin_: str, cmd: list[str]) -> None:
    """biliup 版本不同参数会变：用到的 --xxx 在 `upload --help` 里找不到就停，不带着猜的参数上传。"""
    r = subprocess.run([bin_, "upload", "--help"], capture_output=True, text=True, timeout=30)
    helptext = (r.stdout or "") + (r.stderr or "")
    used = sorted({a for a in cmd[1:] if a.startswith("--") and len(a) > 2})
    missing = [a for a in used if a not in helptext]
    if missing:
        raise Refuse(f"当前 biliup 不认识这些参数：{' '.join(missing)}。请升级 biliup，或在 publish.bili 里改配置")


def mask(cmd: list[str]) -> list[str]:
    out = list(cmd)
    for i, a in enumerate(out[:-1]):
        if a in ("-u", "--user-cookie"):
            out[i + 1] = "<cookie>"
    return out


def check_rate(sess: Path, bc: dict, now: float) -> None:
    last = read(sess.parent / "_bili_last.json", {}) or {}
    wait = float(last.get("at", 0)) + float(bc["min_interval_sec"]) - now
    if wait > 0:
        raise Refuse(f"离上一次上传太近了，请 {int(wait)} 秒后再试（防止触发 B站风控）")


def run_biliup(cmd: list[str], timeout: float) -> tuple[str, int]:
    """边跑边把 biliup 的输出转到任务日志（最多每 10 秒一行，免得进度条刷屏），后台点“取消”时一起结束 biliup。"""
    import signal
    import threading

    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")

    def stop(*_):
        p.kill()
        raise SystemExit(130)

    signal.signal(signal.SIGTERM, stop)
    timer = threading.Timer(timeout, p.kill)
    timer.start()
    lines: list[str] = []
    last = 0.0
    try:
        for line in p.stdout:
            line = ANSI.sub("", line).rstrip()
            if not line:
                continue
            lines.append(line)
            if time.time() - last >= 10:
                print(line[:200], flush=True)
                last = time.time()
        code = p.wait()
    finally:
        timer.cancel()
        if p.poll() is None:
            p.kill()
    out = "\n".join(lines)
    if code < 0 and not out:
        return "上传被中断或超时", 124
    return out, code


def bind_bvid(sess: Path, rank: int, bvid: str) -> None:
    """把 BV 号写进精排记忆的决定文件（和后台卡片里手填 BV 号一样），learn.py 下次更新记忆时拉它的播放数据。
    同一片段已有绑定会被替换，已填的完播率 / 涨粉保留。"""
    path = Path(os.environ.get("KIYA_MEMORY_DECISIONS") or decisions_path(streamer_from_session_dir(sess)))
    dec = read(path, {}) or {}
    pub = dec.setdefault("published", {})
    keep: dict = {}
    for k in [k for k, v in pub.items() if isinstance(v, dict) and v.get("session") == sess.name and v.get("rank") == rank]:
        keep = {x: y for x, y in pub.pop(k).items() if x in ("completion", "followers")}
    pub[bvid] = {"session": sess.name, "rank": rank, **keep}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(dec, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def run(args: argparse.Namespace) -> dict:
    cpath = Path(args.candidates)
    sess = cpath.parent
    cfg = load_cfg(resolve_config(args.config, sess))
    bc = bili_cfg(cfg)
    data = json.loads(cpath.read_text(encoding="utf-8"))
    if not any(c["rank"] == args.rank for c in data["candidates"]):
        raise Refuse(f"没有序号 {args.rank} 的候选")
    if args.mode not in MODES:
        raise Refuse(f"不支持的发布方式：{args.mode}")
    pf = sess / "publish" / f"{args.rank:02d}.json"
    pack = read(pf, None)
    if not pack:
        raise Refuse("还没有发布包：先打开编辑页的“发布”卡片生成标题 / 简介 / 标签")
    if not (pack.get("title") or "").strip():
        raise Refuse("标题是空的")
    if not pack.get("source"):
        raise Refuse("来源（直播间链接）是空的：转载类型必须填来源，请检查主播配置的 live_url")
    if pack.get("taboo_hits") and not args.allow_taboo:
        raise Refuse(f"标题 / 简介 / 说话内容命中禁区词：{'、'.join(pack['taboo_hits'])}。确认没问题再勾“仍然发布”")
    result_file = sess / "publish" / f"{args.rank:02d}.bili.json"
    prev = read(result_file, {}) or {}
    if prev.get("status") == "ok" and not args.again:
        raise Refuse(f"这条已经在 {prev.get('at_text', '之前')} 投过了（{prev.get('mode_text', '')}），要重投请勾“重新投稿”")

    now = time.time()
    extra: list[str] = []
    if args.mode == "private":
        extra = [str(x) for x in bc.get("private_args") or []]
        if not extra:
            raise Refuse("“仅自己可见”没有配置：biliup 没有这个参数，请在 publish.bili.private_args 里填验证过的参数")
    video = find_video(sess, args.rank, int(bc["height"]))
    cover = sess / "publish" / f"{args.rank:02d}_cover.jpg"
    dtime = resolve_dtime(args.mode, args.at, bc["hold_days"], now)
    bin_ = biliup_bin()
    ck = cookie_path(bc)
    mode_text = {"hold": f"先放着（{datetime.fromtimestamp(dtime):%m-%d %H:%M} 定时，可在创作中心改 / 撤回）" if dtime else "",
                 "now": "立即公开发布",
                 "timed": f"定时 {datetime.fromtimestamp(dtime):%m-%d %H:%M}" if dtime else "",
                 "private": "仅自己可见"}[args.mode]
    base = {"session": sess.name, "rank": args.rank, "mode": args.mode, "mode_text": mode_text, "dtime": dtime,
            "video": video.name, "title": pack["title"], "at": now, "at_text": datetime.fromtimestamp(now).strftime("%m-%d %H:%M")}

    if args.dry_run:
        cmd = build_cmd(bin_ or "biliup", ck, video, pack, cover if cover.exists() else None, bc, dtime, extra)
        base.update(status="dry_run", command=mask(cmd), biliup=bin_ is not None, logged_in=ck.is_file())
        print("预演（不会上传）：" + " ".join(json.dumps(x, ensure_ascii=False) if " " in x else x for x in mask(cmd)))
        return base

    if not bin_:
        raise Refuse("服务器上没有找到 biliup：先安装（见 deploy/README.md 的“一键发布”）")
    if not ck.is_file():
        raise Refuse(f"没有找到登录文件 {ck}：先在服务器上执行 `biliup login`（见 deploy/README.md）")
    check_rate(sess, bc, now)
    cmd = build_cmd(bin_, ck, video, pack, cover if cover.exists() else None, bc, dtime, extra)
    check_flags(bin_, cmd)
    print(f"开始上传 {video.name}（{mode_text}）…", flush=True)
    (sess.parent / "_bili_last.json").write_text(json.dumps({"at": now, "session": sess.name, "rank": args.rank}),
                                                 encoding="utf-8")   # 先占位：上传中途失败也算一次，避免狂点重试
    out, code = run_biliup(cmd, float(bc["timeout_sec"]))
    tail = "\n".join(out.splitlines()[-12:])
    m = re.search(r"\bBV[0-9A-Za-z]{10}\b", out)
    base.update(status="ok" if code == 0 else "failed", exit_code=code, output_tail=tail, bvid=m.group(0) if m else "")
    if prev.get("status") == "ok":
        base["previous"] = {k: prev.get(k) for k in ("mode_text", "at_text", "bvid")}
    if code == 0 and base["bvid"]:
        try:
            bind_bvid(sess, args.rank, base["bvid"])
            base["bound"] = True
        except (OSError, ValueError) as e:
            base["bound"] = False
            base["bind_error"] = str(e)
    if code == 0:
        base["visibility_note"] = "请在创作中心确认这条的状态" if args.mode in ("hold", "private") else ""
    return base


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("candidates", nargs="?")
    ap.add_argument("--rank", type=int)
    ap.add_argument("--mode", default="hold", choices=MODES)
    ap.add_argument("--at", help="定时发布时间 2026-11-20T20:00（本机时区）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-taboo", action="store_true")
    ap.add_argument("--again", action="store_true", help="已经投过也再投一次")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    if args.status:
        cfg = load_cfg(resolve_config(args.config, Path(args.candidates).parent if args.candidates else None))
        print(json.dumps(status(cfg), ensure_ascii=False))
        return 0
    if not args.candidates or not args.rank:
        ap.error("需要 candidates.json 和 --rank")
    try:
        res = run(args)
    except Refuse as e:
        print(str(e), file=sys.stderr)
        return 2
    if res["status"] != "dry_run":
        out = Path(args.candidates).parent / "publish" / f"{args.rank:02d}.bili.json"
        out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res["status"] in ("ok", "dry_run") else 1


if __name__ == "__main__":
    raise SystemExit(main())
