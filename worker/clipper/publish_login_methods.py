"""Password, SMS, browser authorization and cookie import for the shared confirmation flow.

Passwords/OTP/imported text are transient stdin inputs, not ticket fields. Captchas are
completed by a human in the vendor widget; Bilibili validates the submitted proof.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
import uuid

from . import publish_account as auth

ANDROID_KEY = "783bbb7264451d82"
ANDROID_SECRET = "2653583c8873dea268ab9386918b1d65"
HOST = "https://passport.bilibili.com"


def text(body, key, maximum, required=True):
    value = body.get(key, "")
    if not isinstance(value, str) or len(value) > maximum or (required and not value):
        raise auth.LoginError("登录输入不完整或长度超限，请检查后重试")
    return value


def passport(path, params, *, get=False, signed=True, cookie=None):
    values = dict(params)
    if signed:
        values.update(appkey=ANDROID_KEY, ts=str(int(time.time())))
    form = urllib.parse.urlencode(sorted(values.items()))
    if signed:
        form += "&sign=" + hashlib.md5((form + ANDROID_SECRET).encode()).hexdigest()
    headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded",
               "Origin": "https://passport.bilibili.com", "Referer": "https://passport.bilibili.com/"}
    if cookie: headers["Cookie"] = cookie
    request = urllib.request.Request(HOST + path + ("?"+form if get else ""),
                                     data=None if get else form.encode(), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            result = json.loads(response.read(2_000_001))
        if not isinstance(result, dict) or type(result.get("code")) is not int:
            raise ValueError()
        return result
    except Exception:
        raise auth.LoginError("B站登录服务暂时无法连接，请稍后重试") from None


def safe_url(value):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value): return None
    try:
        parsed = urllib.parse.urlparse(value)
        keys = {k.lower() for k in urllib.parse.parse_qs(parsed.query)}
        if (parsed.scheme != "https" or parsed.hostname not in {"passport.bilibili.com", "www.bilibili.com"}
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or keys & {"sessdata", "bili_jct", "access_token", "refresh_token"}): return None
        return value
    except ValueError:
        return None


def new_ticket(method, streamer):
    root = auth.managed_cookie(streamer).parent / "pending"
    if root.exists():
        for file in root.glob("*.json"):
            if re.fullmatch(r"[a-f0-9]{32}\.json", file.name) and auth.read(file).get("expires_at", 0) <= time.time():
                file.unlink(missing_ok=True)
        if len(list(root.glob("*.json"))) >= 20:
            raise auth.LoginError("待处理登录过多，请取消旧请求或稍后再试")
    login_id = uuid.uuid4().hex
    path = auth.ticket_path(login_id, streamer)
    state = {"method": method, "state": "starting", "base_revision": auth.revision(streamer), "expires_at": int(time.time())+600}
    auth.private_write(path, state)
    return path, state


def load_ticket(login_id, method, streamer):
    path, state = auth.pending(login_id, streamer)
    if state.get("method") != method:
        raise auth.LoginError("登录方式与当前请求不一致，请重新开始")
    return path, state


def public_state(path, state):
    result = {"login_id": path.stem, "method": state["method"], "state": state["state"], "expires_at": state["expires_at"]}
    for key in ("account", "message", "masked_phone"):
        if key in state: result[key] = state[key]
    if state.get("captcha"):
        result["captcha"] = {k:state["captcha"][k] for k in ("gt", "challenge")}
    if safe_url(state.get("login_url")): result["login_url"] = state["login_url"]
    if state.get("sent_at"):
        result["retry_after"] = max(0, int(state["sent_at"] + 60 - time.time()))
    return result


def challenge(path, state, data):
    url = safe_url(data.get("recaptcha_url") or data.get("url"))
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query) if url else {}
    first = lambda *names: next((query[n][0] for n in names if query.get(n)), "")
    geetest = data.get("geetest") if isinstance(data.get("geetest"), dict) else {}
    gt = data.get("gee_gt") or geetest.get("gt") or first("gee_gt", "gt")
    value = data.get("gee_challenge") or geetest.get("challenge") or first("gee_challenge", "challenge")
    token = data.get("recaptcha_token") or data.get("token") or first("recaptcha_token", "token")
    if all(isinstance(x, str) and 8 <= len(x) <= 512 and re.fullmatch(r"[A-Za-z0-9_-]+", x) for x in (gt,value,token)):
        state.update(state="captcha_required", captcha={"gt":gt,"challenge":value,"token":token})
    else:
        state.update(state="action_required", message="B站要求额外验证，请在官方页面完成后重试，或改用浏览器登录")
    if url: state["login_url"] = url
    auth.private_write(path, state)
    return public_state(path, state)


def proof(body, state):
    values = body.get("proof")
    if state.get("state") != "captcha_required" or not isinstance(values, dict): return None
    result = {}
    for key in ("challenge", "validate", "seccode"):
        value = values.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_|.-]{8,512}", value):
            raise auth.LoginError("请先完成人机验证")
        result[key] = value
    return result


def normalize_credentials(raw, platform):
    if not isinstance(raw, dict): raise auth.LoginError("未取得完整的投稿凭证")
    cookie = raw.get("cookie_info") or {}
    token = raw.get("token_info") or {}
    if not isinstance(cookie, dict) or not isinstance(cookie.get("cookies"), list):
        raise auth.LoginError("登录 Cookie 格式无效")
    names = {c.get("name") for c in cookie.get("cookies", []) if isinstance(c, dict) and c.get("value")}
    if (not {"SESSDATA","bili_jct","DedeUserID"} <= names or not isinstance(token,dict)
            or not re.fullmatch(r"[0-9]{1,20}",str(token.get("mid", "")))
            or not all(isinstance(token.get(k),str) and token[k] for k in ("access_token","refresh_token"))
            or type(token.get("expires_in")) is not int or not 0 <= token["expires_in"] <= 4294967295):
        raise auth.LoginError("未取得完整的投稿凭证，请重试或改用浏览器登录")
    sso = raw.get("sso") or []
    if not isinstance(sso,list) or not all(isinstance(x,str) for x in sso):
        raise auth.LoginError("登录文件格式无效")
    if platform not in ("BiliTV", "Android", None): raise auth.LoginError("登录文件平台不受支持")
    return {"cookie_info": cookie, "token_info": {**token,"mid":int(token["mid"])}, "sso":sso, "platform":platform or "Android"}


def stage(path, state, data, platform):
    credentials = normalize_credentials(data, platform)
    # Discard phone/SMS/captcha parameters once the authenticated credential is available.
    state = {k:state[k] for k in ("method","base_revision","expected_uid") if k in state}
    state.update(state="verifying", credentials=credentials, expires_at=int(time.time())+300)
    auth.private_write(path,state)
    return {**auth.verify_pending(path,state), "login_id":path.stem, "method":state["method"]}


def password_login(body, streamer, login_id=None):
    username, password = text(body,"username",256), text(body,"password",256)
    path, state = load_ticket(login_id,"password",streamer) if login_id else new_ticket("password",streamer)
    try:
        key_result = passport("/x/passport-login/web/key", {}, get=True)
        key = key_result.get("data") or {}
        if key_result.get("code") != 0 or not key.get("key") or not key.get("hash"):
            raise auth.LoginError("B站未提供可用的密码加密参数，请重试")
        try:
            from cryptography.hazmat.primitives.serialization import load_pem_public_key
            from cryptography.hazmat.primitives.asymmetric.padding import PKCS1v15
            encrypted = load_pem_public_key(key["key"].encode()).encrypt((key["hash"]+password).encode(), PKCS1v15())
        except Exception:
            raise auth.LoginError("密码加密失败，请检查输入或改用浏览器登录") from None
        payload = dict(actionKey="appkey",build=6270200,captcha="",challenge="",channel="bili",device="phone",mobi_app="android",
                       password=base64.b64encode(encrypted).decode().rstrip("="),permission="ALL",platform="android",seccode="",subid=1,username=username,validate="")
        completed = proof(body,state)
        if state.get("state") == "captcha_required" and not completed: return public_state(path,state)
        if completed: payload.update(captcha=state["captcha"]["token"], **completed)
        response = passport("/x/passport-login/oauth2/login",payload)
        data = response.get("data") or {}
        if response["code"] == 0 and data.get("cookie_info"): return stage(path,state,data,"Android")
        if data.get("url") or data.get("recaptcha_url") or data.get("geetest"):
            return challenge(path,state,data)
        if response["code"] in (-629,-627): raise auth.LoginError("账号或密码错误，请检查后重试")
        raise auth.LoginError("密码登录未完成，可能需要身份核验，请重试或使用浏览器登录")
    except Exception:
        if not login_id: path.unlink(missing_ok=True)
        raise


def sms_send(body, streamer, login_id=None):
    country, phone = text(body,"country",4), text(body,"phone",20)
    if not re.fullmatch(r"[1-9][0-9]{0,3}",country) or not re.fullmatch(r"[0-9]{5,20}",phone):
        raise auth.LoginError("请填写有效的国家代码和手机号")
    path, state = load_ticket(login_id,"sms",streamer) if login_id else new_ticket("sms",streamer)
    if state.get("phone") and (state["phone"] != phone or state["country"] != country):
        raise auth.LoginError("手机号已改变，请重新开始短信登录")
    if time.time() - state.get("sent_at",0) < 60: raise auth.LoginError("请等待 60 秒后再发送短信")
    state.update(phone=phone,country=country,masked_phone=phone[:2]+"****"+phone[-3:])
    completed = proof(body,state)
    if state.get("state") == "captcha_required" and not completed: return public_state(path,state)
    digest=hashlib.md5(uuid.uuid4().bytes).hexdigest().upper()
    payload=dict(actionKey="appkey",build=6510400,buvid="XY"+digest[2]+digest[12]+digest[22]+digest,
                 channel="bili",cid=country,device="phone",mobi_app="android",platform="android",tel=phone)
    if completed:
        payload.update(gee_challenge=completed["challenge"],gee_validate=completed["validate"],gee_seccode=completed["seccode"],recaptcha_token=state["captcha"]["token"])
    response=passport("/x/passport-login/sms/send",payload)
    data=response.get("data") or {}
    if response["code"] == 0 and data.get("captcha_key"):
        state.update(state="sms_sent",sms_payload={**payload,"captcha_key":data["captcha_key"]},sent_at=int(time.time()),expires_at=int(time.time())+600)
        state.pop("captcha",None); state.pop("login_url",None)
        auth.private_write(path,state)
        return public_state(path,state)
    if data.get("recaptcha_url") or data.get("url"): return challenge(path,state,data)
    if not login_id: path.unlink(missing_ok=True)
    raise auth.LoginError("短信未发送成功，请稍后重试或使用浏览器登录")


def sms_login(body, streamer, login_id):
    path,state=load_ticket(login_id,"sms",streamer)
    if state.get("state") != "sms_sent" or not state.get("sms_payload"):
        raise auth.LoginError("请先发送短信验证码")
    code=text(body,"code",8)
    if not re.fullmatch(r"[0-9]{4,8}",code): raise auth.LoginError("请填写短信验证码")
    attempts=state.get("code_attempts",0)
    if attempts >= 5: raise auth.LoginError("验证码尝试次数过多，请重新开始登录")
    state["code_attempts"]=attempts+1; auth.private_write(path,state)
    result=passport("/x/passport-login/login/sms",{**state["sms_payload"],"code":code})
    data=result.get("data") or {}
    if result["code"] == 0 and data.get("cookie_info"): return stage(path,state,data,"Android")
    if data.get("url") or data.get("recaptcha_url"):
        # A post-OTP identity check is not the send-SMS captcha; do not resend an OTP as its continuation.
        state.update(state="action_required", message="B站要求额外身份核验，请改用浏览器登录完成授权")
        url = safe_url(data.get("url") or data.get("recaptcha_url"))
        if url: state["login_url"] = url
        auth.private_write(path,state)
        return public_state(path,state)
    raise auth.LoginError("验证码错误或已过期，请检查后重试")


def parse_cookie(value):
    try: data=json.loads(value)
    except ValueError: data=None
    if isinstance(data,dict) and data.get("token_info") and data.get("cookie_info"):
        return normalize_credentials(data,data.get("platform") or "Android"), None
    if isinstance(data,dict):
        info = data.get("cookie_info")
        rows=data.get("cookies") or (info.get("cookies") if isinstance(info,dict) else None)
        if rows is None: rows=[{"name":k,"value":v} for k,v in data.items() if isinstance(v,str)]
    elif isinstance(data,list): rows=data
    elif data is not None: raise auth.LoginError("Cookie 格式不支持")
    else:
        value=re.sub(r"^Cookie:\s*","",value,flags=re.I)
        if "\n" in value or "\r" in value: raise auth.LoginError("请只粘贴 Cookie 值，不要粘贴整段请求头")
        rows=[{"name":p.split("=",1)[0].strip(),"value":p.split("=",1)[1].strip()} for p in value.split(";") if "=" in p]
    cookies={}
    for row in rows if isinstance(rows,list) else []:
        if not isinstance(row,dict): continue
        domain=str(row.get("domain") or ".bilibili.com").lstrip(".").lower()
        if domain != "bilibili.com" and not domain.endswith(".bilibili.com"): continue
        name,val=row.get("name"),row.get("value")
        if isinstance(name,str) and isinstance(val,str) and re.fullmatch(r"[A-Za-z0-9_]+",name) and not any(ord(c)<32 or c==';' for c in val): cookies[name]=val
    if not cookies.get("SESSDATA") or not cookies.get("bili_jct"):
        raise auth.LoginError("Cookie 需包含 SESSDATA 和 bili_jct，或导入完整 biliup 登录 JSON")
    return None,cookies


def cookie_login(body, streamer):
    value=text(body,"cookie",65536)
    credentials,cookies=parse_cookie(value)
    if credentials:
        path,state=new_ticket("cookie",streamer)
        return stage(path,state,credentials,credentials["platform"])
    # Validate the imported web session first, without ever returning its contents.
    path,state=new_ticket("cookie",streamer)
    draft=path.with_suffix(".draft")
    try:
        auth.private_write(draft,{"cookie_info":{"cookies":[{"name":k,"value":v} for k,v in cookies.items()]}})
        who=auth.account(draft)
    finally:
        draft.unlink(missing_ok=True); path.unlink(missing_ok=True)
    qr=auth.start(streamer,method="cookie")
    path,state=auth.pending(qr["login_id"],streamer)
    state["expected_uid"]=who["uid"]; auth.private_write(path,state)
    response=passport("/x/passport-tv-login/h5/qrcode/confirm",{"auth_code":state["auth_code"],"csrf":cookies["bili_jct"],"scanning_type":3},
                      signed=False,cookie="SESSDATA="+cookies["SESSDATA"]+"; bili_jct="+cookies["bili_jct"])
    if response["code"] != 0:
        path.unlink(missing_ok=True)
        raise auth.LoginError("Cookie 授权未完成，请更新 Cookie 或改用浏览器登录")
    return {**auth.poll(path.stem,streamer),"login_id":path.stem,"method":"cookie"}
