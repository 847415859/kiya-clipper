#!/usr/bin/env python3
"""Calculate current editor timing and subtitles without encoding video."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clipper.edit import validate_edit, edit_window
from clipper.asr import load_transcript
from clipper.kcfg import load_cfg
from clipper.streamer import resolve_config
from clipper.subtitles import load_config, validate_style, get_style, lore_keywords
from clipper.render_plan import plan


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("candidates")
    ap.add_argument("--rank", type=int, required=True)
    ap.add_argument("--edit", required=True)
    args = ap.parse_args()
    folder = Path(args.candidates).parent
    data = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    candidate = next(c for c in data["candidates"] if c["rank"] == args.rank)
    edit = validate_edit(json.loads(Path(args.edit).read_text(encoding="utf-8")), candidate["clip_end"] - candidate["clip_start"])
    cfg = load_config()
    style = validate_style(edit["subtitles"]["style"]) if "style" in edit["subtitles"] else get_style(None, cfg)[1]
    path = resolve_config(None, folder)
    streamer = load_cfg(path) if path.exists() else {}
    keywords = sorted({*(lore_keywords(streamer) if style["highlight_lore"] else []), *style["highlight_words"]}, key=len, reverse=True)
    transcript = load_transcript(folder / "transcript.json") or {}
    result = plan(edit, transcript.get("sentences", []), edit_window(edit, candidate)[0], style, cfg.get("corrections") or {}, keywords)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, StopIteration) as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(1)
