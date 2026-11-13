# Portkey API Key Updater

[![Release](https://img.shields.io/github/v/release/Photon-Security/portkey-api-renewal?style=flat-square)](https://github.com/Photon-Security/portkey-api-renewal/releases)
[![Package](https://img.shields.io/badge/package-portkey--renew--api--key-informational?style=flat-square)](pyproject.toml)
[![Images](https://img.shields.io/badge/images-included-brightgreen?style=flat-square)](assets/)
[![macOS](https://img.shields.io/badge/macOS-26.5_tested-green?style=flat-square)](https://www.apple.com/macos/)
[![zsh](https://img.shields.io/badge/zsh-5.9_tested-blue?style=flat-square)](https://www.zsh.org/)
[![Python](https://img.shields.io/badge/Python-3.12.13_tested-orange?style=flat-square)](https://www.python.org/)
[![Python Support](https://img.shields.io/badge/Python-3.8--3.12_supported-orange?style=flat-square)](https://www.python.org/)
[![Portkey](https://img.shields.io/badge/Portkey-Gateway-purple?style=flat-square)](https://portkey.ai/)
[![Tests](https://img.shields.io/github/actions/workflow/status/Photon-Security/portkey-api-renewal/changelog.yml?branch=main&label=checks&style=flat-square)](https://github.com/Photon-Security/portkey-api-renewal/actions/workflows/changelog.yml)

<p align="center">
  <img src="assets/Portkey_API_Logo.png" alt="Portkey API Key Updater Logo" width="160" />
</p>

Automate Portkey API key renewal on macOS. The tool reads your authenticated Portkey browser session, finds the newest active unexpired API key for a configured workspace, stores it in macOS Keychain, and creates a new key when no usable key exists.

## One-off setup (macOS)

The renewer checks an existing API key against the configured inference endpoint. If the key needs renewal, it uses your authenticated browser session to list or create a key through the gateway control plane, then stores the active key in macOS Keychain. A browser sign-in is only needed when the control-plane session is missing or expired.

Your control-plane URL, workspace and organisation IDs, inference URL, and model must all belong to the **same gateway**. Obtain these six settings from your gateway administrator. Do not put API keys or bearer tokens in the script.

### 1. Install or update

Paste this whole function into an interactive zsh terminal. The variables are declared at the top without preset values. The function asks for the six settings, installs `uv` if needed, downloads the installer, and passes your answers to it. It does not save those answers in your shell history or in this repository.

```sh
setup_portkey_api_renewer() {
  local oauth_base workspace_id organisation_id key_name infer_base infer_model installer
  printf 'Control-plane URL (https://...): '
  IFS= read -r oauth_base || return 1
  printf 'Workspace ID: '
  IFS= read -r workspace_id || return 1
  printf 'Organisation ID: '
  IFS= read -r organisation_id || return 1
  printf 'API key name: '
  IFS= read -r key_name || return 1
  printf 'Inference base URL (https://.../v1): '
  IFS= read -r infer_base || return 1
  printf 'Inference model: '
  IFS= read -r infer_model || return 1
  case "$oauth_base" in https://?*) ;; *) printf 'Enter an HTTPS control-plane URL.\n' >&2; return 1 ;; esac
  case "$infer_base" in https://?*) ;; *) printf 'Enter an HTTPS inference base URL.\n' >&2; return 1 ;; esac
  if [[ -z "$workspace_id" || -z "$organisation_id" || -z "$key_name" || -z "$infer_model" ]]; then
    printf 'All six settings are required.\n' >&2
    return 1
  fi
  command -v uv >/dev/null || { curl -LsSf https://astral.sh/uv/install.sh | sh || return 1; source "$HOME/.local/bin/env" || return 1; }
  installer="$(curl -fsSL https://raw.githubusercontent.com/Photon-Security/portkey-api-renewal/refs/heads/main/install.sh)" || return 1
  [[ "$installer" == *PORTKEY_CONFIG_OVERRIDES_V1* ]] || { printf 'The published installer does not support gateway settings. No installation was started.\n' >&2; return 1; }
  PORTKEY_OAUTH_BASE="$oauth_base" PORTKEY_OAUTH_WORKSPACE_ID="$workspace_id" PORTKEY_OAUTH_ORGANISATION_ID="$organisation_id" PORTKEY_OAUTH_KEY_NAME="$key_name" PORTKEY_INFERENCE_BASE="$infer_base" PORTKEY_INFERENCE_MODEL="$infer_model" /bin/zsh -c "$installer"
}
setup_portkey_api_renewer
```

The installer uses `~/Applications/Portkey-API-renewal` by default; set `PORTKEY_INSTALL_DIR` before running the function if you need another location. On every successful install or update, it backs up any existing `config/config.json` to a unique `config/config.json.backup.*` file and replaces it with a config built from the freshly installed template and the six settings you entered. It keeps `logs/` and earlier backups. Existing config values are not silently merged back in.

### 2. Load the key in new terminal sessions

Add these lines to `~/.zshrc` (adjust the command path if you changed `PORTKEY_INSTALL_DIR`):

```sh
alias check_portkey_api="$HOME/Applications/Portkey-API-renewal/.venv/bin/portkey-check"
export PORTKEY_API_KEY="$(security find-generic-password -s PORTKEY_API_KEY -w 2>/dev/null)"
```

Open a new terminal or run `source ~/.zshrc`. Tools that consume the key can read `PORTKEY_API_KEY` from the environment. Keep keys out of checked-in JSON or TOML files. To load the latest key into an already open shell after renewal, rerun the `export` line or open a new terminal.

### 3. Check or renew

```sh
check_portkey_api
```

The checker first tests the locally stored key with a one-token inference request. When that key is rejected or absent, it reads control-plane cookies from Chrome, Edge, Firefox, or Brave. If no bearer token is available, it opens the configured control-plane URL in your default browser. Complete sign-in, return to the terminal, and press Enter; the checker waits for cookie propagation, then validates the session and selects or creates a key. The active key is saved to Keychain as `PORTKEY_API_KEY`.

To update later, run the one-off function again with the same gateway settings. The installer also accepts all six `PORTKEY_OAUTH_BASE`, `PORTKEY_OAUTH_WORKSPACE_ID`, `PORTKEY_OAUTH_ORGANISATION_ID`, `PORTKEY_OAUTH_KEY_NAME`, `PORTKEY_INFERENCE_BASE`, and `PORTKEY_INFERENCE_MODEL` environment variables if you invoke it separately. An installer run without overrides produces a template-based config that you must configure before checking a key.

## Commands

```bash
portkey-check              # Select newest active unexpired key, or create one if needed
portkey-check --renew      # Force creation of a new key
portkey-check --force-login # Skip the existing-key probe and use the browser session
portkey-renew              # Create a new key interactively
portkey-get-bearer         # Validate browser bearer-token extraction
python3 main.py check      # Root dispatcher
```

Legacy aliases `check-key`, `renew-key`, and `get-bearer` remain available.

## Behavior

Portkey can keep multiple keys active. This tool always lists keys from `/albus/v2/api-keys?workspace_id=...`, ignores inactive or expired keys, and selects the active unexpired key with the newest `created_at`. If none exists, it creates a new key through `/albus/v2/api-keys/workspace/user`.

## Security Report

`portkey-check` runs a console environment verification after selecting the active key. To generate the HTML security report manually:

```bash
generate-report
python3 main.py report
```

The report is written to `logs/security_report.html`.

## Requirements

- macOS
- Python 3.8 through 3.12
- `uv` for the recommended local setup
- Access to sign in to the configured control plane in Chrome, Edge, Firefox, or Brave

Safari is not supported for automated cookie extraction.

## Troubleshooting

- `No bearer token found`: the checker opens the configured control plane in a supported default browser; complete sign-in, then return to the terminal.
- `Portkey API key list failed`: non-authentication failures still require checking `base_url` and `workspace_id`; expired authentication now starts an interactive browser login.
- Keychain prompts: allow Terminal access to macOS Keychain.
- Keep `config/config.json` private, for example `chmod 600 config/config.json`.

<p align="center">
  <img src="assets/Portkey_API_Banner.png" alt="Portkey API Key Updater Banner" />
</p>

## Versioning

This project follows [Semantic Versioning](https://semver.org/). The version lives in `src/portkey_key_updater/__init__.py`, is shown with `portkey-check --version` or `python3 main.py --version`, and each release is documented in [CHANGELOG.md](CHANGELOG.md). Tagging `vX.Y.Z` triggers the GitHub release workflow.

## License

MIT License. See [LICENSE](LICENSE).
