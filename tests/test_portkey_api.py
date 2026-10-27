"""Tests for Portkey API helper behavior."""

from datetime import datetime, timezone
from types import SimpleNamespace

from portkey_key_updater import portkey_api


def _key(value, status="active", created_at="2026-06-01T00:00:00.000Z", expires_at=None):
    return {
        "key": value,
        "status": status,
        "created_at": created_at,
        "expires_at": expires_at,
    }


def test_select_latest_active_unexpired_key_uses_created_at():
    now = datetime(2026, 6, 2, tzinfo=timezone.utc)
    keys = [
        _key("pk-old", created_at="2026-05-30T00:00:00.000Z"),
        _key("pk-new", created_at="2026-06-01T00:00:00.000Z"),
    ]

    assert portkey_api.select_latest_active_key(keys, now=now)["key"] == "pk-new"


def test_select_latest_active_key_ignores_expired_and_inactive_keys():
    now = datetime(2026, 6, 2, tzinfo=timezone.utc)
    keys = [
        _key("pk-revoked", status="revoked", created_at="2026-06-03T00:00:00.000Z"),
        _key("pk-expired", created_at="2026-06-02T00:00:00.000Z", expires_at="2026-06-01T00:00:00.000Z"),
        _key("pk-valid", created_at="2026-05-31T00:00:00.000Z", expires_at="2026-06-03T00:00:00.000Z"),
    ]

    assert portkey_api.select_latest_active_key(keys, now=now)["key"] == "pk-valid"


def test_select_latest_active_key_treats_null_expiry_as_unexpired():
    now = datetime(2026, 6, 2, tzinfo=timezone.utc)

    assert portkey_api.select_latest_active_key([_key("pk-no-expiry")], now=now)["key"] == "pk-no-expiry"


def test_select_latest_active_key_returns_none_for_empty_data():
    assert portkey_api.select_latest_active_key([]) is None


def test_create_api_key_sends_configured_portkey_payload(monkeypatch):
    calls = {}
    config = {
        "oauth": {
            "base_url": "https://portkey.example.com",
            "create_api_key_endpoint": "/albus/v2/api-keys/workspace/user",
            "key_name": "PortKey_NonProd",
            "key_description": "",
            "scopes": ["completions.write"],
            "organisation_id": "org-id",
            "workspace_id": "workspace-id",
            "key_type": "workspace",
            "expires_at": None,
            "defaults": {"config_id": None, "metadata": None},
            "rotation_policy": None,
        },
        "headers": {
            "content_type": "application/json",
            "accept": "application/json, text/plain, */*",
            "accept_language": "en-AU,en;q=0.9",
            "accept_encoding": "gzip, deflate, br",
            "connection": "keep-alive",
            "user_agent": "pytest",
        },
        "timeouts": {"api_request": 5},
    }

    def fake_post(url, json, headers, cookies, timeout, allow_redirects):
        calls.update(
            url=url,
            json=json,
            headers=headers,
            cookies=cookies,
            timeout=timeout,
            allow_redirects=allow_redirects,
        )
        return SimpleNamespace(status_code=201, json=lambda: {"id": "id", "key": "pk-created", "object": "api-key"})

    monkeypatch.setattr(portkey_api.requests, "post", fake_post)

    success, api_key, error = portkey_api.create_api_key(config, "token", {"token": {"value": "cookie-token"}})

    assert success is True
    assert api_key == "pk-created"
    assert error is None
    assert calls["url"] == "https://portkey.example.com/albus/v2/api-keys/workspace/user"
    assert calls["json"]["workspace_id"] == "workspace-id"
    assert calls["json"]["organisation_id"] == "org-id"
    assert calls["json"]["scopes"] == ["completions.write"]
    assert calls["headers"]["Authorization"] == "Bearer token"
    assert calls["cookies"] == {"token": "cookie-token"}
    assert calls["allow_redirects"] is False


def test_list_api_keys_sanitises_authentication_error_and_classifies_it(monkeypatch):
    config = {
        "oauth": {
            "base_url": "https://portkey.example.com",
            "workspace_id": "workspace-id",
            "list_api_keys_endpoint": "/albus/v2/api-keys",
        },
        "headers": {
            "accept": "application/json",
            "accept_language": "en-AU",
            "accept_encoding": "gzip",
            "connection": "keep-alive",
            "user_agent": "pytest",
        },
        "timeouts": {"api_request": 5},
    }
    response = SimpleNamespace(
        status_code=401,
        json=lambda: {"data": {"message": "jwt expired", "errorCode": "AB05", "secret": "do-not-echo"}},
    )
    monkeypatch.setattr(portkey_api.requests, "get", lambda *args, **kwargs: response)

    success, keys, error = portkey_api.list_api_keys(config, "expired-token", {})

    assert success is False
    assert keys == []
    assert error == "HTTP 401 (AB05)"
    assert "do-not-echo" not in error
    assert portkey_api.is_authentication_error(error) is True


class _Response:
    """Minimal stand-in for a requests.Response."""

    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _inference_config(**overrides):
    inference = {
        "base_url": "https://gateway.example.com/v1",
        "chat_endpoint": "/chat/completions",
        "model": "test-model",
        "timeout": 5,
    }
    inference.update(overrides)
    return {"inference": inference}


def _capture_post(monkeypatch, response):
    """Patch requests.post to record the call and return a canned response."""
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, json=json, headers=headers, timeout=timeout)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(portkey_api.requests, "post", fake_post)
    return seen


def test_inference_probe_spends_one_token_on_the_configured_model(monkeypatch):
    seen = _capture_post(monkeypatch, _Response(200))

    status, detail, error = portkey_api.verify_api_key_inference(_inference_config(), "pk-live")

    assert (status, error) == ("valid", None)
    assert "inference returned 200" in detail
    assert seen["url"] == "https://gateway.example.com/v1/chat/completions"
    assert seen["json"]["model"] == "test-model"
    assert seen["json"]["max_tokens"] == 1
    assert seen["timeout"] == 5


def test_inference_probe_sends_the_key_only_in_the_portkey_header(monkeypatch):
    seen = _capture_post(monkeypatch, _Response(200))

    status, detail, _ = portkey_api.verify_api_key_inference(_inference_config(), "pk-live")

    assert seen["headers"]["x-portkey-api-key"] == "pk-live"
    assert "Authorization" not in seen["headers"]
    # An empty x-portkey-config is rejected by the chat endpoint with HTTP 400.
    assert "x-portkey-config" not in seen["headers"]
    # The key must never reach the payload, the URL, or anything reported back to the caller.
    assert "pk-live" not in str(seen["json"]) and "pk-live" not in seen["url"]
    assert "pk-live" not in f"{status}{detail}"


def test_inference_probe_reports_unauthorized_so_renewal_can_proceed(monkeypatch):
    _capture_post(monkeypatch, _Response(401, {"data": {"errorCode": "AB05"}}))

    status, detail, error = portkey_api.verify_api_key_inference(_inference_config(), "pk-dead")

    assert status == "unauthorized"
    assert detail is None
    assert error.startswith("HTTP 401")


def test_non_authentication_failure_still_proves_the_key_authenticated(monkeypatch):
    # A retired model answers HTTP 404: the request got past authentication, so the key is fine.
    _capture_post(monkeypatch, _Response(404))

    status, detail, error = portkey_api.verify_api_key_inference(_inference_config(), "pk-live")

    assert (status, error) == ("valid", None)
    assert "authenticated (HTTP 404)" in detail


def test_unreachable_gateway_is_distinct_from_a_rejected_key(monkeypatch):
    _capture_post(monkeypatch, portkey_api.requests.ConnectionError("boom"))

    status, detail, error = portkey_api.verify_api_key_inference(_inference_config(), "pk-live")

    assert (status, detail) == ("unreachable", None)
    assert error == "ConnectionError"


def test_inference_settings_prefer_config_then_environment_then_default(monkeypatch):
    monkeypatch.setenv("AIPE_API_BASE", "https://from-env.example.com/v1")

    assert portkey_api.inference_settings(_inference_config())["base_url"] == "https://gateway.example.com/v1"
    assert portkey_api.inference_settings({})["base_url"] == "https://from-env.example.com/v1"

    monkeypatch.delenv("AIPE_API_BASE")
    assert portkey_api.inference_settings({})["base_url"] == portkey_api.INFERENCE_DEFAULTS["base_url"]
    assert portkey_api.inference_settings({})["model"] == portkey_api.INFERENCE_DEFAULTS["model"]
