#!/usr/bin/env python3
"""试用阿里云百炼录音文件识别（Paraformer / Fun-ASR），评估阶段 2 的语音转写方案。

不需要 GPU：音频上传到你自己的 OSS，生成一个几小时后失效的签名链接交给百炼识别。
2 核 2G 的录制机就能跑；4 小时音频按官方单价约 ¥1.2（paraformer-v2）/ ¥3.2（fun-asr）。

用法：
  python scripts/try_asr.py /tmp/kiya_test.mp4                              # 整个文件，paraformer-v2
  python scripts/try_asr.py /tmp/kiya_test.mp4 --model fun-asr              # 换模型对比
  python scripts/try_asr.py /data/rec/xxx.flv --start 600 --end 900         # 录像里的一段
  python scripts/try_asr.py /tmp/kiya_test.mp4 --model paraformer-v2 fun-asr  # 两个模型一起跑，便于对比

前提：
  - 仓库根目录 .env 里有 DASHSCOPE_API_KEY；控制台已开通所用模型
  - rclone 已配置名为 oss 的远端（阶段 0 同步录像时配好的）。上传走内网；签名链接临时换成公网
    endpoint 生成（百炼的服务器访问不到 OSS 内网地址），不会改动 rclone 配置文件
结果保存到 output/asr_trials/<时间>/：每个模型一份 JSON（句子 + 词级时间戳）和一份可读的 txt。
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
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
PRICE_PER_SEC = {"paraformer-v2": 0.00008, "fun-asr": 0.00022}   # 元/秒，北京地域官方单价（2026-11 查询）


def load_env() -> None:
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
            if m and m.group(1) not in os.environ and m.group(2):
                os.environ[m.group(1)] = m.group(2).strip('"').strip("'")


def extract_audio(src: Path, dst: Path, start: float | None, end: float | None) -> float:
    """抽成 16kHz 单声道 MP3（约 0.24 MB/分钟），返回时长秒。"""
    ff = shutil.which("ffmpeg") or sys.exit("找不到 ffmpeg")
    cmd = [ff, "-nostdin", "-loglevel", "error", "-y"]
    if start is not None:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(src)]
    if end is not None:
        cmd += ["-t", str(end - (start or 0))]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "32k", str(dst)]
    subprocess.run(cmd, check=True)
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1",
                        str(dst)], capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def public_endpoint() -> str:
    """百炼的服务器访问不到 OSS 内网地址，签名链接必须用公网 endpoint。"""
    ep = os.environ.get("OSS_ENDPOINT_PUBLIC") or os.environ.get("OSS_ENDPOINT_INTERNAL") or "oss-cn-beijing.aliyuncs.com"
    return ep.replace("-internal", "")


def upload_and_sign(local: Path, bucket: str, key: str, hours: int = 6) -> str:
    rc = os.environ.get("RCLONE", "rclone")
    subprocess.run([rc, "copyto", str(local), f"oss:{bucket}/{key}"], check=True)
    # 连接串语法临时覆盖 endpoint，不改 rclone 配置文件
    r = subprocess.run([rc, "link", "--expire", f"{hours}h", f"oss,endpoint={public_endpoint()}:{bucket}/{key}"],
                       capture_output=True, text=True)
    url = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    if r.returncode != 0 or not url.startswith("http"):
        sys.exit(f"生成签名链接失败：{(r.stderr or r.stdout).strip()[:300]}")
    return url


def http_json(url: str, key: str, body: dict | None = None, async_: bool = False) -> dict:
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if async_:
        headers["X-DashScope-Async"] = "enable"
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"接口返回错误 {e.code}：{e.read().decode(errors='replace')[:500]}")


def transcribe(host: str, key: str, model: str, file_url: str, timeout: int = 1800) -> tuple[dict, float]:
    body = {"model": model, "input": {"file_urls": [file_url]},
            "parameters": {"timestamp_alignment_enabled": True}}
    if model.startswith("paraformer"):
        body["parameters"]["language_hints"] = ["zh", "en", "ja"]
    t0 = time.time()
    sub = http_json(f"{host}/api/v1/services/audio/asr/transcription", key, body, async_=True)
    task = sub.get("output", {}).get("task_id")
    if not task:
        sys.exit(f"提交任务失败：{json.dumps(sub, ensure_ascii=False)[:300]}")
    while True:
        time.sleep(2)
        st = http_json(f"{host}/api/v1/tasks/{task}", key)
        status = st.get("output", {}).get("task_status")
        if status == "SUCCEEDED":
            break
        if status not in ("PENDING", "RUNNING"):
            sys.exit(f"识别失败：{json.dumps(st, ensure_ascii=False)[:500]}")
        if time.time() - t0 > timeout:
            sys.exit("识别超时")
    results = st["output"].get("results", [])
    ok = [r for r in results if r.get("subtask_status") == "SUCCEEDED"]
    if not ok:
        sys.exit(f"子任务失败：{json.dumps(results, ensure_ascii=False)[:500]}")
    with urllib.request.urlopen(ok[0]["transcription_url"], timeout=60) as r:
        return json.loads(r.read().decode()), time.time() - t0


def summarize(result: dict) -> tuple[list[dict], int, int]:
    sents, words = [], 0
    for tr in result.get("transcripts", []):
        for s in tr.get("sentences", []):
            sents.append({"begin": s["begin_time"] / 1000, "end": s["end_time"] / 1000, "text": s.get("text", "")})
            words += len(s.get("words", []))
    chars = sum(len(s["text"]) for s in sents)
    return sents, words, chars


def fmt(t: float) -> str:
    t = int(t)
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="视频或音频文件")
    ap.add_argument("--model", nargs="+", default=["paraformer-v2"], help="paraformer-v2 / fun-asr，可多个")
    ap.add_argument("--start", type=float)
    ap.add_argument("--end", type=float)
    ap.add_argument("--bucket", help="OSS bucket，默认读 .env 的 OSS_BUCKET")
    ap.add_argument("--keep", action="store_true", help="识别后保留 OSS 上的临时音频")
    ap.add_argument("--out", default=str(ROOT / "output" / "asr_trials"))
    args = ap.parse_args()

    load_env()
    key = os.environ.get("DASHSCOPE_API_KEY") or sys.exit("没有找到 DASHSCOPE_API_KEY")
    bucket = args.bucket or os.environ.get("OSS_BUCKET") or sys.exit("请用 --bucket 指定 OSS bucket，或在 .env 填 OSS_BUCKET")
    host = os.environ.get("DASHSCOPE_API_HOST", "https://dashscope.aliyuncs.com").rstrip("/")
    run_dir = Path(args.out) / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    audio = run_dir / "audio.mp3"
    dur = extract_audio(Path(args.file), audio, args.start, args.end)
    print(f"音频 {dur:.0f} 秒，{audio.stat().st_size / 1e6:.1f} MB")
    obj = f"asr-tmp/{uuid.uuid4().hex}.mp3"
    url = upload_and_sign(audio, bucket, obj)
    print("已上传到 OSS 并生成 6 小时有效的签名链接")

    try:
        for model in args.model:
            print(f"\n== {model} ==")
            result, secs = transcribe(host, key, model, url)
            sents, words, chars = summarize(result)
            (run_dir / f"{model}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            lines = [f"[{fmt(s['begin'])}–{fmt(s['end'])}] {s['text']}" for s in sents]
            (run_dir / f"{model}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
            price = PRICE_PER_SEC.get(model)
            print(f"用时 {secs:.0f} 秒；{len(sents)} 句、{chars} 字、词级时间戳 {words} 个"
                  + (f"；本次约 ¥{dur * price:.4f}，4 小时直播约 ¥{4 * 3600 * price:.2f}" if price else ""))
            for l in lines[:15]:
                print("  " + l)
            if len(lines) > 15:
                print(f"  …共 {len(lines)} 句，完整内容见 {run_dir / (model + '.txt')}")
    finally:
        if not args.keep:
            subprocess.run([os.environ.get("RCLONE", "rclone"), "deletefile", f"oss:{bucket}/{obj}"],
                           capture_output=True)
    print(f"\n结果保存在 {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
