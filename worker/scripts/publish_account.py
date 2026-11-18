#!/usr/bin/env python3
"""Short JSON-only server commands. Never print cookies, tokens, upstream response or tracebacks."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clipper import publish_account as auth
from clipper.env import load_env
from clipper.kcfg import load_cfg
from clipper.streamer import resolve_config, streamer_from_args


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=["status", "start", "poll", "activate", "cancel", "logout", "password", "sms-send", "sms-code", "cookie", "browser"])
    ap.add_argument("--streamer", required=True)
    ap.add_argument("--login-id")
    ap.add_argument("--uid")
    args = ap.parse_args()
    try:
        load_env()
        sid = streamer_from_args(args.streamer)
        if args.action in ("password", "sms-send", "sms-code", "cookie", "browser"):
            from clipper import publish_login_methods as methods
            raw = sys.stdin.buffer.read(131073)
            if len(raw) > 131072: raise auth.LoginError("登录输入过长")
            body = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(body, dict): raise auth.LoginError("登录输入格式不正确")
            if args.action == "password": result = methods.password_login(body,sid,args.login_id)
            elif args.action == "sms-send": result = methods.sms_send(body,sid,args.login_id)
            elif args.action == "sms-code": result = methods.sms_login(body,sid,args.login_id)
            elif args.action == "cookie": result = methods.cookie_login(body,sid)
            else: result = auth.start(sid,method="browser")
        elif args.action == "status":
            cfg = load_cfg(resolve_config(None, streamer=sid))
            result = auth.status((cfg.get("publish") or {}).get("bili") or {}, sid)
        elif args.action == "start": result = auth.start(sid)
        elif args.action == "poll": result = auth.poll(args.login_id, sid)
        elif args.action == "activate": result = auth.activate(args.login_id, args.uid, sid)
        elif args.action == "logout": result = auth.logout(sid)
        else:
            auth.ticket_path(args.login_id, sid).unlink(missing_ok=True)
            result = {"state": "cancelled"}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except auth.LoginError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 2
    except Exception:
        print(json.dumps({"error": "发布账号操作失败，请检查 worker 依赖和服务配置后重试"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
