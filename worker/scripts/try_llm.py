#!/usr/bin/env python3
"""阶段 0 试用：让多模态大模型（阿里云百炼 Qwen-VL）判断一个片段有不有趣。

三种输入方式（任选一种）：
  # 1) 直接用弹幕分析的结果：第 3 个高峰（自动找视频、抽帧、带上高频弹幕）
  python scripts/try_llm.py --peaks output/20261110-2000/peaks.csv --rank 3 --rec-dir /data/rec

  # 2) 指定视频和时间段（秒，视频文件内的时间）
  python scripts/try_llm.py --video /data/rec/xxx.flv --start 600 --end 660

  # 3) 还没录到直播时：用任意几张截图试
  python scripts/try_llm.py --images a.jpg b.jpg --danmaku "哈哈哈 | 草 | ？？？"

可选：--transcript "主播说的话"（阶段 2 会由语音转写自动填）、--model、--dry-run（只抽帧不调用）。
API Key 从环境变量 DASHSCOPE_API_KEY 或仓库根目录 .env 读取，不要写进代码或命令行历史。
每次结果保存到 output/llm_trials/，方便比较不同模型和提示词。
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
PROMPT_VERSION = "rank-v0"

SYSTEM_PROMPT = """你是 B站 VTuber 直播切片的资深剪辑编辑，擅长判断哪些直播片段单独剪出来会好笑、有梗、有传播力。
你会拿到：主播资料、片段的若干张按时间顺序的画面截图、片段期间观众刷得最多的弹幕，以及（可能有的）主播原话。
请只根据给到的信息判断，看不出来的就如实说看不出来，不要编造画面里没有的内容。
只输出一个 JSON 对象，不要输出任何其他文字。"""

OUTPUT_SPEC = """输出 JSON 字段：
{
  "funny": true/false,                // 这个片段单独剪出来是否值得做切片
  "score": 1-10,                      // 切片价值，7 分以上值得发
  "type": "破防|口胡|互动|金句|高能|整活|其他|无",
  "standalone": true/false,           // 脱离直播上下文，路人能不能看懂
  "what_happened": "一句话描述片段里发生了什么（基于画面和弹幕）",
  "reason": "为什么有趣 / 为什么不值得切",
  "punch_frame": 0,                   // 你认为笑点最可能在哪张截图（从 0 开始的序号），看不出填 -1
  "title_candidates": ["3 个 B站风格的切片标题，参考主播自己的标题风格：抽象、自嘲、带感叹号"],
  "missing_info": "要做出更准判断还缺什么信息（例如主播原话）"
}"""


def load_env() -> None:
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$", line)
            if m and m.group(1) not in os.environ and m.group(2):
                os.environ[m.group(1)] = m.group(2).strip().strip('"').strip("'")


def run_ffmpeg_frames(video: Path, start: float, end: float, n: int, out_dir: Path, width: int) -> list[Path]:
    if not shutil.which("ffmpeg"):
        sys.exit("找不到 ffmpeg，请先安装：apt install -y ffmpeg")
    dur = max(1.0, end - start)
    frames = []
    for i in range(n):
        t = start + dur * (i + 0.5) / n
        dst = out_dir / f"frame_{i:02d}.jpg"
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video),
               "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", str(dst)]
        subprocess.run(cmd, check=True)
        if dst.exists() and dst.stat().st_size > 0:
            frames.append(dst)
    if not frames:
        sys.exit(f"没有抽到画面，检查视频文件和时间：{video} {start}-{end}")
    return frames


def from_peaks(csv_path: Path, rank: int, rec_dir: Path) -> dict:
    with open(csv_path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    row = next((r for r in rows if r["序号"] == str(rank)), None)
    if not row:
        sys.exit(f"{csv_path} 里没有序号 {rank}")
    name = row["视频文件"]
    matches = list(rec_dir.rglob(name))
    if not matches:
        sys.exit(f"在 {rec_dir} 下找不到视频 {name}（是否已被同步脚本移到 OSS？）")
    to_sec = lambda s: sum(int(x) * k for x, k in zip(s.split(":"), (3600, 60, 1)))
    start = float(row["文件内起点秒"])
    dur = to_sec(row["片段止"]) - to_sec(row["片段起"])
    return {"video": matches[0], "start": start, "end": start + dur,
            "danmaku": row["高频弹幕"], "label": f"{csv_path.parent.name} #{rank} {row['高峰时间']}"}


def build_messages(cfg: dict, images: list[Path], danmaku: str, transcript: str, seconds_per_frame: float | None) -> list:
    s = cfg.get("streamer", {})
    lore = cfg.get("lore", {})
    j = lambda key: "、".join(str(x) for x in (lore.get(key) or []))
    profile = (
        f"主播：{s.get('name', '')}（B站虚拟主播，Live2D 模型）\n"
        f"人设：{j('persona')}\n"
        f"昵称：{j('nicknames')}；观众被称为“{lore.get('audience_call', '')}”；粉丝名 {lore.get('fan_name', '')}\n"
        f"常见话题：{j('recurring_topics')}\n"
        f"口头禅：{j('catchphrases')}\n"
        "她自己做过的高播放切片标题示例：《舰长寄来律师函：请停止你的报恩行为》《妈妈我D到傻V了》《你们都是禁言凶手！！！》"
    )
    timing = f"每张截图间隔约 {seconds_per_frame:.0f} 秒" if seconds_per_frame else "截图按时间顺序排列"
    text = (
        f"【主播资料】\n{profile}\n\n"
        f"【片段信息】\n共 {len(images)} 张画面截图，{timing}。\n"
        f"片段期间刷得最多的弹幕：{danmaku or '（无）'}\n"
        f"主播原话：{transcript or '（暂无，阶段 2 接入语音转写后提供）'}\n\n"
        f"{OUTPUT_SPEC}"
    )
    content = []
    for i, img in enumerate(images):
        b64 = base64.b64encode(img.read_bytes()).decode()
        content.append({"type": "text", "text": f"截图 {i}："})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    content.append({"type": "text", "text": text})
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}]


def call_api(base_url: str, api_key: str, model: str, messages: list, timeout: int = 120) -> tuple[dict, float]:
    body = json.dumps({"model": model, "messages": messages, "temperature": 0.3}).encode()
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"接口返回错误 {e.code}：{e.read().decode(errors='replace')[:800]}")
    return data, time.time() - t0


def parse_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--peaks", help="弹幕分析输出的 peaks.csv")
    src.add_argument("--video", help="视频文件")
    src.add_argument("--images", nargs="+", help="直接给几张截图")
    ap.add_argument("--rank", type=int, default=1, help="配合 --peaks：第几个高峰")
    ap.add_argument("--rec-dir", default="/data/rec", help="配合 --peaks：录像目录")
    ap.add_argument("--start", type=float, help="配合 --video：开始秒")
    ap.add_argument("--end", type=float, help="配合 --video：结束秒")
    ap.add_argument("--danmaku", default="", help="片段期间的高频弹幕，用 | 分隔")
    ap.add_argument("--transcript", default="", help="主播原话（可选）")
    ap.add_argument("--frames", type=int, default=8, help="抽几张图，默认 8")
    ap.add_argument("--width", type=int, default=640, help="截图宽度，越大越清楚也越贵")
    ap.add_argument("--model", help="模型名，默认读 .env 的 DASHSCOPE_MODEL，再默认 qwen3-vl-flash")
    ap.add_argument("--config", default=str(REPO / "config" / "kiya.yaml"))
    ap.add_argument("--price-in", type=float, help="输入单价（元 / 百万 tokens），填了就估算费用")
    ap.add_argument("--price-out", type=float, help="输出单价（元 / 百万 tokens）")
    ap.add_argument("--dry-run", action="store_true", help="只抽帧、拼提示词，不调用接口")
    ap.add_argument("--out", default=str(ROOT / "output" / "llm_trials"))
    args = ap.parse_args()

    load_env()
    args.model = args.model or os.environ.get("DASHSCOPE_MODEL") or "qwen3-vl-flash"
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    out_root = Path(args.out)
    run_dir = out_root / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    label, danmaku, spf = "images", args.danmaku, None
    if args.images:
        missing = [p for p in args.images if not Path(p).is_file()]
        if missing:
            sys.exit(f"找不到图片：{', '.join(missing)}（请先把截图上传到服务器，再写实际路径）")
        images = []
        for i, p in enumerate(args.images):
            dst = run_dir / f"frame_{i:02d}{Path(p).suffix.lower() or '.jpg'}"
            shutil.copy(p, dst)
            images.append(dst)
    else:
        if args.peaks:
            info = from_peaks(Path(args.peaks), args.rank, Path(args.rec_dir))
            danmaku = danmaku or info["danmaku"]
            label = info["label"]
        else:
            if args.start is None or args.end is None:
                sys.exit("--video 需要同时给 --start 和 --end")
            info = {"video": Path(args.video), "start": args.start, "end": args.end}
            label = f"{Path(args.video).name} {args.start:.0f}-{args.end:.0f}s"
        images = run_ffmpeg_frames(info["video"], info["start"], info["end"], args.frames, run_dir, args.width)
        spf = (info["end"] - info["start"]) / len(images)

    messages = build_messages(cfg, images, danmaku, args.transcript, spf)
    preview = [m if isinstance(m["content"], str) else
               {"role": m["role"], "content": [c if c["type"] == "text" else {"type": "image_url", "image_url": "<base64 省略>"}
                                               for c in m["content"]]} for m in messages]
    (run_dir / "request_preview.json").write_text(json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"片段：{label}，截图 {len(images)} 张，已保存到 {run_dir}")

    if args.dry_run:
        print("dry-run：未调用接口。提示词预览见 request_preview.json")
        return 0

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        sys.exit("没有找到 DASHSCOPE_API_KEY：在仓库根目录 .env 里填写，或 export DASHSCOPE_API_KEY=...")
    base_url = os.environ.get("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL)
    data, secs = call_api(base_url, api_key, args.model, messages)
    text = data["choices"][0]["message"]["content"]
    if isinstance(text, list):
        text = "".join(c.get("text", "") for c in text)
    result = parse_json(text)
    usage = data.get("usage", {})

    record = {"label": label, "model": args.model, "prompt_version": PROMPT_VERSION, "frames": len(images),
              "width": args.width, "danmaku": danmaku, "transcript": args.transcript,
              "latency_s": round(secs, 1), "usage": usage, "result": result, "raw": None if result else text}
    if args.price_in is not None and args.price_out is not None:
        cost = usage.get("prompt_tokens", 0) / 1e6 * args.price_in + usage.get("completion_tokens", 0) / 1e6 * args.price_out
        record["cost_yuan"] = round(cost, 5)
    (run_dir / "result.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n模型 {args.model}，耗时 {secs:.1f} 秒，tokens：输入 {usage.get('prompt_tokens')} / 输出 {usage.get('completion_tokens')}"
          + (f"，约 ¥{record['cost_yuan']}" if "cost_yuan" in record else ""))
    if result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("模型没有返回合法 JSON，原文：\n" + text)
    print(f"\n完整结果：{run_dir / 'result.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
