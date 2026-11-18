import json
from pathlib import Path

import pytest
from clipper import publish_account as auth
from clipper.bili import memory_cookie_file

REAL_REQUEST = auth.request_qr


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "output_root", lambda sid: tmp_path / sid)
    monkeypatch.setattr(auth, "account", lambda path: {"uid": "123", "name": "测试发布账号"})
    def request(action, code=None):
        if action == "auth_code":
            return {"code": 0, "data": {"url": "https://passport.bilibili.com/x/passport-tv-login/h5/qrcode/auth?auth_code=secret-code", "auth_code": "secret-code"}}
        return {"code": 0, "data": {"cookie_info": {"cookies": [
            {"name": k, "value": "secret-"+k} for k in ("SESSDATA", "bili_jct", "DedeUserID")]},
            "token_info": {"mid": 123, "access_token": "secret-access", "refresh_token": "secret-refresh", "expires_in": 1000}, "sso": []}}
    monkeypatch.setattr(auth, "request_qr", request)
    return tmp_path


def ready(sid="kiya"):
    qr = auth.start(sid)
    who = auth.poll(qr["login_id"], sid)
    assert who["state"] == "ready"
    return qr, who


def test_qr_requires_explicit_activation_and_never_exposes_credentials(world):
    qr, who = ready()
    assert qr["qr_image"].startswith("data:image/png;base64,iVBOR")
    assert not auth.managed_cookie("kiya").exists()
    public = json.dumps([qr, who])
    for value in ("secret-access", "secret-refresh", "secret-SESSDATA", "secret-code"):
        assert value not in public
    result = auth.activate(qr["login_id"], "123", "kiya")
    assert result["account"]["uid"] == "123"
    saved = auth.read(auth.managed_cookie("kiya"))
    assert saved["platform"] == "BiliTV" and saved["token_info"]["access_token"] == "secret-access"
    assert not auth.ticket_path(qr["login_id"], "kiya").exists()


def test_scanned_expired_and_malformed_response_preserve_old_account(world, monkeypatch):
    old = {"old": "secret"}
    auth.private_write(auth.managed_cookie(), old)
    qr = auth.start()
    monkeypatch.setattr(auth, "request_qr", lambda *a: {"code": 86090})
    assert auth.poll(qr["login_id"])["state"] == "scanned"
    monkeypatch.setattr(auth, "request_qr", lambda *a: {"code": 86038})
    assert auth.poll(qr["login_id"])["state"] == "expired"
    assert auth.read(auth.managed_cookie()) == old
    with pytest.raises(auth.LoginError): auth.poll(qr["login_id"])


def test_wrong_account_scope_and_ticket_expiry_cannot_activate(world, monkeypatch):
    qr, _ = ready()
    with pytest.raises(auth.LoginError): auth.activate(qr["login_id"], "999", "kiya")
    with pytest.raises(auth.LoginError): auth.activate(qr["login_id"], "123", "miko")
    with pytest.raises(auth.LoginError): auth.ticket_path("../bili")
    path = auth.ticket_path(qr["login_id"])
    record = auth.read(path); record["expires_at"] = 0; auth.private_write(path, record)
    with pytest.raises(auth.LoginError): auth.activate(qr["login_id"], "123")
    assert not path.exists()


def test_logout_does_not_fall_back_to_old_configured_account(world, monkeypatch):
    legacy = world / "legacy.json"
    legacy.write_text('{"old": "secret"}')
    monkeypatch.setenv("KIYA_BILIUP_COOKIE", str(legacy))
    assert auth.upload_cookie({}) == legacy
    qr, _ = ready()
    auth.activate(qr["login_id"], "123")
    assert auth.upload_cookie({}) == auth.managed_cookie()
    assert memory_cookie_file({}) == str(auth.managed_cookie())
    auth.logout()
    assert auth.status({})["state"] == "logged_out"
    assert auth.upload_cookie({}) == auth.managed_cookie()
    assert legacy.read_text() == '{"old": "secret"}'
    assert auth.read(auth.managed_cookie())["disabled"]


def test_two_pending_switches_cannot_overwrite_each_other(world):
    one, _ = ready(); two, _ = ready()
    auth.activate(one["login_id"], "123")
    with pytest.raises(auth.LoginError, match="其他操作"):
        auth.activate(two["login_id"], "123")


def test_publish_status_uses_requested_streamer_and_respects_disabled_marker(world, monkeypatch):
    from scripts.publish_bili import status, cookie_path
    legacy = world / "legacy.json"; legacy.write_text('{"cookie_info":{}}')
    monkeypatch.setenv("KIYA_BILIUP_COOKIE",str(legacy))
    auth.private_write(auth.managed_cookie("miko"), {"cookie_info":{"cookies":[]},"token_info":{}})
    assert status({},"miko")["cookie"] == str(auth.managed_cookie("miko"))
    assert cookie_path({},"kiya") == legacy
    auth.logout("miko")
    assert status({},"miko")["logged_in"] is False


def test_temporary_verification_failure_can_retry_without_reusing_qr(world, monkeypatch):
    qr = auth.start()
    def failed(path): raise auth.LoginError("账号状态暂时无法验证")
    monkeypatch.setattr(auth, "account", failed)
    assert auth.poll(qr["login_id"])["state"] == "verifying"
    monkeypatch.setattr(auth, "request_qr", lambda *a: (_ for _ in ()).throw(AssertionError("QR already consumed")))
    monkeypatch.setattr(auth, "account", lambda path: {"uid":"123","name":"用户"})
    assert auth.poll(qr["login_id"])["state"] == "ready"


def test_relogin_preserves_last_valid_backup_and_repeated_logout_invalidates_old_ticket(world):
    first,_=ready(); auth.activate(first["login_id"],"123")
    original=auth.read(auth.managed_cookie())
    auth.logout()
    second,_=ready()
    auth.activate(second["login_id"],"123")
    assert auth.read(auth.managed_cookie().with_name("bili.previous.json")) == original
    auth.logout()
    stale,_=ready()
    path=auth.ticket_path(stale["login_id"])
    delayed=auth.read(path)
    auth.logout()
    auth.private_write(path,delayed)  # Simulate a poll finishing just after logout cleared pending state.
    with pytest.raises(auth.LoginError,match="其他操作"):
        auth.activate(stale["login_id"],"123")


def test_network_errors_never_echo_upstream_cookie_headers(world, monkeypatch):
    # Restore real transport while replacing urlopen with a failure containing a mock secret.
    monkeypatch.setattr(auth.urllib.request, "urlopen", lambda *a,**k: (_ for _ in ()).throw(ValueError("cookie=secret")))
    with pytest.raises(auth.LoginError) as error:
        REAL_REQUEST("auth_code")
    assert "secret" not in str(error.value)
