"""Regression tests for Portkey browser-session refresh behavior."""

import base64
import builtins
import json
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from portkey_key_updater import check_key


def _jwt(exp):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def test_bearer_token_is_expired_uses_jwt_expiry_without_printing_token():
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)

    assert check_key.bearer_token_is_expired(_jwt(now.timestamp() - 1), now=now) is True
    assert check_key.bearer_token_is_expired(_jwt(now.timestamp() + 1), now=now) is False
    assert check_key.bearer_token_is_expired("opaque-token", now=now) is None


def test_expired_jwt_refreshes_before_first_api_request(monkeypatch):
    calls = []
    old_token = _jwt(1)
    fresh_token = _jwt(4_000_000_000)
    fresh_cookies = {"auth_token": {"value": fresh_token}}

    monkeypatch.setattr(
        check_key,
        "_refresh_browser_session",
        lambda config, browser_info, previous_token=None: (fresh_token, fresh_cookies),
    )

    def fake_list(config, token, cookies):
        calls.append((token, cookies))
        return True, [{"key": "pk-safe"}], None

    monkeypatch.setattr(check_key, "list_api_keys", fake_list)

    result = check_key._validate_or_refresh_session(
        {"oauth": {"base_url": "https://portkey.example.com"}},
        {"bundle_id": "com.microsoft.edgemac", "name": "Edge"},
        old_token,
        {"auth_token": {"value": old_token}},
    )

    assert result == (True, fresh_token, fresh_cookies, [{"key": "pk-safe"}])
    assert calls == [(fresh_token, fresh_cookies)]


def test_server_401_triggers_one_login_and_one_validated_retry(monkeypatch):
    calls = []
    fresh_cookies = {"auth_token": {"value": "fresh-token"}}
    monkeypatch.setattr(
        check_key,
        "_refresh_browser_session",
        lambda config, browser_info, previous_token=None: ("fresh-token", fresh_cookies),
    )

    def fake_list(config, token, cookies):
        calls.append(token)
        if token == "old-token":
            return False, [], "HTTP 401 (AB05)"
        return True, [], None

    monkeypatch.setattr(check_key, "list_api_keys", fake_list)

    result = check_key._validate_or_refresh_session(
        {"oauth": {"base_url": "https://portkey.example.com"}},
        {"bundle_id": "com.microsoft.edgemac", "name": "Edge"},
        "old-token",
        {"auth_token": {"value": "old-token"}},
    )

    assert result == (True, "fresh-token", fresh_cookies, [])
    assert calls == ["old-token", "fresh-token"]


def test_cookie_propagation_wait_enforces_25_seconds(monkeypatch, capsys):
    sleeps = []
    monkeypatch.setattr(check_key.time, "sleep", sleeps.append)

    check_key._wait_for_cookie_propagation()

    captured = capsys.readouterr()
    assert check_key.COOKIE_PROPAGATION_WAIT_SECONDS == 25
    assert sleeps == [1] * 25
    assert "25s" in captured.err
    assert "wait complete" in captured.err


def test_refresh_opens_declared_login_and_waits_for_changed_cookie(monkeypatch):
    opened = []
    sessions = iter(
        [
            ("old-token", "auth_token", {"auth_token": {"value": "old-token"}}),
            ("fresh-token", "auth_token", {"auth_token": {"value": "fresh-token"}}),
        ]
    )
    monkeypatch.setattr(check_key, "open_browser", lambda url: opened.append(url) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(check_key.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(builtins, "input", lambda prompt: "")
    monkeypatch.setattr(check_key, "_read_browser_session", lambda *args: next(sessions))

    result = check_key._refresh_browser_session(
        {"oauth": {"base_url": "https://portkey.example.com/"}},
        {"bundle_id": "com.microsoft.edgemac", "name": "Edge"},
        previous_token="old-token",
        max_attempts=2,
        retry_delay=0,
        cookie_wait_seconds=0,
    )

    assert result == ("fresh-token", {"auth_token": {"value": "fresh-token"}})
    assert opened == ["https://portkey.example.com"]


def test_non_authentication_failure_does_not_open_login(monkeypatch):
    monkeypatch.setattr(check_key, "list_api_keys", lambda *args: (False, [], "HTTP 500"))
    monkeypatch.setattr(
        check_key,
        "_refresh_browser_session",
        lambda *args: (_ for _ in ()).throw(AssertionError("login must not open")),
    )

    result = check_key._validate_or_refresh_session(
        {"oauth": {"base_url": "https://portkey.example.com"}},
        {"bundle_id": "com.microsoft.edgemac", "name": "Edge"},
        "opaque-token",
        {"auth_token": {"value": "opaque-token"}},
    )

    assert result[0] is False


def _stub_main_side_effects(monkeypatch, argv):
    """Neutralize logging, Keychain writes, clipboard and env analysis around main()."""
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(check_key, "load_config", lambda: {"oauth": {"base_url": "https://portkey.example.com"}})
    monkeypatch.setattr(check_key, "log_start", lambda *a, **k: None)
    monkeypatch.setattr(check_key, "log_end", lambda *a, **k: None)
    monkeypatch.setattr(check_key, "log_info", lambda *a, **k: None)
    monkeypatch.setattr(check_key, "log_success", lambda *a, **k: None)
    monkeypatch.setattr(check_key, "copy_to_clipboard", lambda key: True)
    monkeypatch.setattr(check_key, "run_environment_analysis", lambda key: None)

    forbidden = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("browser session must not be touched when the key already works")
    )
    monkeypatch.setattr(check_key, "_extract_session_token_and_cookies", forbidden)

    stored = []
    monkeypatch.setattr(check_key, "update_keychain", lambda key: stored.append(key))
    return stored


def test_working_key_short_circuits_before_any_browser_work(monkeypatch):
    # The whole point: an API key lives a month, the bearer minutes. A live key must not cost a login.
    stored = _stub_main_side_effects(monkeypatch, ["portkey-check"])
    monkeypatch.setattr(check_key, "read_existing_api_key", lambda: ("pk-live", "Keychain service X"))
    monkeypatch.setattr(
        check_key, "verify_api_key_inference", lambda config, key: ("valid", "inference returned 200", None)
    )

    with pytest.raises(SystemExit) as exit_info:
        check_key.main()

    assert exit_info.value.code == 0
    assert stored == ["pk-live"]


def test_rejected_key_falls_through_to_the_browser_path(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["portkey-check"])
    monkeypatch.setattr(check_key, "load_config", lambda: {"oauth": {"base_url": "https://portkey.example.com"}})
    for name in ("log_start", "log_end", "log_info", "log_success"):
        monkeypatch.setattr(check_key, name, lambda *a, **k: None)
    monkeypatch.setattr(check_key, "read_existing_api_key", lambda: ("pk-dead", "Keychain service X"))
    monkeypatch.setattr(
        check_key, "verify_api_key_inference", lambda config, key: ("unauthorized", None, "HTTP 401")
    )

    reached = []

    def _browser(config):
        reached.append("browser")
        raise SystemExit(1)

    monkeypatch.setattr(check_key, "_extract_session_token_and_cookies", _browser)

    with pytest.raises(SystemExit):
        check_key.main()

    assert reached == ["browser"]


def test_force_login_skips_the_probe_entirely(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["portkey-check", "--force-login"])
    monkeypatch.setattr(check_key, "load_config", lambda: {"oauth": {"base_url": "https://portkey.example.com"}})
    for name in ("log_start", "log_end", "log_info", "log_success"):
        monkeypatch.setattr(check_key, name, lambda *a, **k: None)
    monkeypatch.setattr(
        check_key,
        "verify_api_key_inference",
        lambda *a: (_ for _ in ()).throw(AssertionError("--force-login must not probe")),
    )
    monkeypatch.setattr(check_key, "_extract_session_token_and_cookies", lambda config: ("t", {}, {}))
    monkeypatch.setattr(check_key, "_validate_or_refresh_session", lambda *a: (False, None, None, None))

    with pytest.raises(SystemExit):
        check_key.main()


def test_unreachable_gateway_keeps_the_existing_key_instead_of_forcing_mfa(monkeypatch):
    stored = _stub_main_side_effects(monkeypatch, ["portkey-check"])
    monkeypatch.setattr(check_key, "read_existing_api_key", lambda: ("pk-live", "Keychain service X"))
    monkeypatch.setattr(
        check_key, "verify_api_key_inference", lambda config, key: ("unreachable", None, "ProxyError")
    )

    with pytest.raises(SystemExit) as exit_info:
        check_key.main()

    assert exit_info.value.code == 0
    assert stored == ["pk-live"]


def test_probe_never_prints_the_api_key(monkeypatch, capsys):
    monkeypatch.setattr(check_key, "read_existing_api_key", lambda: ("pk-secret-value-1234", "Keychain"))
    monkeypatch.setattr(check_key, "log_success", lambda *a, **k: None)
    monkeypatch.setattr(
        check_key, "verify_api_key_inference", lambda config, key: ("valid", "inference returned 200", None)
    )

    check_key.probe_existing_api_key({})
    captured = capsys.readouterr()

    assert "pk-secret-value-1234" not in captured.out + captured.err


def _jar(token):
    return {"auth_token": {"value": token}} if token else {}


def test_other_browsers_are_tried_when_default_has_no_session(monkeypatch):
    from portkey_key_updater import get_bearer

    tried = []

    def fake_cookies(browser_id, domain):
        tried.append(browser_id)
        return _jar("edge-token") if browser_id == "com.microsoft.edgemac" else {}

    monkeypatch.setattr(get_bearer, "get_browser_cookies_for_domain", fake_cookies)

    result = check_key._find_session_in_other_browsers(
        {"oauth": {"base_url": "https://portkey.example.com"}},
        {"bundle_id": "com.google.chrome", "name": "Google Chrome"},
    )

    assert result == ("edge-token", _jar("edge-token"))
    assert tried == ["com.microsoft.edgemac"]


def test_finder_skips_default_unreadable_and_unusable_browsers(monkeypatch):
    from portkey_key_updater import get_bearer

    def fake_cookies(browser_id, domain):
        if browser_id == "com.microsoft.edgemac":
            raise RuntimeError("keychain denied")
        if browser_id == "com.brave.Browser":
            return _jar("same-as-old")
        if browser_id == "org.mozilla.firefox":
            return _jar("fresh")
        raise AssertionError(f"unexpected browser {browser_id}")

    monkeypatch.setattr(get_bearer, "get_browser_cookies_for_domain", fake_cookies)

    token, cookies, browser = get_bearer.find_session_in_other_browsers(
        "portkey.example.com",
        "com.google.chrome",
        lambda token: token != "same-as-old",
    )

    assert token == "fresh"
    assert browser["name"] == "Firefox"


def test_finder_returns_none_when_no_other_browser_has_a_session(monkeypatch):
    from portkey_key_updater import get_bearer

    monkeypatch.setattr(get_bearer, "get_browser_cookies_for_domain", lambda *a: {})

    assert get_bearer.find_session_in_other_browsers("portkey.example.com", "com.google.chrome") is None


def test_refresh_accepts_session_created_in_another_browser(monkeypatch):
    monkeypatch.setattr(check_key, "open_browser", lambda url: SimpleNamespace(returncode=0))
    monkeypatch.setattr(check_key.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(builtins, "input", lambda prompt: "")
    monkeypatch.setattr(check_key, "_read_browser_session", lambda *args: (None, None, {}))
    monkeypatch.setattr(
        check_key,
        "_find_session_in_other_browsers",
        lambda config, info, previous=None: ("firefox-token", _jar("firefox-token")),
    )

    result = check_key._refresh_browser_session(
        {"oauth": {"base_url": "https://portkey.example.com"}},
        {"bundle_id": "com.google.chrome", "name": "Google Chrome"},
        previous_token="old",
        max_attempts=1,
        retry_delay=0,
        cookie_wait_seconds=0,
    )

    assert result == ("firefox-token", _jar("firefox-token"))
