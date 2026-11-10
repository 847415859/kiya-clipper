"""文本向量（梗库检索用）：百炼 text-embedding 的 OpenAI 兼容接口，结果按“模型 + 文本”落盘缓存。

梗库在几千条以内，向量放内存里用 numpy 算余弦相似度就够了，不需要向量数据库。
没有 DASHSCOPE_API_KEY、或接口失败时，调用方退回 lexical_scores（字面相似度），系统照样能用，只是匹配粗一些。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

from .env import REPO, load_env

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "text-embedding-v3"
DEFAULT_CACHE = REPO / "worker" / "output" / "_embed_cache"
BATCH = 10                    # 百炼 v3 一次最多 10 条


class EmbedError(RuntimeError):
    pass


class Embedder:
    def __init__(self, model: str | None = None, base_url: str | None = None, api_key: str | None = None,
                 cache_dir: str | Path | None = None, timeout: int = 60, max_retries: int = 3, dimensions: int | None = None):
        load_env()
        self.model = model or os.environ.get("DASHSCOPE_EMBED_MODEL") or DEFAULT_MODEL
        self.base_url = (base_url or os.environ.get("DASHSCOPE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key or os.environ.get("DASHSCOPE_API_KEY") or ""
        self.cache_dir = Path(cache_dir or os.environ.get("KIYA_EMBED_CACHE") or DEFAULT_CACHE) / self.model.replace("/", "_")
        self.timeout, self.max_retries, self.dimensions = timeout, max_retries, dimensions
        self.calls = 0

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _file(self, text: str) -> Path:
        return self.cache_dir / (hashlib.sha256(text.encode()).hexdigest()[:40] + ".json")

    def _post(self, texts: list[str]) -> list[list[float]]:
        body = {"model": self.model, "input": texts, "encoding_format": "float"}
        if self.dimensions:
            body["dimensions"] = self.dimensions
        last = None
        for i in range(self.max_retries):
            req = urllib.request.Request(self.base_url + "/embeddings", data=json.dumps(body).encode(), method="POST",
                                         headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.loads(r.read().decode())
                rows = sorted(data["data"], key=lambda x: x.get("index", 0))
                if len(rows) != len(texts):
                    raise EmbedError("返回条数对不上")
                return [x["embedding"] for x in rows]
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}：{e.read().decode(errors='replace')[:200]}"
                if e.code not in (429, 500, 502, 503, 504):
                    raise EmbedError(last) from None
            except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError, EmbedError) as e:
                last = f"{type(e).__name__}：{e}"
            time.sleep(2 ** i)
        raise EmbedError(f"重试 {self.max_retries} 次仍失败：{last}")

    def embed(self, texts: list[str]) -> np.ndarray:
        """返回 (n, d) 的单位向量；先读缓存，没有的按批请求。"""
        if not self.api_key:
            raise EmbedError("没有 DASHSCOPE_API_KEY（填在仓库根目录 .env）")
        vecs: dict[str, list[float]] = {}
        todo = []
        for t in dict.fromkeys(texts):
            try:
                vecs[t] = json.loads(self._file(t).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                todo.append(t)
        for i in range(0, len(todo), BATCH):
            chunk = todo[i:i + BATCH]
            self.calls += 1
            for t, v in zip(chunk, self._post(chunk)):
                vecs[t] = v
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                f = self._file(t)
                tmp = f.with_suffix(".tmp")
                tmp.write_text(json.dumps(v), encoding="utf-8")
                tmp.replace(f)
        m = np.array([vecs[t] for t in texts], dtype=np.float32)
        n = np.linalg.norm(m, axis=1, keepdims=True)
        return m / np.where(n == 0, 1, n)


def cosine_scores(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """query (d,) 与 matrix (n, d)（都是单位向量）的余弦相似度。"""
    return matrix @ query


def _grams(text: str, ns=(2, 3)) -> set[str]:
    import re
    t = re.sub(r"[^0-9A-Za-z一-鿿]+", "", text.lower())
    return {t[i:i + n] for n in ns for i in range(len(t) - n + 1)}


def lexical_scores(query: str, docs: list[str]) -> np.ndarray:
    """没有向量时的备用：字（2、3 字片段）重合度，0~1。只能抓到字面相同的情况。"""
    q = _grams(query)
    out = []
    for d in docs:
        g = _grams(d)
        out.append(len(q & g) / max(1, min(len(q), len(g))) if q and g else 0.0)
    return np.array(out, dtype=np.float32)
