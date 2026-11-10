#!/usr/bin/env python3
"""主播周报：本周最火的梗 TOP5、高光场面、哪类内容最容易出效果、观众高频词（词云）。

用法：
  python scripts/weekly_report.py                       # 截止今天的最近 7 天
  python scripts/weekly_report.py --end 2026-11-10      # 截止指定日期（含）
  python scripts/weekly_report.py --days 14             # 改统计天数（默认读 config 的 weekly.days）

结果写到 output/_reports/weekly-<截止日期>.json（后台页面用）和 .html（一键导出，发给主播的文档，只含适合主播看的内容）。
最后一行打印 JSON 摘要路径，后台读它。不联网、不调大模型。
数据来自各场的转写 / 弹幕（ngrams.json）、精排结果（rerank.json）、审核标注（只用来排除标了不有趣的片段）、
learn.py 生成的 memory.json（切片号播放表现）。禁区话题命中的内容不会出现在文档里。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from clipper.kcfg import load_cfg, read_decisions  # noqa: E402
from clipper.weekly import build_report, read_json, render_html  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(REPO / "config" / "kiya.yaml"))
    ap.add_argument("--out", default=str(ROOT / "output"))
    ap.add_argument("--reviews", default=str(REPO / "eval" / "reviews"))
    ap.add_argument("--end", help="截止日期 YYYY-MM-DD（含），默认今天")
    ap.add_argument("--days", type=int, help="统计天数，默认读 config 的 weekly.days（7）")
    args = ap.parse_args()

    cfg = load_cfg(Path(args.config))
    if args.days:
        cfg.setdefault("weekly", {})["days"] = args.days
    try:
        end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else date.today()
    except ValueError:
        print("日期格式应为 YYYY-MM-DD", file=sys.stderr)
        return 1
    out = Path(args.out)
    mem = read_json(out / "_memory" / "memory.json", {}) or {}
    dec = read_decisions()
    rep = build_report(out, cfg, end, Path(args.reviews), mem, set(dec.get("rejected_lore") or []))
    rdir = out / "_reports"
    rdir.mkdir(parents=True, exist_ok=True)
    stem = f"weekly-{end.isoformat()}"
    jp = rdir / f"{stem}.json"
    for p, text in ((jp, json.dumps(rep, ensure_ascii=False, indent=1)), (rdir / f"{stem}.html", render_html(rep))):
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(p)
    pub, inn = rep["public"], rep["internal"]
    print(f"{rep['period']['label']}：{pub['overview']['sessions']} 场，梗 {len(pub['memes'])} 个，高光 {len(pub['highlights'])} 个，"
          f"词云 {len(pub['words'])} 个词，类型统计{'已出' if pub['types']['enough'] else '先不出'}")
    for n in inn["notes"]:
        print(f"  提示：{n}")
    print(jp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
