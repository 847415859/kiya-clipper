import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from clipper.bili import Bili, BiliError, cookie_header, cookie_path, memory_cookie_file
from learn import fetch_my_uploads


def test_netscape_preserves_httponly_and_filters_expired_or_other_domains(tmp_path):
    file = tmp_path / "cookie.txt"
    file.write_text("# Netscape HTTP Cookie File\n"
        "#HttpOnly_.bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\ttest-session\n"
        ".bilibili.com\tTRUE\t/\tFALSE\t0\tbuvid3\ttest-device\n"
        ".bilibili.com\tTRUE\t/\tFALSE\t1\texpired\told\n"
        "other.example\tTRUE\t/\tFALSE\t0\tpassword\twrong-domain\n"
        "live.bilibili.com\tFALSE\t/\tTRUE\t0\tlive_only\twrong-host\n", encoding="utf-8-sig")
    assert cookie_header(file) == "SESSDATA=test-session; buvid3=test-device"


@pytest.mark.parametrize("wrap", [lambda rows: rows, lambda rows: {"cookies": rows}, lambda rows: {"cookie_info": {"cookies": rows}}])
def test_reads_biliup_and_browser_json_cookies(tmp_path, wrap):
    file = tmp_path / "cookie.json"
    file.write_text(json.dumps(wrap([{"name": "SESSDATA", "value": "test-session"},
                                    {"name": "buvid3", "value": "test-device", "domain": ".bilibili.com"},
                                    {"name": "bad", "value": "injected;value"},
                                    {"name": "stale", "value": "expired", "expirationDate": 1}]), ensure_ascii=False), encoding="utf-8")
    assert cookie_header(file) == "SESSDATA=test-session; buvid3=test-device"


def test_missing_malformed_and_explicit_paths_do_not_silently_use_other_accounts(tmp_path, monkeypatch):
    monkeypatch.setenv("KIYA_COOKIE_FILE", str(tmp_path / "query.txt"))
    monkeypatch.setenv("KIYA_BILIUP_COOKIE", str(tmp_path / "upload.json"))
    assert cookie_path() == tmp_path / "query.txt"
    assert cookie_header(tmp_path / "missing") == ""
    assert cookie_path("~/bili-cookies.json") == Path.home() / "bili-cookies.json"
    for raw in ('{"cookie_info": false}', '{"cookies": {}}', '[null, 1, "wrong"]', '{broken'):
        file = tmp_path / "invalid.json"
        file.write_text(raw, encoding="utf-8")
        assert cookie_header(file) == ""
    cfg = {"memory": {"cookie_file": "memory.txt"}, "publish": {"bili": {"cookie": "upload.json"}}}
    assert memory_cookie_file(cfg) == str(tmp_path / "query.txt")
    monkeypatch.delenv("KIYA_COOKIE_FILE")
    assert memory_cookie_file(cfg) == "memory.txt"
    cfg.pop("memory")
    assert memory_cookie_file(cfg) == str(tmp_path / "upload.json")
    monkeypatch.delenv("KIYA_BILIUP_COOKIE")
    assert memory_cookie_file(cfg) == "upload.json"


def test_login_status_is_separate_from_wbi_keys_and_network_errors_hide_values(tmp_path, monkeypatch):
    b = Bili(cookie_file=str(tmp_path / "missing"), pause=0)
    monkeypatch.setattr(b, "_get", lambda *a, **k: {"isLogin": True, "mid": 123})
    assert b.login_status() == {"logged_in": True, "uid": 123}
    monkeypatch.setattr(b, "_get", lambda *a, **k: {"wbi_img": {"img_url": "key.png"}})
    assert not b.login_status()["logged_in"]
    def bad_request(*a, **k):
        raise ValueError("fake-cookie-secret")
    monkeypatch.setattr("urllib.request.urlopen", bad_request)
    other = Bili(cookie_file=str(tmp_path / "missing"), pause=0)
    with pytest.raises(BiliError) as error:
        other.login_status()
    assert "fake-cookie-secret" not in str(error.value)


class BlockedListing:
    def __init__(self, fail_view=False, owner=123):
        self.calls = []
        self.fail_view, self.owner = fail_view, owner
    def uploads(self, uid, limit):
        raise BiliError("/x/space/wbi/arc/search 返回 -352")
    def stat(self, bvid):
        self.calls.append(bvid)
        if self.fail_view:
            raise BiliError("视频暂不可用")
        return {"bvid": bvid, "title": "已投稿的视频", "owner_mid": self.owner, "view": 321, "like": 12}


def test_bound_video_statistics_update_even_when_account_listing_is_blocked():
    b, errors = BlockedListing(), []
    bindings = {"BV1UGsg63E8Y": {"session": "20261111-2126", "rank": 6}}
    rows = fetch_my_uploads(b, "123", 30, [], bindings, errors)
    assert rows[0]["view"] == 321 and rows[0]["data_source"] == "bound_bv"
    assert rows[0]["stats_stale"] is False and b.calls == ["BV1UGsg63E8Y"]
    assert len(errors) == 1 and "-352" in errors[0]


def test_view_failure_preserves_cache_and_does_not_invent_zero_statistics():
    b, errors = BlockedListing(fail_view=True), []
    bindings = {"BV1UGsg63E8Y": {"session": "20261111-2126", "rank": 6},
                "BV1ab411c7de": {"session": "20261111-2126", "rank": 7}}
    rows = fetch_my_uploads(b, "123", 30, [{"bvid": "BV1UGsg63E8Y", "title": "旧标题", "view": 99}], bindings, errors)
    assert len(rows) == 1 and rows[0]["view"] == 99 and rows[0]["stats_stale"] is True


def test_bound_video_owner_must_match_configured_account_and_queries_are_bounded():
    b, errors = BlockedListing(owner=999), []
    bindings = {"BV1UGsg63E8Y": {"session": "20261111-2126", "rank": 6}}
    assert not fetch_my_uploads(b, "123", 30, [{"bvid": "BV1UGsg63E8Y", "view": 99}], bindings, errors)
    assert "UID" in errors[-1]
    b = BlockedListing()
    bindings = {f"BV{i:010d}": {"session": "20261111-2126", "rank": i + 1} for i in range(40)}
    assert len(fetch_my_uploads(b, "123", 100, [], bindings, [])) == 30
    assert len(b.calls) == 30
