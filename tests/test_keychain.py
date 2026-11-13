

from types import SimpleNamespace

import pytest

from portkey_key_updater import check_key, renew_key, utils


def test_update_keychain_uses_update_without_deleting_and_reports_failure(monkeypatch, capsys):
    commands = []
    def run(cmd, **kwargs):
        commands.append(cmd)
        if 'find-generic-password' in cmd:
            return SimpleNamespace(returncode=0, stdout='old-fixture', stderr='')
        return SimpleNamespace(returncode=1, stdout='', stderr='SECRET_SENTINEL')
    monkeypatch.setattr(utils.subprocess, 'run', run)
    assert check_key.update_keychain('new-fixture') is False
    assert not any('delete-generic-password' in cmd for cmd in commands)
    assert '-U' in commands[-1]
    assert 'SECRET_SENTINEL' not in capsys.readouterr().err


@pytest.mark.parametrize("storage_ok", [False, True])
def test_standalone_renew_saves_generated_key_and_fails_if_storage_fails(monkeypatch, storage_ok):
    monkeypatch.setattr(renew_key.sys, 'argv', ['portkey-renew', '--silent'])
    monkeypatch.setattr(renew_key, 'load_config', lambda: {})
    for name in ('log_start', 'log_end', 'log_info', 'log_success', 'log_error'):
        monkeypatch.setattr(renew_key, name, lambda *a, **k: None)
    monkeypatch.setattr(renew_key, '_extract_session_token_and_cookies', lambda *a: ('fixture-token', {}))
    monkeypatch.setattr(renew_key, 'request_api_key_with_token', lambda *a: (True, 'fixture-key'))
    stored = []
    monkeypatch.setattr(utils, 'store_keychain_secret', lambda secret, **kwargs: stored.append(secret) or storage_ok)
    monkeypatch.setattr(renew_key, 'copy_to_clipboard', lambda *a: True)
    with pytest.raises(SystemExit) as exit_info:
        renew_key.main()
    assert stored == ['fixture-key']
    assert exit_info.value.code == (0 if storage_ok else 1)


def test_already_stored_key_does_not_require_a_write(monkeypatch):
    def run(cmd, **kwargs):
        assert 'find-generic-password' in cmd
        assert '-a' in cmd
        return SimpleNamespace(returncode=0, stdout='fixture-key\n', stderr='')
    monkeypatch.setattr(utils.subprocess, 'run', run)
    assert check_key.update_keychain('fixture-key') is True


@pytest.mark.parametrize("service", ["PORTKEY_API_KEY", "PORTKEY_API_KEY_LEGACY"])
def test_profile_uses_selected_keychain_item_for_read_and_write(monkeypatch, service):
    monkeypatch.setenv("PORTKEY_KEYCHAIN_SERVICE", service)
    commands = []

    def run(cmd, **kwargs):
        commands.append(cmd)
        return SimpleNamespace(returncode=0, stdout='fixture-key\n', stderr='')

    monkeypatch.setattr(utils.subprocess, 'run', run)
    assert check_key.read_existing_api_key() == ('fixture-key', f'Keychain service {service}')
    assert utils.store_keychain_secret('replacement-fixture') is True
    assert len(commands) == 2
    for cmd in commands:
        assert cmd[cmd.index('-s') + 1] == service


def test_default_keychain_service_is_preserved(monkeypatch):
    monkeypatch.delenv("PORTKEY_KEYCHAIN_SERVICE", raising=False)
    assert utils.get_keychain_service_name() == "PORTKEY_API_KEY"


def test_legacy_profile_does_not_fall_back_to_new_gateway_environment(monkeypatch):
    monkeypatch.setenv("PORTKEY_KEYCHAIN_SERVICE", "PORTKEY_API_KEY_LEGACY")
    monkeypatch.setenv("PORTKEY_API_KEY", "new-gateway-fixture")
    monkeypatch.delenv("PORTKEY_API_KEY_LEGACY", raising=False)
    monkeypatch.setattr(utils.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1))
    assert check_key.read_existing_api_key() == (None, None)
