"""Server-side QR login and per-streamer biliup credentials. Never serialize secrets to the UI.

Protocol reference: biliup/biliup crates/biliup/src/uploader/credential.rs.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path

from .streamer import output_root, streamer_from_args

# Public BiliTV protocol identifiers used by biliup, not a user's login credentials.
APPKEY = "4409e2ce8ffd12b8"
APPSEC = "59b43e04ad6965f34319062b478f83dd"
PASSPORT = "https://passport.bilibili.com/x/passport-tv-login/qrcode/"


class LoginError(Exception):
    pass


def managed_cookie(streamer=None) -> Path:
    return output_root(streamer_from_args(streamer)) / "_credentials" / "bili.json"


def upload_cookie(bc: dict, streamer=None) -> Path:
    managed = managed_cookie(streamer)
    # A disabled/invalid managed file must not silently fall back to another account.
    if managed.exists():
        return managed
    return Path(os.environ.get("KIYA_BILIUP_COOKIE") or bc.get("cookie") or "~/.config/biliup/cookies.json").expanduser()


def read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def private_write(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.parent.chmod(0o700)
    fd, temp = tempfile.mkstemp(prefix=".login-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        Path(temp).unlink(missing_ok=True)


def revision(streamer=None):
    path = managed_cookie(streamer)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "none"


@contextmanager
def change_lock(streamer=None):
    root = managed_cookie(streamer).parent
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = root / "account-change.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime > 60:
        lock.unlink(missing_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise LoginError("账号正在更新，请稍后重试") from None
    try:
        yield
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def request_qr(action: str, code: str | None = None) -> dict:
    values = {"appkey": APPKEY, "local_id": "0", "ts": str(int(time.time()))}
    if code:
        values["auth_code"] = code
    form = urllib.parse.urlencode(sorted(values.items()))
    sign = hashlib.md5((form + APPSEC).encode()).hexdigest()
    req = urllib.request.Request(PASSPORT + action, data=(form + "&sign=" + sign).encode(),
        headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            data = json.load(response)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except Exception:
        raise LoginError("无法连接 B站登录服务，请稍后重试") from None


def account(path: Path) -> dict:
    from .bili import Bili
    try:
        data = Bili(cookie_file=str(path), pause=0)._get("/x/web-interface/nav", {}, ok_codes=(0, -101))
    except Exception:
        raise LoginError("账号状态暂时无法验证，请检查网络后重试") from None
    if data.get("isLogin") is not True or not str(data.get("mid", "")).isdigit():
        raise LoginError("登录凭证已失效，请重新扫码")
    return {"uid": str(data["mid"]), "name": str(data.get("uname") or "B站用户")[:80]}


def status(bc: dict, streamer=None) -> dict:
    path = upload_cookie(bc, streamer)
    source = "website" if path == managed_cookie(streamer) else "configured"
    if not path.is_file() or read(path).get("disabled"):
        return {"state": "logged_out", "source": source, "account": None}
    try:
        return {"state": "logged_in", "source": source, "account": account(path)}
    except LoginError as e:
        return {"state": "unknown" if "暂时" in str(e) else "expired", "source": source, "account": None, "message": str(e)}


def ticket_path(login_id: str, streamer=None) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", login_id or ""):
        raise LoginError("登录请求无效，请重新获取二维码")
    return managed_cookie(streamer).parent / "pending" / (login_id + ".json")


def start(streamer=None, method="qr") -> dict:
    import qrcode
    root = managed_cookie(streamer).parent / "pending"
    root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt": root.parent.chmod(0o700)
    if root.exists():
        for file in root.glob("*.json"):
            if re.fullmatch(r"[a-f0-9]{32}\.json", file.name) and read(file).get("expires_at", 0) <= time.time():
                file.unlink(missing_ok=True)
        if sum(1 for _ in root.glob("*.json")) >= 20:
            raise LoginError("待处理的登录请求过多，请稍后再试")
    result = request_qr("auth_code")
    data = result.get("data") or {}
    url = data.get("url") or ""
    parsed = urllib.parse.urlparse(url)
    if result.get("code") != 0 or parsed.scheme != "https" or parsed.hostname != "passport.bilibili.com" or not data.get("auth_code"):
        raise LoginError("B站未返回可用二维码，请稍后重试")
    login_id = uuid.uuid4().hex
    expires = int(time.time()) + 180
    private_write(ticket_path(login_id, streamer), {"auth_code": data["auth_code"], "expires_at": expires,
                                                  "state": "waiting", "method": method, "base_revision": revision(streamer)})
    image = qrcode.make(url)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    result = {"login_id": login_id, "expires_at": expires, "state": "waiting", "method": method,
              "qr_image": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()}
    if method == "browser": result["login_url"] = url
    return result


def pending(login_id, streamer=None):
    path = ticket_path(login_id, streamer)
    data = read(path)
    if not data or data.get("expires_at", 0) <= time.time():
        path.unlink(missing_ok=True)
        raise LoginError("二维码或确认请求已过期，请重新扫码")
    return path, data


def poll(login_id, streamer=None) -> dict:
    path, state = pending(login_id, streamer)
    if state.get("state") == "ready":
        return {"state": "ready", "account": state["account"], "expires_at": state["expires_at"]}
    if state.get("state") == "verifying":
        return verify_pending(path, state)
    if state.get("state") in ("captcha_required", "sms_sent", "action_required"):
        from .publish_login_methods import public_state
        return public_state(path, state)
    result = request_qr("poll", state["auth_code"])
    code = result.get("code")
    if code in (86039, 86090):
        return {"state": "scanned" if code == 86090 else "waiting", "expires_at": state["expires_at"]}
    if code == 86038:
        path.unlink(missing_ok=True)
        return {"state": "expired"}
    if code != 0:
        raise LoginError("扫码登录未完成，请重新获取二维码")
    credentials = result.get("data") or {}
    token = credentials.get("token_info") or {}
    cookies = (credentials.get("cookie_info") or {}).get("cookies") or []
    names = {c.get("name") for c in cookies if isinstance(c, dict) and c.get("value")}
    if (not {"SESSDATA", "bili_jct", "DedeUserID"} <= names or not token.get("access_token")
            or not token.get("refresh_token") or not str(token.get("mid", "")).isdigit()):
        raise LoginError("登录凭证不完整，请重新扫码")
    credentials = {"cookie_info": credentials["cookie_info"], "token_info": token,
                   "sso": credentials.get("sso") or [], "platform": "BiliTV"}
    state = {**state, "state": "verifying", "credentials": credentials, "expires_at": int(time.time()) + 300}
    state.pop("auth_code", None)
    private_write(path, state)
    return verify_pending(path, state)


def verify_pending(path, state):
    credentials = state["credentials"]
    # Store only in the protected server directory, then independently verify before activation.
    draft = path.with_suffix(".draft")
    try:
        private_write(draft, credentials)
        try:
            who = account(draft)
        except LoginError as e:
            return {"state": "verifying", "message": str(e), "expires_at": state["expires_at"]}
    finally:
        draft.unlink(missing_ok=True)
    if who["uid"] != str(credentials["token_info"]["mid"]):
        raise LoginError("登录账号校验不一致，请重新扫码")
    if state.get("expected_uid") and who["uid"] != state["expected_uid"]:
        raise LoginError("登录账号与导入的 Cookie 不一致，请重新登录")
    private_write(path, {**state, "state": "ready", "account": who, "expires_at": int(time.time()) + 300})
    return {"state": "ready", "account": who, "expires_at": int(time.time()) + 300}


def activate(login_id, expected_uid, streamer=None):
    path, state = pending(login_id, streamer)
    if state.get("state") != "ready" or state.get("account", {}).get("uid") != expected_uid:
        raise LoginError("账号尚未核验或确认信息不一致，请重新扫码")
    target = managed_cookie(streamer)
    with change_lock(streamer):
        if state.get("base_revision") != revision(streamer):
            raise LoginError("发布账号已被其他操作更新，请重新扫码后确认")
        if target.exists() and not read(target).get("disabled"):
            private_write(target.with_name("bili.previous.json"), read(target))
        private_write(target, state["credentials"])
        path.unlink(missing_ok=True)
    return {"state": "logged_in", "source": "website", "account": state["account"]}


def logout(streamer=None):
    target = managed_cookie(streamer)
    with change_lock(streamer):
        if target.exists() and not read(target).get("disabled"):
            private_write(target.with_name("bili.previous.json"), read(target))
        private_write(target, {"disabled": True, "revision": uuid.uuid4().hex})
    root = target.parent / "pending"
    if root.exists():
        for file in root.glob("*.json"):
            if re.fullmatch(r"[a-f0-9]{32}\.json", file.name):
                file.unlink(missing_ok=True)
    return {"state": "logged_out", "source": "website", "account": None}
