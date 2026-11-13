"""Seedream 图像生成适配器。

Seedream 属于字节跳动火山引擎方舟，不属于阿里云百炼。默认使用方舟的
OpenAI 风格图像生成接口，地址、模型和字段可通过环境变量覆盖。
只负责生成和下载图片，不把密钥写入日志或发布包。
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .env import load_env

DEFAULT_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/images/generations"
DEFAULT_MODEL = "seedream-5.5"


class SeedreamError(RuntimeError):
    pass


def _json_request(url: str, key: str, body: dict[str, Any] | None = None, method: str = "POST") -> dict[str, Any]:
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as e:
        detail = ""
        if isinstance(e, urllib.error.HTTPError):
            try:
                detail = e.read().decode("utf-8", "replace")[:500]
            except OSError:
                pass
        raise SeedreamError(f"Seedream 请求失败：{e}{' ' + detail if detail else ''}") from e


def _download(url: str, out: Path, key: str = "") -> None:
    if url.startswith("data:") and "," in url:
        try:
            data = base64.b64decode(url.split(",", 1)[1])
        except (ValueError, TypeError) as e:
            raise SeedreamError("Seedream 返回的 data 图片无法解码") from e
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
        raise SeedreamError(f"Seedream 图片下载失败：{e}") from e
    if len(data) < 100:
        raise SeedreamError("Seedream 返回的图片为空")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)


def _result_url(obj: dict[str, Any]) -> str | None:
    data = obj.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            return first.get("url") or first.get("image_url")
        if isinstance(first, str):
            return first
    output = obj.get("output") or {}
    results = output.get("results") if isinstance(output, dict) else None
    if isinstance(results, list) and results:
        first = results[0]
        if isinstance(first, dict):
            return first.get("url") or first.get("image_url")
        if isinstance(first, str):
            return first
    if isinstance(output, dict):
        return output.get("url") or output.get("image_url")
    return None


def generate(prompt: str, reference: Path, out: Path, model: str | None = None,
             endpoint: str | None = None, api_key: str | None = None,
             size: str = "1920*1200", timeout: int = 180) -> dict[str, Any]:
    load_env()
    key = (api_key or os.environ.get("SEEDREAM_API_KEY") or os.environ.get("ARK_API_KEY")
           or os.environ.get("VOLCENGINE_ARK_API_KEY") or "")
    if not key:
        raise SeedreamError("没有 Seedream API Key：填写 SEEDREAM_API_KEY 或 ARK_API_KEY")
    if not reference.exists():
        raise SeedreamError(f"参考帧不存在：{reference}")
    ep = endpoint or os.environ.get("SEEDREAM_ENDPOINT") or DEFAULT_ENDPOINT
    mdl = model or os.environ.get("SEEDREAM_MODEL") or DEFAULT_MODEL
    b64 = base64.b64encode(reference.read_bytes()).decode()
    image = f"data:image/jpeg;base64,{b64}"
    body = {"model": mdl, "prompt": prompt, "image": image, "size": size,
            "response_format": "url", "watermark": False,
            "sequential_image_generation": "disabled"}
    first = _json_request(ep, key, body)
    task_id = (first.get("output") or {}).get("task_id") or first.get("task_id")
    result = first
    if task_id:
        task_url = os.environ.get("SEEDREAM_TASK_ENDPOINT") or "https://ark.cn-beijing.volces.com/api/v3/images/tasks/"
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(3)
            result = _json_request(task_url.rstrip("/") + "/" + str(task_id), key, None, "GET")
            status = (result.get("output") or {}).get("task_status") or result.get("task_status")
            if status in ("SUCCEEDED", "FAILED", "CANCELED"):
                break
        status = (result.get("output") or {}).get("task_status") or result.get("task_status")
        if status != "SUCCEEDED":
            raise SeedreamError(f"Seedream 任务未成功：{status or '超时'}")
    url = _result_url(result)
    if not url:
        raise SeedreamError("Seedream 返回中没有图片地址")
    _download(url, out, key)
    return {"model": mdl, "prompt": prompt, "endpoint": ep, "task_id": task_id, "source_url": url,
            "file": out.name, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
