#!/usr/bin/env python3
"""Read-only Bilibili query diagnostics; never print cookie values or upload anything."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clipper.bili import Bili, BiliError, memory_cookie_file
from clipper.env import load_env
from clipper.kcfg import load_cfg
from clipper.streamer import resolve_config


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config")
    ap.add_argument("--streamer")
    ap.add_argument("--cookie-file", help="显式指定 Netscape 或 biliup JSON 登录文件")
    ap.add_argument("--uid", help="可选，只测试该 UID 的投稿列表（最多 1 条）")
    ap.add_argument("--bvid", help="可选，只测试一条已知 BV 的公开数据")
    args = ap.parse_args()
    load_env()
    cfg = load_cfg(resolve_config(args.config, streamer=args.streamer))
    b = Bili(cookie_file=args.cookie_file or memory_cookie_file(cfg, args.streamer))
    print(f"查询 Cookie 文件：{b.cookie_file.resolve()}")
    print("文件状态：" + ("存在" if b.cookie_file.is_file() else "不存在"))
    names = {pair.split("=", 1)[0] for pair in b.cookie.split("; ") if pair}
    for key in ("SESSDATA", "DedeUserID", "bili_jct", "buvid3"):
        print(f"{key}：{'已读取' if key in names else '未读取'}")
    try:
        status = b.login_status()
        print("登录状态：" + (f"有效（UID {status['uid']}）" if status["logged_in"] else "未登录"))
    except BiliError as e:
        print(f"登录状态：无法确认；{e}")
    if args.uid:
        try:
            print(f"投稿列表：成功，读取 {len(b.uploads(args.uid, 1))} 条")
        except BiliError as e:
            print(f"投稿列表：失败；{e}")
    if args.bvid:
        try:
            row = b.stat(args.bvid)
            print("已知 BV：" + json.dumps({k: row.get(k) for k in ("bvid", "owner_mid", "view", "like")}, ensure_ascii=False))
        except BiliError as e:
            print(f"已知 BV：失败；{e}")


if __name__ == "__main__":
    main()
