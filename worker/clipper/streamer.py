"""主播（streamer）抽象层：把「主播」从全局硬编码变成可配置实体。

多主播支持的核心约定（详见 docs/多主播支持.md）：

- 主播标识 streamer_id：小写字母开头，后可跟小写字母 / 数字 / `_` / `-`，最长 32 位（例如 kiya、kiya2）。
- 配置：config/streamers/<streamer_id>.yaml；找不到时回退 config/<streamer_id>.yaml（兼容旧路径）。
  首位主播 kiya 的配置仍在 config/kiya.yaml，靠这个回退继续工作，无需迁移。
- 数据：worker/output/<streamer_id>/<场次>/。场次目录的父目录（sess.parent）就是主播的输出目录，
  因此 _memory / _llm_cache / _jobs 等共享目录只要从 sess.parent 定位，就自动按主播隔离。
- 评测：eval/<streamer_id>/（reviews、annotations、results 按主播隔离）。
- 环境变量：KIYA_STREAMER 指定默认主播，不设就是 kiya。

向后兼容：旧的 output/<场次>/（没有主播层级）在 streamer_from_session_dir 里按默认主播处理，
所以升级前已有的数据不用搬。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_STREAMER = "kiya"
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def normalize(streamer_id: str) -> str:
    """校验并规范化主播标识；不合法抛 ValueError（路径安全和一致性）。"""
    s = (streamer_id or "").strip().lower()
    if not ID_RE.match(s):
        raise ValueError(f"主播标识不合法：{streamer_id!r}（1–32 位小写字母开头，可含小写字母 / 数字 / _ 和 -）")
    return s


def default_streamer() -> str:
    """当前主播：环境变量 KIYA_STREAMER 优先，否则 kiya。"""
    return normalize(os.environ.get("KIYA_STREAMER") or DEFAULT_STREAMER)


def streamer_from_args(streamer: str | None) -> str:
    """命令行 --streamer 优先，其次环境变量 KIYA_STREAMER，最后默认 kiya。"""
    return normalize(streamer) if streamer else default_streamer()


def streamer_from_session_dir(session_dir: Path) -> str:
    """从场次目录推导主播 ID。

    - 新结构 output/<streamer>/<场次> → 返回 <streamer>
    - 旧结构 output/<场次>（父目录名就是 output）→ 返回默认主播
    """
    parent = session_dir.parent
    if parent.name == "output":
        return default_streamer()
    try:
        return normalize(parent.name)
    except ValueError:
        return default_streamer()


# ---------------------------------------------------------------- 配置
def config_dir() -> Path:
    return REPO / "config" / "streamers"


def config_path(streamer_id: str) -> Path:
    """主播配置路径：config/streamers/<id>.yaml，找不到回退 config/<id>.yaml。"""
    sid = normalize(streamer_id)
    p = config_dir() / f"{sid}.yaml"
    if p.exists():
        return p
    legacy = REPO / "config" / f"{sid}.yaml"
    if legacy.exists():
        return legacy
    return p          # 都不存在：返回新路径，调用方读取时报错并给出提示


def template_path() -> Path:
    return config_dir() / "_template.yaml"


def resolve_config(explicit_config, session_dir=None, streamer=None) -> Path:
    """统一解析主播配置路径：显式 --config 优先，其次从场次目录推导主播，再 --streamer / 环境变量 / 默认。"""
    if explicit_config:
        return Path(explicit_config)
    sid = streamer_from_session_dir(session_dir) if session_dir is not None else streamer_from_args(streamer)
    return config_path(sid)


# ---------------------------------------------------------------- 数据 / 评测目录
def _is_legacy_output(base: Path) -> bool:
    """旧结构：worker/output 下直接是 <场次>/ 和 _memory/（没有主播这一层）。"""
    try:
        for d in base.iterdir():
            if not d.is_dir():
                continue
            if d.name == "_memory" or (d / "candidates.json").exists() or (d / "transcript.json").exists():
                return True
    except OSError:
        pass
    return False


def _is_legacy_eval(base: Path) -> bool:
    """旧结构：eval 下直接是 reviews/、annotations.csv、results.csv。"""
    return (base / "reviews").is_dir() or (base / "annotations.csv").exists() or (base / "results.csv").exists()


def output_root(streamer_id: str | None = None) -> Path:
    """主播的输出目录 worker/output/<id>。

    首位主播 kiya 兼容旧结构：output/kiya 还不存在、而 output/ 下直接放着旧的场次或 _memory 时，
    继续用 output/ 本身（数据不用搬；想迁移就把它们 mv 进 output/kiya/，之后自动走新结构）。
    """
    sid = normalize(streamer_id) if streamer_id else default_streamer()
    base = REPO / "worker" / "output"
    p = base / sid
    if sid == DEFAULT_STREAMER and not p.is_dir() and _is_legacy_output(base):
        return base
    return p


def session_dir(streamer_id: str, session_name: str) -> Path:
    return output_root(streamer_id) / session_name


def eval_root(streamer_id: str | None = None) -> Path:
    """主播的评测目录 eval/<id>；kiya 同样兼容旧的 eval/reviews 等（eval/kiya 不存在时）。"""
    sid = normalize(streamer_id) if streamer_id else default_streamer()
    base = REPO / "eval"
    p = base / sid
    if sid == DEFAULT_STREAMER and not p.is_dir() and _is_legacy_eval(base):
        return base
    return p


def reviews_dir(streamer_id: str | None = None) -> Path:
    return eval_root(streamer_id) / "reviews"


# ---------------------------------------------------------------- 主播专属运行时配置
def overrides_path(streamer_id: str | None = None) -> Path:
    """后台覆盖配置（热词 / 纠错词 / 默认字幕样式），按主播隔离在 output/<id>/admin_overrides.json。

    首位主播 kiya 兼容旧的 config/admin_overrides.json（旧文件存在时优先用旧路径，不用搬）。
    """
    sid = normalize(streamer_id) if streamer_id else default_streamer()
    p = output_root(sid) / "admin_overrides.json"
    if sid != DEFAULT_STREAMER or p.exists():
        return p
    legacy = REPO / "config" / "admin_overrides.json"
    return legacy if legacy.exists() else p


def decisions_path(streamer_id: str | None = None) -> Path:
    """后台精排记忆决定（梗转正 / 禁区 / 删梗 / 权重），按主播隔离在 output/<id>/memory_decisions.json。

    首位主播 kiya 兼容旧的 config/memory_decisions.json。
    """
    sid = normalize(streamer_id) if streamer_id else default_streamer()
    p = output_root(sid) / "memory_decisions.json"
    if sid != DEFAULT_STREAMER or p.exists():
        return p
    legacy = REPO / "config" / "memory_decisions.json"
    return legacy if legacy.exists() else p
