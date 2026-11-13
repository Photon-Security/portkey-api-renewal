"""Exercise the published setup, installer and helpers with isolated local files."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not shutil.which("zsh") or not shutil.which("rsync"), reason="requires installer tools")
def test_setup_installs_both_gateways_and_loads_commands_without_local_adapter(tmp_path):
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as target:
        for name in ("pyproject.toml", "config/config.template.json", "scripts/portkey-shell.sh",
                     "src/portkey_key_updater/__init__.py", "src/portkey_key_updater/check_key.py",
                     "src/portkey_key_updater/get_bearer.py", "src/portkey_key_updater/renew_key.py",
                     "src/portkey_key_updater/utils.py"):
            target.write(ROOT / name, "source/" + name)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real_curl = shutil.which("curl")
    scripts = {
        "curl": f'''#!/bin/sh
case "$*" in
  *raw.githubusercontent.com*) cat "$TEST_INSTALLER" ;;
  *) exec "{real_curl}" "$@" ;;
esac
''',
        "uv": '''#!/bin/sh
if [ "$1" = venv ]; then
  mkdir -p .venv/bin
  ln -s "$TEST_PYTHON" .venv/bin/python
else
  cat > .venv/bin/portkey-check <<'CHECK'
#!/bin/sh
"$TEST_PYTHON" - <<'PY'
import json, os
from pathlib import Path
cfg = json.loads(Path(os.environ['PORTKEY_KEY_UPDATER_CONFIG']).read_text())
with open(os.environ['TEST_TRACE'], 'a') as f:
    f.write(json.dumps([cfg['oauth']['base_url'], os.environ['PORTKEY_KEYCHAIN_SERVICE']]) + '\\n')
PY
CHECK
  chmod +x .venv/bin/portkey-check
fi
''',
        "security": "#!/bin/sh\nprintf 'fixture-key\\n'\n",
    }
    for name, body in scripts.items():
        p = bin_dir / name
        p.write_text(body)
        p.chmod(0o700)
    env = os.environ.copy()
    for name in list(env):
        if name.startswith("PORTKEY_"):
            env.pop(name)
    env.update(PATH=f"{bin_dir}:{env['PATH']}", PORTKEY_RENEWER_HOME=str(tmp_path / 'apps'),
               PORTKEY_ZIP_URL=archive.as_uri(), TEST_INSTALLER=str(ROOT / 'install.sh'),
               TEST_PYTHON=shutil.which('python3'), TEST_TRACE=str(tmp_path / 'trace'))
    # Run both setup choices and an update, then check both from an unrelated cwd.
    script = '''source "$1" <<< $'2\nhttps://new.example.test\nworkspace-new\norganisation-new\nhttps://inference-new.example.test/v1\n@example/model' || exit $?
setup_portkey_api_renewer <<< $'1\nhttps://legacy.example.test\nworkspace-legacy\norganisation-legacy\nhttps://inference-legacy.example.test/v1\n@example/model' || exit $?
setup_portkey_api_renewer <<< $'2\nhttps://new.example.test\nworkspace-new\norganisation-new\nhttps://inference-new.example.test/v1\n@example/model' || exit $?
check_portkey_api || exit $?
check_portkey_api_legacy || exit $?
[[ "$PORTKEY_API_KEY" = fixture-key && "$PORTKEY_API_KEY_LEGACY" = fixture-key ]] || exit 3
'''
    result = subprocess.run(['zsh', '-f', '-c', script, 'test', str(ROOT / 'scripts/setup-portkey.sh')],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    traces = [json.loads(line) for line in (tmp_path / 'trace').read_text().splitlines()]
    assert traces == [['https://new.example.test', 'PORTKEY_API_KEY'],
                      ['https://legacy.example.test', 'PORTKEY_API_KEY_LEGACY']]
    new_dir = tmp_path / 'apps/Portkey-API-renewal'
    legacy_dir = tmp_path / 'apps/Portkey-API-renewal-legacy'
    assert len(list((new_dir / 'config').glob('config.json.backup.*'))) == 1
    assert json.loads((legacy_dir / 'config/config.json').read_text())['oauth']['base_url'] == 'https://legacy.example.test'
    assert (new_dir / 'src/portkey_key_updater/utils.py').read_text() == (ROOT / 'src/portkey_key_updater/utils.py').read_text()
    assert 'fixture-key' not in result.stdout + result.stderr


def test_shell_helper_reports_missing_gateway_without_running_checker(tmp_path):
    env = {**os.environ, 'PORTKEY_RENEWER_HOME': str(tmp_path)}
    result = subprocess.run(['zsh', '-f', '-c', 'source "$1"; check_portkey_api_legacy',
                             'test', str(ROOT / 'scripts/portkey-shell.sh')], env=env,
                            capture_output=True, text=True)
    assert result.returncode == 1
    assert 'Install the legacy gateway' in result.stderr
