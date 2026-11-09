"""语音转写（技术方案“模块一 预处理”）：阿里云百炼录音文件识别，不需要 GPU。

流程：录像抽成 16k 单声道 MP3 → 走内网传到 OSS → 用公网 endpoint 生成签名链接 → 提交异步识别任务
（一场的多个分段放在同一个任务里）→ 轮询 → 下载结果 → 按分段偏移拼成整场时间轴 → 删除 OSS 临时文件。

整场转写保存为 output/<场次>/transcript.json：
  {"model":..., "duration": 秒, "cost": 元, "segments": [...],
   "sentences": [{"t0": 秒, "t1": 秒, "text": "...", "words": [[t0, t1, "词"], ...]}, ...]}
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .overrides import load_overrides

PRICE_PER_SEC = {"paraformer-v2": 0.00008, "fun-asr": 0.00022}   # 元/秒，北京地域官方单价（2026-11 查询）
DEFAULT_HOST = "https://dashscope.aliyuncs.com"


class AsrError(RuntimeError):
    pass


def rclone() -> str:
    return os.environ.get("RCLONE", "rclone")


def extract_audio(src: Path, dst: Path, start: float | None = None, end: float | None = None) -> float:
    """抽成 16kHz 单声道 32kbps MP3（约 0.24 MB/分钟），返回时长秒。"""
    if not shutil.which("ffmpeg"):
        raise AsrError("找不到 ffmpeg")
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y"]
    if start is not None:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(src)]
    if end is not None:
        cmd += ["-t", str(end - (start or 0))]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "32k", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise AsrError(f"抽音频失败：{r.stderr.strip()[-300:]}")
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1",
                        str(dst)], capture_output=True, text=True)
    try:
        return float(p.stdout.strip())
    except ValueError:
        return 0.0


def public_endpoint() -> str:
    """百炼的服务器访问不到 OSS 内网地址，签名链接必须用公网 endpoint。"""
    ep = os.environ.get("OSS_ENDPOINT_PUBLIC") or os.environ.get("OSS_ENDPOINT_INTERNAL") or "oss-cn-beijing.aliyuncs.com"
    return ep.replace("-internal", "")


def upload_and_sign(local: Path, bucket: str, key: str, hours: int = 6) -> str:
    r = subprocess.run([rclone(), "copyto", str(local), f"oss:{bucket}/{key}"], capture_output=True, text=True)
    if r.returncode != 0:
        raise AsrError(f"上传 OSS 失败：{r.stderr.strip()[-300:]}")
    # 连接串语法临时覆盖 endpoint，不改 rclone 配置文件
    r = subprocess.run([rclone(), "link", "--expire", f"{hours}h", f"oss,endpoint={public_endpoint()}:{bucket}/{key}"],
                       capture_output=True, text=True)
    url = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    if r.returncode != 0 or not url.startswith("http"):
        raise AsrError(f"生成签名链接失败：{(r.stderr or r.stdout).strip()[-300:]}")
    return url


def delete_object(bucket: str, key: str) -> None:
    subprocess.run([rclone(), "deletefile", f"oss:{bucket}/{key}"], capture_output=True)


def _post(url: str, key: str, body: dict | None = None, async_: bool = False) -> dict:
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if async_:
        headers["X-DashScope-Async"] = "enable"
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise AsrError(f"接口返回错误 {e.code}：{e.read().decode(errors='replace')[:500]}") from None


def build_params(model: str, asr_cfg: dict | None = None) -> dict:
    c = asr_cfg or {}
    p: dict = {"timestamp_alignment_enabled": True}
    if model.startswith("paraformer"):
        p["language_hints"] = [str(x) for x in c.get("language_hints") or ["zh", "en", "ja"]]
    vid, _ = resolve_vocabulary(model, c)
    if vid and not c.get("no_hotwords"):
        p["vocabulary_id"] = vid
    return p


def transcribe_urls(urls: list[str], model: str, key: str, host: str | None = None, params: dict | None = None,
                    timeout: int = 3600, poll: float = 3.0, log=print) -> list[dict | None]:
    """一个任务识别多个文件（最多 100 个），按 urls 顺序返回每个文件的识别结果（失败为 None）。"""
    host = (host or os.environ.get("DASHSCOPE_API_HOST") or DEFAULT_HOST).rstrip("/")
    body = {"model": model, "input": {"file_urls": urls}, "parameters": params or build_params(model)}
    sub = _post(f"{host}/api/v1/services/audio/asr/transcription", key, body, async_=True)
    task = sub.get("output", {}).get("task_id")
    if not task:
        raise AsrError(f"提交任务失败：{json.dumps(sub, ensure_ascii=False)[:300]}")
    t0, last = time.time(), ""
    while True:
        time.sleep(poll)
        st = _post(f"{host}/api/v1/tasks/{task}", key)
        status = st.get("output", {}).get("task_status")
        if status in ("SUCCEEDED", "FAILED") and st["output"].get("results"):
            break
        if status not in ("PENDING", "RUNNING"):
            raise AsrError(f"识别失败：{json.dumps(st, ensure_ascii=False)[:500]}")
        if status != last:
            log(f"  任务 {status.lower()}…")
            last = status
        if time.time() - t0 > timeout:
            raise AsrError("识别超时")
    by_url: dict[str, dict] = {}
    results = st["output"]["results"]
    for i, r in enumerate(results):
        u = r.get("file_url") or (urls[i] if i < len(urls) else "")
        by_url[u] = r
    out: list[dict | None] = []
    for i, u in enumerate(urls):
        r = by_url.get(u) or (results[i] if i < len(results) else {})
        if r.get("subtask_status") != "SUCCEEDED":
            log(f"  第 {i + 1} 个文件识别失败：{r.get('code', '')} {r.get('message', '')}".rstrip())
            out.append(None)
            continue
        with urllib.request.urlopen(r["transcription_url"], timeout=120) as f:
            out.append(json.loads(f.read().decode()))
    return out


def sentences_from_result(result: dict, offset: float = 0.0) -> list[dict]:
    out = []
    for tr in result.get("transcripts", []):
        for s in tr.get("sentences", []):
            text = (s.get("text") or "").strip()
            if not text:
                continue
            words = [[round(w["begin_time"] / 1000 + offset, 2), round(w["end_time"] / 1000 + offset, 2),
                      (w.get("text") or "") + (w.get("punctuation") or "")]
                     for w in s.get("words", []) if "begin_time" in w and "end_time" in w]
            out.append({"t0": round(s["begin_time"] / 1000 + offset, 2), "t1": round(s["end_time"] / 1000 + offset, 2),
                        "text": text, "words": words})
    return out


def estimate_cost(model: str, seconds: float) -> float | None:
    p = PRICE_PER_SEC.get(model)
    return round(seconds * p, 4) if p else None


def transcribe_files(audio_files: list[Path], offsets: list[float], model: str, key: str, bucket: str,
                     asr_cfg: dict | None = None, keep_remote: bool = False, log=print) -> tuple[list[dict], list[bool]]:
    """上传 → 识别 → 拼接，返回 (整场句子列表, 每个文件是否成功)。"""
    keys = [f"asr-tmp/{uuid.uuid4().hex}.mp3" for _ in audio_files]
    try:
        urls = []
        for f, k in zip(audio_files, keys):
            urls.append(upload_and_sign(f, bucket, k))
        log(f"  已上传 {len(urls)} 个音频，提交识别（{model}）")
        results = transcribe_urls(urls, model, key, params=build_params(model, asr_cfg), log=log)
    finally:
        if not keep_remote:
            for k in keys:
                delete_object(bucket, k)
    sents: list[dict] = []
    for res, off in zip(results, offsets):
        if res:
            sents += sentences_from_result(res, off)
    sents.sort(key=lambda s: s["t0"])
    return sents, [r is not None for r in results]


def load_transcript(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def speech_between(sentences: list[dict], start: float, end: float) -> list[dict]:
    """与 [start, end] 有重叠的句子。"""
    return [s for s in sentences if s["t1"] > start and s["t0"] < end]


def format_speech(sentences: list[dict], start: float, limit: int = 1200) -> str:
    """给大模型 / 人看的格式：每句前面标相对片段起点的秒数。"""
    parts, n = [], 0
    for s in sentences:
        line = f"[{max(0, s['t0'] - start):.0f}s] {s['text']}"
        n += len(line)
        if n > limit:
            parts.append("…")
            break
        parts.append(line)
    return "\n".join(parts)


# ---------------------------------------------------------------- 热词表（百炼定制热词，免费）
VOCAB_MODEL = "speech-biasing"


def vocab_call(key: str, action: str, host: str | None = None, **inp) -> dict:
    """定制热词接口：create_vocabulary / list_vocabulary / query_vocabulary / update_vocabulary / delete_vocabulary。"""
    host = (host or os.environ.get("DASHSCOPE_API_HOST") or DEFAULT_HOST).rstrip("/")
    body = {"model": VOCAB_MODEL, "input": {"action": action, **inp}}
    return _post(f"{host}/api/v1/services/audio/asr/customization", key, body)


def _valid_hotword(text: str) -> bool:
    if not text or text.isdigit() or len(text) < 2:      # 纯数字、单字容易误伤其他词
        return False
    if text.isascii():
        return len(text.split()) <= 7                    # 官方限制：纯英文最多 7 个空格分隔的词
    return len(text) <= 15                               # 含中文最多 15 个字


def build_hotwords(streamer_cfg: dict) -> list[dict]:
    """热词 = asr.hotwords 里手写的 + 主播资料（昵称、粉丝名、观众称呼、世界观、口头禅、常见话题、专属梗）
    + 后台加的（config/admin_overrides.json）。"""
    lore = streamer_cfg.get("lore") or {}
    asr = streamer_cfg.get("asr") or {}
    weight = int(asr.get("hotword_weight", 4))
    words: list[tuple[str, int]] = []
    for item in asr.get("hotwords") or []:
        if isinstance(item, dict):
            words.append((str(item.get("text", "")), int(item.get("weight", weight))))
        else:
            words.append((str(item), weight))
    for k in ("nicknames", "world", "catchphrases", "recurring_topics"):
        words += [(str(w), weight) for w in lore.get(k) or []]
    words += [(str(lore[k]), weight) for k in ("fan_name", "audience_call") if lore.get(k)]
    words += [(str(w), weight) for w in streamer_cfg.get("lore_words") or []]
    words += [(str(w), weight) for w in load_overrides().get("hotwords") or [] if isinstance(w, str)]   # 后台加的
    seen, out = set(), []
    for text, w in words:
        text = text.strip()
        if text.lower() in seen or not _valid_hotword(text):
            continue
        seen.add(text.lower())
        out.append({"text": text, "weight": max(1, min(5, w))})
    return out


def resolve_vocabulary(model: str, asr_cfg: dict | None) -> tuple[str | None, str]:
    """本次识别用哪个热词表：配置里手写的 vocabulary_id 优先；否则用 hotwords.py 同步时记在 .env 里的，
    但热词表是按识别模型创建的，模型对不上就不用（换模型后重新运行 hotwords.py sync）。"""
    vid = str((asr_cfg or {}).get("vocabulary_id") or "").strip()
    if vid:
        return vid, "配置 asr.vocabulary_id"
    vid = os.environ.get("ASR_VOCABULARY_ID", "").strip()
    if not vid:
        return None, "未设置热词表"
    vm = os.environ.get("ASR_VOCABULARY_MODEL", "").strip()
    if vm and vm != model:
        return None, f"热词表是给 {vm} 建的，和 {model} 不匹配，未使用（运行 hotwords.py sync 重建）"
    return vid, "热词表"
