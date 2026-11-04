"""Installer config replacement tests using a local source archive and isolated install."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest


INSTALLER = Path(__file__).resolve().parents[1] / "install.sh"
REQUIRED = (
    "pyproject.toml",
    "src/portkey_key_updater/check_key.py",
    "src/portkey_key_updater/get_bearer.py",
    "src/portkey_key_updater/renew_key.py",
    "src/portkey_key_updater/utils.py",
)
OVERRIDES = {
    "PORTKEY_OAUTH_BASE": "https://example.test",
    "PORTKEY_OAUTH_WORKSPACE_ID": "workspace",
    "PORTKEY_OAUTH_ORGANISATION_ID": "organisation",
    "PORTKEY_OAUTH_KEY_NAME": "PortKey",
    "PORTKEY_INFERENCE_BASE": "https://inference.example.test/v1",
    "PORTKEY_INFERENCE_MODEL": "@example/model",
}


def make_archive(tmp_path, template):
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as target:
        for name in REQUIRED:
            target.writestr(f"source/{name}", "")
        target.writestr("source/config/config.template.json", template)
    return archive


def run_installer(tmp_path, archive, overrides=None):
    install_dir = tmp_path / "installed"
    python = install_dir / ".venv/bin/python"
    python.parent.mkdir(parents=True, exist_ok=True)
    if not python.exists():
        python.symlink_to(shutil.which("python3"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    uv = bin_dir / "uv"
    uv.write_text("#!/bin/sh\nexit 0\n")
    uv.chmod(0o700)
    env = os.environ.copy()
    for name in OVERRIDES:
        env.pop(name, None)
    env.update({"PORTKEY_INSTALL_DIR": str(install_dir), "PORTKEY_ZIP_URL": archive.as_uri(), "PATH": f"{bin_dir}:{env['PATH']}"})
    env.update(overrides or {})
    result = subprocess.run(["/bin/zsh", str(INSTALLER)], cwd=tmp_path, env=env, text=True, capture_output=True)
    return install_dir, result


@pytest.mark.skipif(not shutil.which("rsync") or not shutil.which("zsh"), reason="requires macOS installer tools")
def test_installer_replaces_from_fresh_template_and_retains_unique_backups(tmp_path):
    archive = make_archive(tmp_path, json.dumps({"oauth": {"base_url": "template"}, "inference": {"model": "template"}}))
    install_dir, first = run_installer(tmp_path, archive, OVERRIDES)
    assert first.returncode == 0, first.stdout + first.stderr
    config_dir = install_dir / "config"
    config_file = config_dir / "config.json"
    initial = json.loads(config_file.read_text())
    assert initial["oauth"] == {"base_url": OVERRIDES["PORTKEY_OAUTH_BASE"], "workspace_id": "workspace", "organisation_id": "organisation", "key_name": "PortKey"}
    assert initial["inference"] == {"model": "@example/model", "base_url": OVERRIDES["PORTKEY_INFERENCE_BASE"]}
    assert config_file.stat().st_mode & 0o777 == 0o600
    logs = install_dir / "logs"
    logs.mkdir()
    (logs / "keep.txt").write_text("untouched")
    config_file.write_text('{"old": "user value"}')
    archive = make_archive(tmp_path, json.dumps({"oauth": {"base_url": "new template", "new_setting": "fresh"}, "inference": {"model": "new template"}}))
    install_dir_path, second = run_installer(tmp_path, archive, OVERRIDES)
    assert install_dir_path == install_dir
    assert second.returncode == 0, second.stdout + second.stderr
    backups = list(config_dir.glob("config.json.backup.*"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text()) == {"old": "user value"}
    assert str(backups[0]) in second.stdout
    assert backups[0].stat().st_mode & 0o777 == 0o600
    assert json.loads(config_file.read_text())["oauth"] == {**initial["oauth"], "new_setting": "fresh"}
    assert (logs / "keep.txt").read_text() == "untouched"
    _, third = run_installer(tmp_path, archive, OVERRIDES)
    assert third.returncode == 0, third.stdout + third.stderr
    assert len(list(config_dir.glob("config.json.backup.*"))) == 2
    assert backups[0].exists()


@pytest.mark.skipif(not shutil.which("rsync") or not shutil.which("zsh"), reason="requires macOS installer tools")
@pytest.mark.parametrize("bad_template,overrides", [('{"oauth":', OVERRIDES), (json.dumps({"oauth": {}, "inference": {}}), {"PORTKEY_OAUTH_BASE": "https://example.test"})])
def test_failed_preparation_preserves_existing_config(tmp_path, bad_template, overrides):
    archive = make_archive(tmp_path, bad_template)
    config_dir = tmp_path / "installed/config"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "config.json"
    config_file.write_text('{"existing": true}')
    _, result = run_installer(tmp_path, archive, overrides)
    assert result.returncode != 0
    assert config_file.read_text() == '{"existing": true}'
    assert list(config_dir.glob("config.json.backup.*")) == []
    assert list(config_dir.glob(".config.json.tmp.*")) == []


@pytest.mark.skipif(not shutil.which("rsync") or not shutil.which("zsh"), reason="requires macOS installer tools")
def test_installer_without_overrides_uses_template_values(tmp_path):
    template = {"oauth": {"base_url": "https://template.example"}, "inference": {"model": "@template/model"}}
    archive = make_archive(tmp_path, json.dumps(template))
    install_dir, result = run_installer(tmp_path, archive)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((install_dir / "config/config.json").read_text()) == template
    assert "Replaced config/config.json" in result.stdout
    assert "Config backup:" not in result.stdout
