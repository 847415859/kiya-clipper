#!/usr/bin/env python3
"""百炼定制热词：把主播的名字、粉丝名、梗交给语音识别，减少“舰礼→件礼”“ki由米→key由米”这类错字。

热词功能免费（只按识别时长计费）；每个账号最多 50 个热词表，fun-asr 每表最多 2000 个词。

用法：
  python scripts/hotwords.py show      # 只看会提交哪些词，不调用接口
  python scripts/hotwords.py sync      # 创建或更新热词表，ID 自动写进仓库根目录 .env
  python scripts/hotwords.py list      # 账号下已有的热词表
  python scripts/hotwords.py delete    # 删除 .env 里记录的热词表

词从哪来：config/kiya.yaml 的主播资料（lore：昵称、粉丝名、观众称呼、世界观、口头禅、常见话题）、
lore_words，加上 asr.hotwords 里手写的。看 transcript.txt 发现总认错的词，加到 asr.hotwords 再 sync。
热词表跟识别模型绑定（asr.model）；换了模型要重新 sync，脚本会自动删旧建新。
sync 之后 transcribe.py / try_asr.py 自动使用；try_asr.py --no-hotwords 可以对比有无热词的效果。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from clipper.asr import AsrError, build_hotwords, vocab_call  # noqa: E402


def env_file() -> Path:
    return Path(os.environ.get("KIYA_ENV_FILE") or REPO / ".env")


def load_env() -> None:
    p = env_file()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
            if m and m.group(1) not in os.environ and m.group(2):
                os.environ[m.group(1)] = m.group(2).strip('"').strip("'")


def set_env(updates: dict[str, str]) -> None:
    """改写 .env 里的几行（没有就追加），其他内容原样保留。"""
    p = env_file()
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    todo = dict(updates)
    for i, line in enumerate(lines):
        m = re.match(r"\s*([A-Z0-9_]+)\s*=", line)
        if m and m.group(1) in todo:
            lines[i] = f"{m.group(1)}={todo.pop(m.group(1))}"
    lines += [f"{k}={v}" for k, v in todo.items()]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    for k, v in updates.items():
        os.environ[k] = v


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["show", "sync", "list", "delete"])
    ap.add_argument("--config", default=str(REPO / "config" / "kiya.yaml"))
    args = ap.parse_args()

    load_env()
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    asr = cfg.get("asr") or {}
    model = asr.get("model") or "fun-asr"
    prefix = str(asr.get("hotword_prefix") or "kiya")
    if not re.fullmatch(r"[a-z0-9]{1,10}", prefix):
        print(f"asr.hotword_prefix 只能是 1–10 位小写字母或数字：{prefix!r}", file=sys.stderr)
        return 1
    words = build_hotwords(cfg)
    limit = 2000 if model.startswith("fun-asr") else 500

    if args.action == "show":
        print(f"识别模型 {model}，共 {len(words)} 个热词（上限 {limit}）：")
        print("  " + "、".join(f"{w['text']}" + (f"({w['weight']})" if w["weight"] != 4 else "") for w in words))
        return 0

    key = os.environ.get("DASHSCOPE_API_KEY")
    if not key:
        print("没有找到 DASHSCOPE_API_KEY（填在仓库根目录 .env）", file=sys.stderr)
        return 1
    vid = os.environ.get("ASR_VOCABULARY_ID", "").strip()
    try:
        if args.action == "list":
            r = vocab_call(key, "list_vocabulary", prefix=prefix, page_index=0, page_size=50)
            items = r.get("output", {}).get("vocabulary_list", [])
            print(f"前缀 {prefix} 的热词表 {len(items)} 个：")
            for it in items:
                mark = " ← 当前使用" if it.get("vocabulary_id") == vid else ""
                print(f"  {it.get('vocabulary_id')}  {it.get('status')}  更新于 {it.get('gmt_modified')}{mark}")
            return 0

        if args.action == "delete":
            if not vid:
                print(".env 里没有 ASR_VOCABULARY_ID，没有可删除的")
                return 0
            vocab_call(key, "delete_vocabulary", vocabulary_id=vid)
            set_env({"ASR_VOCABULARY_ID": "", "ASR_VOCABULARY_MODEL": ""})
            print(f"已删除热词表 {vid}")
            return 0

        # sync
        if not words:
            print("没有可提交的热词（检查 config/kiya.yaml 的 lore 和 asr.hotwords）", file=sys.stderr)
            return 1
        if len(words) > limit:
            print(f"热词 {len(words)} 个，超过 {model} 的上限 {limit}，只提交前 {limit} 个")
            words = words[:limit]
        if vid:
            try:
                info = vocab_call(key, "query_vocabulary", vocabulary_id=vid).get("output", {})
            except AsrError:
                info = {}
            if info.get("target_model") == model:
                vocab_call(key, "update_vocabulary", vocabulary_id=vid, vocabulary=words)
                set_env({"ASR_VOCABULARY_MODEL": model})
                print(f"已更新热词表 {vid}：{len(words)} 个词（模型 {model}）")
                return 0
            if info:
                print(f"旧热词表是给 {info.get('target_model')} 建的，当前模型 {model}，删掉重建")
                vocab_call(key, "delete_vocabulary", vocabulary_id=vid)
            else:
                print(f"找不到旧热词表 {vid}，重新创建")
        r = vocab_call(key, "create_vocabulary", target_model=model, prefix=prefix, vocabulary=words)
        new = r.get("output", {}).get("vocabulary_id")
        if not new:
            print(f"创建失败：{r}", file=sys.stderr)
            return 1
        set_env({"ASR_VOCABULARY_ID": new, "ASR_VOCABULARY_MODEL": model})
        print(f"已创建热词表 {new}：{len(words)} 个词（模型 {model}），ID 已写入 {env_file()}")
        print("之后的 transcribe.py / try_asr.py 会自动使用")
        return 0
    except AsrError as e:
        print(f"失败：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
