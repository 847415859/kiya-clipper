#!/usr/bin/env python3
"""试用阿里云百炼录音文件识别（Paraformer / Fun-ASR），评估阶段 2 的语音转写方案。

不需要 GPU：音频上传到你自己的 OSS，生成一个几小时后失效的签名链接交给百炼识别。
2 核 2G 的录制机就能跑；4 小时音频按官方单价约 ¥1.2（paraformer-v2）/ ¥3.2（fun-asr）。

用法：
  python scripts/try_asr.py /tmp/kiya_test.mp4                              # 整个文件，paraformer-v2
  python scripts/try_asr.py /tmp/kiya_test.mp4 --model fun-asr              # 换模型对比
  python scripts/try_asr.py /data/rec/xxx.flv --start 600 --end 900         # 录像里的一段
  python scripts/try_asr.py /tmp/kiya_test.mp4 --model paraformer-v2 fun-asr  # 两个模型一起跑，便于对比
  python scripts/try_asr.py /tmp/kiya_test.mp4 --no-hotwords                # 不用热词表，和默认结果对比

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
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from clipper.asr import (AsrError, build_params, delete_object, estimate_cost, extract_audio,  # noqa: E402
                         resolve_vocabulary, sentences_from_result, transcribe_urls, upload_and_sign)


def load_env() -> None:
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
            if m and m.group(1) not in os.environ and m.group(2):
                os.environ[m.group(1)] = m.group(2).strip('"').strip("'")


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
    ap.add_argument("--no-hotwords", action="store_true", help="不用热词表（和用热词表的结果对比）")
    ap.add_argument("--out", default=str(ROOT / "output" / "asr_trials"))
    args = ap.parse_args()

    load_env()
    key = os.environ.get("DASHSCOPE_API_KEY") or sys.exit("没有找到 DASHSCOPE_API_KEY")
    bucket = args.bucket or os.environ.get("OSS_BUCKET") or sys.exit("请用 --bucket 指定 OSS bucket，或在 .env 填 OSS_BUCKET")
    run_dir = Path(args.out) / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    try:
        audio = run_dir / "audio.mp3"
        dur = extract_audio(Path(args.file), audio, args.start, args.end)
        print(f"音频 {dur:.0f} 秒，{audio.stat().st_size / 1e6:.1f} MB")
        obj = f"asr-tmp/{uuid.uuid4().hex}.mp3"
        url = upload_and_sign(audio, bucket, obj)
        print("已上传到 OSS 并生成 6 小时有效的签名链接")
        try:
            for model in args.model:
                print(f"\n== {model} ==")
                t0 = time.time()
                asr_cfg = {"no_hotwords": args.no_hotwords}
                vid, note = resolve_vocabulary(model, asr_cfg)
                print("热词：" + ("不使用（--no-hotwords）" if args.no_hotwords else (f"{note} {vid}" if vid else note)))
                result = transcribe_urls([url], model, key, params=build_params(model, asr_cfg))[0]
                if result is None:
                    continue
                secs = time.time() - t0
                sents = sentences_from_result(result)
                chars = sum(len(s["text"]) for s in sents)
                words = sum(len(s["words"]) for s in sents)
                (run_dir / f"{model}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                lines = [f"[{fmt(s['t0'])}–{fmt(s['t1'])}] {s['text']}" for s in sents]
                (run_dir / f"{model}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
                cost, per4h = estimate_cost(model, dur), estimate_cost(model, 4 * 3600)
                print(f"用时 {secs:.0f} 秒；{len(sents)} 句、{chars} 字、词级时间戳 {words} 个"
                      + (f"；本次约 ¥{cost:.4f}，4 小时直播约 ¥{per4h:.2f}" if cost is not None else ""))
                for l in lines[:15]:
                    print("  " + l)
                if len(lines) > 15:
                    print(f"  …共 {len(lines)} 句，完整内容见 {run_dir / (model + '.txt')}")
        finally:
            if not args.keep:
                delete_object(bucket, obj)
    except AsrError as e:
        print(f"失败：{e}", file=sys.stderr)
        return 1
    print(f"\n结果保存在 {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
