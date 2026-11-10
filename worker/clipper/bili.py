"""B站公开接口：某个 UP 主的投稿列表、单个视频的播放 / 点赞等数据。

只读公开数据，请求之间间隔 1 秒。投稿列表接口要 WBI 签名（B站网页端的标准参数签名），
机房 IP 容易被风控，所以带上和监控脚本共用的 Cookie 文件（Netscape 格式，KIYA_COOKIE_FILE）。
完播率、涨粉数不是公开数据，只能在创作中心看，后台可以手动填。
"""
from __future__ import annotations

import hashlib
import json
import os
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


def cookie_header(path: str | Path | None) -> str:
    p = Path(path or os.environ.get("KIYA_COOKIE_FILE") or "/root/bili_cookies.txt")
    if not p.exists():
        return ""
    try:
        rows = [l.split("\t") for l in p.read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")]
    except OSError:
        return ""
    return "; ".join(f"{r[5]}={r[6]}" for r in rows if len(r) >= 7)


class Bili:
    def __init__(self, base: str | None = None, cookie_file: str | None = None, pause: float = 1.0):
        self.base = (base or os.environ.get("KIYA_BILI_API") or API).rstrip("/")
        self.cookie = cookie_header(cookie_file)
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
            raise BiliError(f"请求失败 {path}：{e}") from e
        finally:
            self._last = time.time()
        if d.get("code") not in ok_codes:
            hint = "（被风控：检查 Cookie 文件是否有效）" if d.get("code") in (-352, -412, -799) else ""
            raise BiliError(f"{path} 返回 {d.get('code')} {d.get('message', '')}{hint}")
        return d.get("data") or {}

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
                "tname": d.get("tname", ""), **{k: s.get(k, 0) for k in
                                                ("view", "like", "coin", "favorite", "share", "reply", "danmaku")}}
