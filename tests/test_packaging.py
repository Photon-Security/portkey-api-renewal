"""Packaging regression tests."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
SRC = PROJECT_ROOT / "src"

if str(PROJECT_ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(PROJECT_ROOT))

if str(SRC) not in os.sys.path:
    os.sys.path.insert(0, str(SRC))

import main
from portkey_key_updater import utils


def test_load_config_prefers_current_working_directory(tmp_path, monkeypatch):
    """The packaged code should load config/config.json from the active project root."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.json"
    config_file.write_text('{"oauth": {"base_url": "https://example.com"}}')

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PORTKEY_KEY_UPDATER_CONFIG", raising=False)
    monkeypatch.delenv("LITELLM_KEY_UPDATER_CONFIG", raising=False)

    assert utils.load_config()["oauth"]["base_url"] == "https://example.com"


def test_root_directory_has_no_python_wrapper_scripts():
    """The root should expose only the intentional dispatcher entrypoint."""
    root_python_files = sorted(path.name for path in PROJECT_ROOT.glob("*.py"))

    assert root_python_files == ["main.py"]


def test_root_dispatcher_exposes_expected_commands():
    """The root dispatcher should advertise the supported short subcommands."""
    assert set(main.COMMANDS) == {"check", "renew", "bearer", "analyse", "sync", "report"}


def test_root_dispatcher_help_lists_command_descriptions():
    """Top-level help should explain what each positional command does."""
    help_text = main.build_parser().format_help()

    assert "check" in help_text and "Validate the current API key" in help_text
    assert "renew" in help_text and "Generate or renew the API key" in help_text
    assert "report" in help_text and "Generate the security report" in help_text


def test_pyproject_exposes_portkey_and_legacy_console_scripts():
    """Console scripts should include Portkey-first names and legacy aliases."""
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text()

    assert 'portkey-check = "portkey_key_updater.check_key:main"' in pyproject
    assert 'portkey-renew = "portkey_key_updater.renew_key:main"' in pyproject
    assert 'portkey-get-bearer = "portkey_key_updater.get_bearer:main"' in pyproject
    assert 'check-key = "portkey_key_updater.check_key:main"' in pyproject
    assert 'renew-key = "portkey_key_updater.renew_key:main"' in pyproject


def test_runtime_paths_resolve_to_config_and_logs_directories():
    """Default runtime paths should live under config/ and logs/."""
    assert utils.get_runtime_config_path() == PROJECT_ROOT / "config" / "config.json"
    assert utils.get_config_template_path() == PROJECT_ROOT / "config" / "config.template.json"
    assert utils.get_log_file_path() == PROJECT_ROOT / "logs" / "portkey-key-updater.log"
    assert utils.get_security_report_path() == PROJECT_ROOT / "logs" / "security_report.html"


def test_core_modules_exist_in_src_package():
    """Operational modules should live under the src package."""
    package_dir = SRC / "portkey_key_updater"
    expected_modules = {
        "__init__.py",
        "analyse_env.py",
        "check_key.py",
        "get_bearer.py",
        "logger.py",
        "portkey_api.py",
        "renew_key.py",
        "report.py",
        "update_secretmgr.py",
        "utils.py",
    }

    assert expected_modules.issubset({path.name for path in package_dir.glob("*.py")})


def test_package_version_is_single_sourced_and_matches_changelog():
    """pyproject must read its version from the package, and the changelog must document it."""
    import re

    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text()
    changelog = (PROJECT_ROOT / "CHANGELOG.md").read_text()

    assert 'dynamic = ["version"]' in pyproject
    assert re.fullmatch(r"\d+\.\d+\.\d+", __import__("portkey_key_updater").__version__)
    assert f"## [{__import__('portkey_key_updater').__version__}] - " in changelog


def test_license_file_is_mit():
    """The repository should ship the MIT license it declares in pyproject.toml."""
    assert (PROJECT_ROOT / "LICENSE").read_text().startswith("MIT License")
    assert 'license = { text = "MIT" }' in (PROJECT_ROOT / "pyproject.toml").read_text()


def test_version_flag_reports_the_product_name_not_the_command(monkeypatch, capsys):
    """--version should read the same from every entrypoint, whatever script was invoked."""
    import pytest

    from portkey_key_updater import __app_name__, __version__, check_key

    expected = f"portkey-key-renewal {__version__}"
    assert __app_name__ == "portkey-key-renewal"

    for argv in (["portkey-check", "--version"], ["check-key", "--version"]):
        monkeypatch.setattr(os.sys, "argv", argv)
        with pytest.raises(SystemExit) as exit_info:
            check_key.main()
        assert exit_info.value.code == 0
        assert capsys.readouterr().out.strip() == expected

    with pytest.raises(SystemExit) as exit_info:
        main.build_parser().parse_args(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == expected
