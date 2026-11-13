"""Tests for browser bearer-token extraction."""

from types import SimpleNamespace
import sqlite3
import sys

import browser_cookie3
import pytest

from portkey_key_updater import get_bearer

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


def _cookie_db(path, token=None, schema=24):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE meta (key TEXT, value TEXT)')
        db.execute('INSERT INTO meta VALUES ("version", ?)', (str(schema),))
        db.execute('CREATE TABLE cookies (host_key TEXT, path TEXT, is_secure INTEGER, expires_utc INTEGER, name TEXT, value TEXT, encrypted_value BLOB, is_httponly INTEGER)')
        if token:
            db.execute('INSERT INTO cookies VALUES (?, ?, ?, ?, ?, ?, ?, ?)', ('portkey.example.com', '/', 1, 0, 'auth_token', token, b'', 1))


@pytest.mark.parametrize('browser_id, reader, folder', [
    ('com.google.chrome', 'chrome', 'Google/Chrome'),
    ('com.microsoft.edgemac', 'edge', 'Microsoft Edge'),
])
@pytest.mark.parametrize('layout', ['Cookies', 'Network/Cookies'])
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS browser cookie stores")
def test_reads_session_from_work_profile(monkeypatch, tmp_path, browser_id, reader, folder, layout):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setattr(browser_cookie3, '_get_osx_keychain_password', lambda *a: b'fixture-password')
    root = tmp_path / 'Library/Application Support' / folder
    _cookie_db(root / 'Default/Cookies')
    _cookie_db(root / 'Profile 1' / layout, 'fixture-bearer')
    cookies = get_bearer.get_browser_cookies_for_domain(browser_id, 'portkey.example.com')
    assert get_bearer.extract_bearer_token_from_cookies(cookies) == ('fixture-bearer', 'auth_token')


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS browser cookie stores")
def test_decryption_failure_is_reported_without_exception_contents(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv('HOME', str(tmp_path))
    _cookie_db(tmp_path / 'Library/Application Support/Google/Chrome/Default/Cookies')
    def denied(**kwargs):
        raise browser_cookie3.BrowserCookieError('Unable to get key for cookie decryption SECRET_SENTINEL')
    monkeypatch.setattr(browser_cookie3, 'chrome', denied)
    assert get_bearer.get_browser_cookies_for_domain('com.google.chrome', 'portkey.example.com') == {}
    output = capsys.readouterr().out
    assert 'Safe Storage' in output
    assert 'SECRET_SENTINEL' not in output


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS browser cookie stores")
def test_parent_domain_bearer_is_read_even_when_host_has_load_balancer_cookies(monkeypatch, tmp_path):
    monkeypatch.setenv('HOME', str(tmp_path))
    _cookie_db(tmp_path / 'Library/Application Support/Google/Chrome/Default/Cookies')
    def reader(**kwargs):
        domain = kwargs['domain_name']
        name = 'auth_token' if domain == 'example.com' else 'AWSALB'
        return [SimpleNamespace(name=name, value='fixture', domain='.example.com' if name == 'auth_token' else 'portkey.example.com', path='/', secure=True)]
    monkeypatch.setattr(browser_cookie3, 'chrome', reader)
    cookies = get_bearer.get_browser_cookies_for_domain('com.google.chrome', 'portkey.example.com')
    assert get_bearer.extract_bearer_token_from_cookies(cookies)[0] == 'fixture'


@pytest.mark.parametrize('schema', [23, 24])
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS cookie encryption")
def test_encrypted_chromium_cookie_handles_domain_hash(monkeypatch, tmp_path, schema):
    from Cryptodome.Cipher import AES
    from Cryptodome.Protocol.KDF import PBKDF2
    from Cryptodome.Util.Padding import pad
    from hashlib import sha256

    monkeypatch.setenv('HOME', str(tmp_path))
    password = b'fixture-safe-storage'
    monkeypatch.setattr(browser_cookie3, '_get_osx_keychain_password', lambda *a: password)
    path = tmp_path / 'Library/Application Support/Microsoft Edge/Default/Cookies'
    _cookie_db(path, schema=schema)
    domain = 'portkey.example.com'
    plaintext = b'fixture-encrypted-bearer'
    if schema >= 24:
        plaintext = sha256(domain.encode()).digest() + plaintext
    key = PBKDF2(password, b'saltysalt', 16, 1003)
    encrypted = b'v10' + AES.new(key, AES.MODE_CBC, b' ' * 16).encrypt(pad(plaintext, 16))
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO cookies VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (domain, '/', 1, 0, 'auth_token', '', encrypted, 1))
    cookies = get_bearer.get_browser_cookies_for_domain('com.microsoft.edgemac', domain)
    assert get_bearer.extract_bearer_token_from_cookies(cookies)[0] == 'fixture-encrypted-bearer'


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS browser cookie stores")
def test_current_profile_beats_expired_profile_without_mixing_cookies(monkeypatch, tmp_path):
    import base64
    import json
    def jwt(exp):
        payload = base64.urlsafe_b64encode(json.dumps({'exp': exp}).encode()).decode().rstrip('=')
        return f'header.{payload}.signature'
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setattr(browser_cookie3, '_get_osx_keychain_password', lambda *a: b'fixture-password')
    root = tmp_path / 'Library/Application Support/Google/Chrome'
    _cookie_db(root / 'Default/Cookies', jwt(1))
    _cookie_db(root / 'Profile 1/Cookies', jwt(4_000_000_000))
    with sqlite3.connect(root / 'Default/Cookies') as db:
        db.execute('INSERT INTO cookies VALUES (?, ?, ?, ?, ?, ?, ?, ?)', ('portkey.example.com', '/', 1, 0, 'default-only', 'fixture', b'', 1))
    cookies = get_bearer.get_browser_cookies_for_domain('com.google.chrome', 'portkey.example.com')
    assert get_bearer.extract_bearer_token_from_cookies(cookies)[0] == jwt(4_000_000_000)
    assert 'default-only' not in cookies


def test_diagnostic_command_prints_no_cookie_values_and_never_calls_http_or_writes(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['portkey-get-bearer', '--diagnose-cookies'])
    monkeypatch.setattr(get_bearer, 'load_config', lambda: {'oauth': {'base_url': 'https://portkey.example.com'}})
    monkeypatch.setattr(get_bearer, '_cookie_files', lambda *a: [])
    monkeypatch.setattr(get_bearer, 'get_browser_cookies_for_domain', lambda *a, **k: {'auth_token': {'value': 'SECRET_SENTINEL'}})
    def forbidden(*a, **k):
        raise AssertionError('diagnostics must not call HTTP or write Keychain')
    monkeypatch.setattr(get_bearer, 'list_api_keys', forbidden)
    from portkey_key_updater import utils
    monkeypatch.setattr(utils, 'store_keychain_secret', forbidden)
    with pytest.raises(SystemExit) as exit_info:
        get_bearer.main()
    output = capsys.readouterr().out
    assert exit_info.value.code == 0
    assert 'auth_token' in output
    assert 'SECRET_SENTINEL' not in output
