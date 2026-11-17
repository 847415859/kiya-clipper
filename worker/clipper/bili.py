"""B站公开接口：某个 UP 主的投稿列表、单个视频的播放 / 点赞等数据。

只读公开数据，请求之间间隔 1 秒。投稿列表接口要 WBI 签名（B站网页端的标准参数签名），
查询支持 Netscape（含 HttpOnly）及 biliup JSON Cookie 文件；记忆更新优先显式查询配置，否则复用投稿登录文件。
完播率、涨粉数不是公开数据，只能在创作中心看，后台可以手动填。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.bilibili.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
MIXIN = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39,
         12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63,
         57, 62, 11, 36, 20, 34, 44, 52]


class BiliError(RuntimeError):
    pass


def mixin_key(img_key: str, sub_key: str) -> str:
    s = img_key + sub_key
    return "".join(s[i] for i in MIXIN if i < len(s))[:32]


def sign(params: dict, img_key: str, sub_key: str, now: int | None = None) -> dict:
    """WBI 签名：加 wts、按键排序、去掉 !'()* 后算 w_rid。"""
    p = dict(params, wts=int(now if now is not None else time.time()))
    p = {k: "".join(c for c in str(v) if c not in "!'()*") for k, v in sorted(p.items())}
    q = urllib.parse.urlencode(p)
    p["w_rid"] = hashlib.md5((q + mixin_key(img_key, sub_key)).encode()).hexdigest()
    return p


def cookie_path(path: str | Path | None = None) -> Path:
    return Path(path or os.environ.get("KIYA_COOKIE_FILE") or os.environ.get("KIYA_BILIUP_COOKIE") or
                "/root/bili_cookies.txt").expanduser()


def memory_cookie_file(cfg: dict) -> str | None:
    """Explicit query credentials take priority; otherwise reuse the configured upload login."""
    return (os.environ.get("KIYA_COOKIE_FILE") or (cfg.get("memory") or {}).get("cookie_file") or
            os.environ.get("KIYA_BILIUP_COOKIE") or ((cfg.get("publish") or {}).get("bili") or {}).get("cookie"))


def cookie_header(path: str | Path | None) -> str:
    p = cookie_path(path)
    try:
        raw = p.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return ""
    pairs = {}
    def add(domain, name, value, expires=0):
        domain = str(domain or "").lstrip(".").lower()
        # The request target is api.bilibili.com: never forward unrelated or sibling-host credentials.
        if domain and domain not in ("bilibili.com", "api.bilibili.com"):
            return
        try:
            if float(expires or 0) > 0 and float(expires) <= time.time():
                return
        except (TypeError, ValueError):
            return
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_\-]+", name) or not isinstance(value, str) or not value:
            return
        if any(c in value for c in "\r\n;"):
            return
        pairs[name] = value
    if raw.lstrip().startswith(("{", "[")):
        try:
            data = json.loads(raw)
            rows = data if isinstance(data, list) else (data.get("cookie_info") or {}).get("cookies", data.get("cookies", []))
            if not isinstance(rows, list):
                return ""
            for row in rows:
                if isinstance(row, dict):
                    add(row.get("domain"), row.get("name"), row.get("value"), row.get("expires", row.get("expirationDate", 0)))
        except (ValueError, TypeError, AttributeError):
            return ""
    else:
        for line in raw.splitlines():
            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_"):]
            elif line.startswith("#"):
                continue
            row = line.split("\t")
            if len(row) >= 7:
                add(row[0], row[5], row[6], row[4])
    return "; ".join(f"{k}={v}" for k, v in pairs.items())


class Bili:
    def __init__(self, base: str | None = None, cookie_file: str | None = None, pause: float = 1.0):
        self.base = (base or os.environ.get("KIYA_BILI_API") or API).rstrip("/")
        self.cookie_file = cookie_path(cookie_file)
        self.cookie = cookie_header(self.cookie_file)
        self.pause = pause
        self._keys: tuple[str, str] | None = None
        self._last = 0.0

    def _get(self, path: str, params: dict, ok_codes: tuple = (0,)) -> dict:
        wait = self.pause - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        url = f"{self.base}{path}?{urllib.parse.urlencode(params)}"
        headers = {"User-Agent": UA, "Referer": "https://space.bilibili.com/"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as r:
                d = json.loads(r.read().decode("utf-8"))
        except Exception as e:                       # noqa: BLE001  网络错误统一报
            # Do not include an exception's request headers or cookie content in task logs.
            raise BiliError(f"请求失败 {path}：{type(e).__name__}") from e
        finally:
            self._last = time.time()
        if d.get("code") not in ok_codes:
            hint = "（风控或请求校验失败，不等同于登录过期；请单独检查登录状态）" if d.get("code") in (-352, -412, -799) else ""
            raise BiliError(f"{path} 返回 {d.get('code')} {d.get('message', '')}{hint}")
        return d.get("data") or {}

    def login_status(self) -> dict:
        data = self._get("/x/web-interface/nav", {}, ok_codes=(0, -101))
        return {"logged_in": data.get("isLogin") is True, "uid": data.get("mid") if data.get("isLogin") is True else None}

    def keys(self) -> tuple[str, str]:
        if self._keys is None:
            # 没登录时 nav 返回 -101“账号未登录”，但签名密钥照样给，不算失败
            w = self._get("/x/web-interface/nav", {}, ok_codes=(0, -101)).get("wbi_img") or {}
            stem = lambda u: Path(urllib.parse.urlparse(u or "").path).stem
            self._keys = (stem(w.get("img_url")), stem(w.get("sub_url")))
            if not all(self._keys):
                raise BiliError("拿不到 WBI 签名密钥")
        return self._keys

    def uploads(self, mid: int | str, limit: int = 30) -> list[dict]:
        """最新投稿（按发布时间倒序）。"""
        out, pn = [], 1
        while len(out) < limit:
            ps = min(30, limit - len(out))
            d = self._get("/x/space/wbi/arc/search", sign({"mid": mid, "ps": ps, "pn": pn, "order": "pubdate"}, *self.keys()))
            vl = ((d.get("list") or {}).get("vlist")) or []
            for v in vl:
                out.append({"bvid": v.get("bvid"), "title": v.get("title", ""), "pubdate": v.get("created"),
                            "view": v.get("play") if isinstance(v.get("play"), int) else 0,
                            "reply": v.get("comment", 0), "length": v.get("length", ""),
                            "desc": (v.get("description") or "")[:200]})
            if len(vl) < ps:
                break
            pn += 1
        return out[:limit]

    def stat(self, bvid: str) -> dict:
        d = self._get("/x/web-interface/view", {"bvid": bvid})
        s = d.get("stat") or {}
        return {"bvid": bvid, "title": d.get("title", ""), "pubdate": d.get("pubdate"), "duration": d.get("duration"),
                "owner_mid": (d.get("owner") or {}).get("mid"),
                "tname": d.get("tname", ""), **{k: s.get(k, 0) for k in
                                                ("view", "like", "coin", "favorite", "share", "reply", "danmaku")}}

    # ---------------------------------------------------------------- 热门（梗库用，只取标题 / 词条文字）
    def popular(self, limit: int = 50) -> list[str]:
        """综合热门视频标题。"""
        d = self._get("/x/web-interface/popular", {"ps": min(limit, 50), "pn": 1})
        return [str(v.get("title", "")).strip() for v in d.get("list") or [] if v.get("title")][:limit]

    def weekly_must_watch(self, limit: int = 50) -> list[str]:
        """最新一期“每周必看”的视频标题。"""
        series = (self._get("/x/web-interface/popular/series/list", {}).get("list")) or []
        if not series:
            return []
        number = max(int(x.get("number", 0)) for x in series)
        d = self._get("/x/web-interface/popular/series/one", {"number": number})
        return [str(v.get("title", "")).strip() for v in d.get("list") or [] if v.get("title")][:limit]

    def hot_search(self, limit: int = 30) -> list[str]:
        """热搜词。"""
        d = self._get("/x/web-interface/search/square", {"limit": limit})
        lst = (d.get("trending") or {}).get("list") or []
        return [str(x.get("keyword") or x.get("show_name") or "").strip() for x in lst if x.get("keyword") or x.get("show_name")][:limit]
