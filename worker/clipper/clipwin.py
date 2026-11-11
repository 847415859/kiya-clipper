"""审核片段（clips/NN_*.mp4）的实际剪切范围。

候选的 clip_start / clip_end 是粗筛定的固定窗口（峰值前后各 30 秒），它是候选的“身份”：
标注对齐、评测重叠、精排记忆匹配都靠它，所以**不改**。

有精排结果时，cut.py 按精排修正后的起止剪审核片段（审核看到的就是将要成片的长度），
实际剪切范围记在 clips/windows.json：{"01": {"start": 整场秒, "end": 整场秒, "refined": true}}。
审核页的播放起点、发布包的封面抽帧都读它；没有这个文件（旧场次）或没有这个序号，就是候选窗口本身。
"""
from __future__ import annotations

import json
from pathlib import Path

WINDOWS_FILE = "windows.json"


def cut_window(cand: dict, refined: dict | None) -> tuple[float, float, bool]:
    """审核片段的剪切范围 (起, 止, 是否按精排)。精排的起止必须包含高峰时间点，否则退回候选窗口。"""
    a, b = float(cand["clip_start"]), float(cand["clip_end"])
    if isinstance(refined, dict) and "start" in refined and "end" in refined:
        try:
            rs, re_ = max(0.0, float(refined["start"])), float(refined["end"])
        except (TypeError, ValueError):
            return a, b, False
        t = float(cand.get("t", (a + b) / 2))
        if re_ - rs >= 5 and rs <= t <= re_:
            return rs, re_, True
    return a, b, False


def read_windows(sess: Path) -> dict[str, dict]:
    try:
        raw = json.loads((Path(sess) / "clips" / WINDOWS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def write_windows(sess: Path, updates: dict[str, dict]) -> None:
    """合并写入（--ranks 只剪几个时，不丢其他序号的记录）。"""
    cur = read_windows(sess)
    cur.update(updates)
    d = Path(sess) / "clips"
    d.mkdir(exist_ok=True)
    tmp = d / (WINDOWS_FILE + ".tmp")
    tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(d / WINDOWS_FILE)


def clip_origin(windows: dict, cand: dict) -> float:
    """审核片段第 0 秒对应整场的第几秒。"""
    w = windows.get(f"{int(cand['rank']):02d}")
    if isinstance(w, dict) and isinstance(w.get("start"), (int, float)):
        return float(w["start"])
    return float(cand["clip_start"])
