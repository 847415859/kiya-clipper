"""大模型调用层（技术方案“模块二 精排”的基础）：统一接口、可切换模型、落盘缓存、失败重试、pydantic 校验。

用法：
    client = LLMClient()                                    # 模型 / Key / 地址读环境变量（.env）
    r = client.chat_json(messages, RerankOut, tag="rerank")  # r.data 是校验过的 pydantic 对象，失败为 None

- 接口：OpenAI 兼容的 /chat/completions（阿里云百炼 compatible-mode），换模型只改 DASHSCOPE_MODEL 或传 model。
- 缓存：同样的模型 + 消息（图片按内容哈希）+ 参数，直接读 output/_llm_cache/ 里的结果，调提示词时重跑不重复花钱。
- 重试：网络错误、429、5xx 最多 3 次（1、2、4 秒退避）；输出不是合法 JSON 或不符合结构时，
  把错误告诉模型再要一次，还不行就放弃（data=None，error 写原因）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from .env import REPO, load_env

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_CACHE = REPO / "worker" / "output" / "_llm_cache"


def image_part(path: str | Path) -> dict:
    data = Path(path).read_bytes()
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(data).decode()}}


def text_part(text: str) -> dict:
    return {"type": "text", "text": text}


def extract_json(text: str) -> Any:
    """从模型输出里取 JSON（容忍 ```json 代码块和前后多余的话）。"""
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    start = min([i for i in (t.find("{"), t.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("输出里没有 JSON")
    end = max(t.rfind("}"), t.rfind("]"))
    return json.loads(t[start:end + 1])


@dataclass
class LLMResult:
    data: BaseModel | None
    raw: str = ""
    usage: dict = field(default_factory=dict)
    cached: bool = False
    error: str | None = None
    latency: float = 0.0
    attempts: int = 0


class LLMClient:
    def __init__(self, model: str | None = None, base_url: str | None = None, api_key: str | None = None,
                 cache_dir: str | Path | None = None, timeout: int = 120, temperature: float = 0.3,
                 max_retries: int = 3):
        load_env()
        self.model = model or os.environ.get("DASHSCOPE_MODEL") or "qwen3-vl-plus"
        self.base_url = (base_url or os.environ.get("DASHSCOPE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key or os.environ.get("DASHSCOPE_API_KEY") or ""
        self.cache_dir = Path(cache_dir or os.environ.get("KIYA_LLM_CACHE") or DEFAULT_CACHE)
        self.timeout = timeout
        self.temperature = temperature
        self.max_retries = max_retries
        self._lock = threading.Lock()
        self.total = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "cached": 0}

    # ---------------------------------------------------------------- 缓存
    def _key(self, messages: list, extra: dict) -> str:
        def strip(m):
            if isinstance(m, dict):
                if m.get("type") == "image_url":
                    url = m["image_url"]["url"]
                    return {"img": hashlib.sha256(url.encode()).hexdigest()}
                return {k: strip(v) for k, v in m.items()}
            if isinstance(m, list):
                return [strip(x) for x in m]
            return m
        blob = json.dumps({"model": self.model, "t": self.temperature, "m": strip(messages), "x": extra},
                          ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:40]

    def _cache_get(self, key: str) -> dict | None:
        f = self.cache_dir / f"{key}.json"
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _cache_put(self, key: str, value: dict) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_dir / f".{key}.tmp"
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.cache_dir / f"{key}.json")

    # ---------------------------------------------------------------- 请求
    def _post(self, messages: list) -> tuple[str, dict]:
        if not self.api_key:
            raise RuntimeError("没有 DASHSCOPE_API_KEY（填在仓库根目录 .env）")
        body = json.dumps({"model": self.model, "messages": messages, "temperature": self.temperature}).encode()
        last = None
        for i in range(self.max_retries):
            req = urllib.request.Request(self.base_url + "/chat/completions", data=body, method="POST", headers={
                "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.loads(r.read().decode())
                content = data["choices"][0]["message"]["content"]
                if isinstance(content, list):
                    content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
                return content or "", data.get("usage") or {}
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:300]
                last = f"HTTP {e.code}：{detail}"
                if e.code not in (429, 500, 502, 503, 504):
                    raise RuntimeError(last) from None      # 400 / 401 / 404 重试也没用
            except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError) as e:
                last = f"{type(e).__name__}：{e}"
            time.sleep(2 ** i)
        raise RuntimeError(f"重试 {self.max_retries} 次仍失败：{last}")

    def chat_json(self, messages: list, schema: type[BaseModel], tag: str = "", extra: dict | None = None) -> LLMResult:
        """请求并按 schema 校验；输出不合法时带着错误重试一次。tag / extra 只参与缓存键（例如提示词版本）。"""
        extra = dict(extra or {}, tag=tag, schema=schema.__name__)
        key = self._key(messages, extra)
        hit = self._cache_get(key)
        if hit is not None:
            try:
                data = schema.model_validate(hit["data"])
                with self._lock:
                    self.total["cached"] += 1
                return LLMResult(data=data, raw=hit.get("raw", ""), usage=hit.get("usage", {}), cached=True)
            except (ValidationError, KeyError, TypeError):
                pass                                          # 缓存里的旧结构不合法：重新请求
        t0 = time.time()
        msgs = list(messages)
        usage_total: dict = {}
        raw, err = "", None
        for attempt in (1, 2):
            try:
                raw, usage = self._post(msgs)
            except RuntimeError as e:
                return LLMResult(data=None, error=str(e), latency=time.time() - t0, attempts=attempt)
            for k, v in usage.items():
                if isinstance(v, (int, float)):
                    usage_total[k] = usage_total.get(k, 0) + v
            with self._lock:
                self.total["calls"] += 1
                self.total["prompt_tokens"] += int(usage.get("prompt_tokens", 0))
                self.total["completion_tokens"] += int(usage.get("completion_tokens", 0))
            try:
                data = schema.model_validate(extract_json(raw))
                self._cache_put(key, {"data": data.model_dump(), "raw": raw, "usage": usage_total,
                                      "model": self.model, "at": time.strftime("%F %T")})
                return LLMResult(data=data, raw=raw, usage=usage_total, latency=time.time() - t0, attempts=attempt)
            except (ValueError, ValidationError) as e:
                err = str(e).splitlines()[0][:300] if str(e) else type(e).__name__
                msgs = msgs + [{"role": "assistant", "content": raw},
                               {"role": "user", "content": f"上面的输出不合法：{err}。请只输出一个符合要求的 JSON 对象，不要其他文字。"}]
        return LLMResult(data=None, raw=raw, usage=usage_total, error=f"输出不合法：{err}",
                         latency=time.time() - t0, attempts=2)

    def cost(self, price_in: float | None, price_out: float | None) -> float | None:
        """按单价（元 / 百万 tokens）估算本次累计费用。"""
        if price_in is None or price_out is None:
            return None
        return self.total["prompt_tokens"] / 1e6 * price_in + self.total["completion_tokens"] / 1e6 * price_out


def load_prompt(name: str) -> tuple[str, str, str]:
    """prompts/<name>.md → (版本号, system, user 模板)。文件格式：首行 `version: xxx`，`=== system ===` / `=== user ===` 分段。"""
    text = (REPO / "prompts" / f"{name}.md").read_text(encoding="utf-8")
    m = re.match(r"\s*version:\s*(\S+)", text)
    version = m.group(1) if m else name
    sys_part = re.search(r"=== system ===\s*(.*?)\s*=== user ===", text, re.S)
    user_part = re.search(r"=== user ===\s*(.*)$", text, re.S)
    if not sys_part or not user_part:
        raise ValueError(f"提示词 {name} 格式不对：需要 === system === 和 === user === 两段")
    return version, sys_part.group(1).strip(), user_part.group(1).strip()


def fill(template: str, **values) -> str:
    """把模板里的 {{名字}} 换成值（不用 str.format，模板里的 JSON 花括号不用转义）。"""
    def rep(m):
        k = m.group(1)
        if k not in values:
            raise KeyError(f"提示词模板缺少变量 {k}")
        return str(values[k])
    return re.sub(r"\{\{(\w+)\}\}", rep, template)
