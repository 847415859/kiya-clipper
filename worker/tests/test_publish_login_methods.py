import base64
import json

import pytest
from clipper import publish_account as auth, publish_login_methods as methods
from test_publish_account import world


def credentials():
    return {"cookie_info": {"cookies": [{"name": k, "value": "secret-"+k} for k in ("SESSDATA", "bili_jct", "DedeUserID")]},
            "token_info": {"mid": 123, "access_token": "secret-access", "refresh_token": "secret-refresh", "expires_in":3600}}


def test_password_is_rsa_encrypted_and_not_saved(world, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from cryptography.hazmat.primitives import serialization
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    def passport(path, body, **kwargs):
        if path.endswith('/key'): return {"code": 0, "data": {"key": pem, "hash": "salt-"}}
        encrypted = body['password']
        assert private.decrypt(base64.b64decode(encrypted+'='*(-len(encrypted)%4)), padding.PKCS1v15()) == b'salt-private-password'
        return {"code": 0, "data": credentials()}
    monkeypatch.setattr(methods, 'passport', passport)
    result = methods.password_login({"username": "user", "password": "private-password"}, 'kiya')
    assert result['state'] == 'ready'
    assert not auth.managed_cookie().exists()
    saved = auth.ticket_path(result['login_id']).read_text()
    assert 'private-password' not in saved and 'username' not in saved
    assert 'secret' not in json.dumps(result)
    auth.activate(result['login_id'], '123')
    assert auth.read(auth.managed_cookie())['platform'] == 'Android'


def test_sms_manual_captcha_then_otp_preserves_leading_zero(world, monkeypatch):
    calls = []
    def passport(path, body, **kwargs):
        calls.append((path, body))
        if path.endswith('/sms/send'):
            if not body.get('gee_validate'):
                return {"code": -105, "data": {"recaptcha_url": 'https://passport.bilibili.com/verify?gee_gt=abcdefgh&gee_challenge=challenge123&recaptcha_token=token12345'}}
            assert body['recaptcha_token'] == 'token12345'
            return {"code": 0, "data": {"captcha_key": "private-sms-key"}}
        assert body['code'] == '001234' and body['captcha_key'] == 'private-sms-key'
        return {"code": 0, "data": credentials()}
    monkeypatch.setattr(methods, 'passport', passport)
    body = {'country': '86', 'phone': '13800000000'}
    result = methods.sms_send(body, 'kiya')
    login_id = result['login_id']
    assert result['state'] == 'captcha_required'
    assert 'token' not in result['captcha'] and '13800000000' not in json.dumps(result)
    assert methods.sms_send(body, 'kiya', login_id)['state'] == 'captcha_required'
    assert len(calls) == 1  # Cannot proceed without a human proof.
    body['proof'] = {'challenge': 'challenge123', 'validate': 'validate123', 'seccode': 'validate123|jordan'}
    sent = methods.sms_send(body, 'kiya', login_id)
    assert sent['state'] == 'sms_sent'
    with pytest.raises(auth.LoginError, match='60 秒'): methods.sms_send(body, 'kiya', login_id)
    with pytest.raises(auth.LoginError, match='手机号'): methods.sms_send({**body, 'phone':'13900000000'}, 'kiya', login_id)
    ready = methods.sms_login({'code':'001234'}, 'kiya', login_id)
    assert ready['state'] == 'ready'
    saved = auth.ticket_path(login_id).read_text()
    assert '001234' not in saved and '13800000000' not in saved and 'private-sms-key' not in saved


def test_sms_attempt_limit_and_post_otp_identity_check(world, monkeypatch):
    monkeypatch.setattr(methods, 'passport', lambda path, body, **kw: {'code':0,'data':{'captcha_key':'sms-key'}} if path.endswith('/send') else {'code':-1,'data':{}})
    result = methods.sms_send({'country':'86','phone':'13800000000'},'kiya')
    for _ in range(5):
        with pytest.raises(auth.LoginError, match='验证码错误'): methods.sms_login({'code':'000000'},'kiya',result['login_id'])
    with pytest.raises(auth.LoginError, match='次数'): methods.sms_login({'code':'000000'},'kiya',result['login_id'])
    state = auth.read(auth.ticket_path(result['login_id'])); state['code_attempts'] = 0
    auth.private_write(auth.ticket_path(result['login_id']), state)
    monkeypatch.setattr(methods, 'passport', lambda *a, **kw: {'code':-1,'data':{'url':'https://passport.bilibili.com/verify'}})
    assert methods.sms_login({'code':'000000'},'kiya',result['login_id'])['state'] == 'action_required'


def test_cookie_header_is_exchanged_for_full_tokens_and_uid_checked(world, monkeypatch):
    def passport(path, payload, **kw):
        assert path.endswith('/h5/qrcode/confirm')
        assert kw['cookie'] == 'SESSDATA=web-secret; bili_jct=csrf-secret'
        assert payload['csrf'] == 'csrf-secret' and payload['auth_code'] == 'secret-code'
        return {'code':0}
    monkeypatch.setattr(methods, 'passport', passport)
    result = methods.cookie_login({'cookie':'Cookie: SESSDATA=web-secret; bili_jct=csrf-secret'},'kiya')
    assert result['state'] == 'ready' and 'secret' not in json.dumps(result)
    assert 'web-secret' not in auth.ticket_path(result['login_id']).read_text()
    assert not list(world.rglob('*.draft'))
    calls = iter([{'uid':'999','name':'first'},{'uid':'123','name':'second'}])
    monkeypatch.setattr(auth, 'account', lambda path: next(calls))
    with pytest.raises(auth.LoginError, match='不一致'):
        methods.cookie_login({'cookie':'SESSDATA=web-secret; bili_jct=csrf-secret'},'kiya')
    assert not auth.managed_cookie().exists()


def test_full_login_json_and_browser_authorization_require_confirmation(world):
    imported = methods.cookie_login({'cookie':json.dumps(credentials())}, 'kiya')
    assert imported['state'] == 'ready' and not auth.managed_cookie().exists()
    browser = auth.start('kiya', method='browser')
    assert browser['login_url'].startswith('https://passport.bilibili.com/')
    assert auth.poll(browser['login_id'])['state'] == 'ready'


@pytest.mark.parametrize('value', ['SESSDATA=only', 'SESSDATA=a\nb; bili_jct=c', '{"cookie_info":[]}',
    '[{"name":"SESSDATA","value":"x","domain":"evil.com"},{"name":"bili_jct","value":"x"}]',
    '{"cookie_info":"invalid","token_info":{"mid":123}}'])
def test_invalid_cookie_is_rejected(value):
    with pytest.raises(auth.LoginError): methods.parse_cookie(value)


@pytest.mark.parametrize('url', ['http://passport.bilibili.com/', 'https://evil.com/', 'https://passport.bilibili.com.evil.com/',
    'https://passport.bilibili.com/?access_token=secret','https://user@passport.bilibili.com/'])
def test_official_urls_never_expose_credentials_or_untrusted_origins(url):
    assert methods.safe_url(url) is None
