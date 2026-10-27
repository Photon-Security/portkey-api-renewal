"""Tests for browser bearer-token extraction."""

from types import SimpleNamespace

from portkey_key_updater.get_bearer import extract_bearer_token_from_cookies, extract_cookies_from_jar


def test_extract_bearer_token_accepts_portkey_auth_token_cookie():
    token, cookie_name = extract_bearer_token_from_cookies(
        {
            "auth_token": {"value": "Bearer jwt-value"},
            "refresh_token": {"value": "refresh"},
        }
    )

    assert token == "jwt-value"
    assert cookie_name == "auth_token"


def test_extract_cookies_only_accepts_domains_applicable_to_exact_host(capsys):
    def cookie(name, domain):
        return SimpleNamespace(
            name=name,
            value="sentinel",
            domain=domain,
            path="/",
            secure=True,
        )

    cookies = extract_cookies_from_jar(
        [
            cookie("exact", ".portkey.example.com"),
            cookie("parent", ".example.com"),
            cookie("lookalike", "portkey.example.com.attacker.test"),
            cookie("sibling", "other.example.com"),
        ],
        "portkey.example.com",
    )

    assert set(cookies) == {"exact", "parent"}
