#!/usr/bin/env python3
"""批量抓取公共梗图/表情包，灌入 assets/stickers/（配合后台「网络搜索」单张添加）。

用法：
  python scripts/fetch_stickers.py --all                     # 抓取所有情绪标签
  python scripts/fetch_stickers.py --emotion 破防 --limit 20  # 只抓一类
  python scripts/fetch_stickers.py --all --dry-run            # 只看会搜到什么、不下载

说明：
- 数据源：斗图吧（dbbqb）搜索接口；仅内部测试用，商用前换成授权素材。
- 下载的文件名强制加 net_ 前缀（net_情绪_关键词_序号），方便商用前清点版权未验证的图。
- 断点续传：目标文件已存在且 >1KB 就跳过，重跑不会重复下载。
- 下载后建议用 scripts/describe_stickers.py 让多模态模型自动写含义说明（没有说明的贴图 AI 编排不会用）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
STICKER_DIR = REPO / "assets" / "stickers"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# 每个情绪标签一组搜索词（按 VTuber 圈 / 切片常用贴图场景调）
EMOTIONS = {
    "破防": ["破防", "蚌埠住了", "绷不住了"],
    "惊讶": ["震惊", "离谱", "卧槽"],
    "问号": ["问号", "一脸懵逼", "黑人问号"],
    "大笑": ["大笑", "笑死", "哈哈哈"],
    "哭": ["哭", "泪目", "呜呜"],
    "得意": ["得意", "嚣张", "狂笑"],
    "无语": ["无语", "白眼", "汗"],
    "生气": ["生气", "愤怒", "急了"],
    "点赞": ["点赞", "牛", "666"],
    "心累": ["心累", "累了", "摆烂"],
}


def search(keyword: str, page: int = 1) -> list[str]:
    """调斗图吧搜索接口，返回图片直链列表。接口可能变化，失效时换站点重写本函数。"""
    q = urllib.parse.urlencode({"size": 60, "w": keyword, "page": page})
    req = urllib.request.Request(f"https://www.dbbqb.com/api/search/json?{q}",
                                 headers={"User-Agent": UA, "Referer": "https://www.dbbqb.com/"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode())
    return [f"https://image.dbbqb.com/{it['path']}" for it in data if it.get("path")]


def download(url: str, dst: Path) -> bool:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.dbbqb.com/"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r, open(dst, "wb") as f:
            f.write(r.read())
        return dst.stat().st_size > 1000
    except Exception:
        if dst.exists():
            dst.unlink()
        return False


def guess_ext(url: str) -> str:
    u = url.lower().split("?")[0]
    for ext in (".gif", ".png", ".jpg", ".jpeg", ".webp"):
        if u.endswith(ext):
            return ".gif" if ext == ".gif" else (".jpg" if ext in (".jpg", ".jpeg") else ext)
    return ".png"     # 猜错了也无妨：后端 StickerService 按文件头纠正


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--emotion", choices=list(EMOTIONS), help="只抓这一类（不填 = 全部）")
    ap.add_argument("--all", action="store_true", help="抓取全部情绪标签（等价于不填 --emotion）")
    ap.add_argument("--limit", type=int, default=20, help="每个搜索词最多下几张，默认 20")
    ap.add_argument("--dry-run", action="store_true", help="只列出会下载什么，不真下载")
    args = ap.parse_args()

    STICKER_DIR.mkdir(parents=True, exist_ok=True)
    targets = {args.emotion: EMOTIONS[args.emotion]} if args.emotion else EMOTIONS
    total_ok = total_skip = 0
    for emotion, keywords in targets.items():
        for kw in keywords:
            try:
                urls = search(kw)[: args.limit]
            except Exception as e:
                print(f"[{emotion}/{kw}] 搜索失败：{e}", file=sys.stderr)
                continue
            for i, u in enumerate(urls):
                dst = STICKER_DIR / f"net_{emotion}_{kw}_{i + 1:02d}{guess_ext(u)}"
                if dst.exists() and dst.stat().st_size > 1000:
                    total_skip += 1
                    continue
                if args.dry_run:
                    print(f"[dry] {dst.name}  <-  {u}")
                    continue
                ok = download(u, dst)
                total_ok += 1 if ok else 0
                print(f"{'✓' if ok else '✗'} {dst.name}")
                time.sleep(0.3)      # 别太快，避免被限流
    print(f"完成：新增 {total_ok} 张，跳过已存在 {total_skip} 张。目录 {STICKER_DIR}")
    print("下一步：python scripts/describe_stickers.py 给没有含义说明的贴图自动写描述（AI 编排才会用）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
