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

The setup asks for a gateway profile and its control-plane and inference
settings. Obtain those settings from your gateway administrator. API keys and bearer tokens are never embedded
in the setup script. For other gateways, pass all six `PORTKEY_OAUTH_BASE`,
`PORTKEY_OAUTH_WORKSPACE_ID`, `PORTKEY_OAUTH_ORGANISATION_ID`,
`PORTKEY_OAUTH_KEY_NAME`, `PORTKEY_INFERENCE_BASE`, and `PORTKEY_INFERENCE_MODEL`
settings to `install.sh`, using values from your gateway administrator.

### 1. Install or update

Paste this command into an interactive zsh terminal:

```zsh
source <(curl -fsSL https://raw.githubusercontent.com/Photon-Security/portkey-api-renewal/refs/heads/main/scripts/setup-portkey.sh)
```

Select the new or legacy profile and enter the gateway settings. Setup downloads the current package, creates
the environment and configuration, and loads the checker commands in this
terminal. Run setup again to update, or select the other gateway to install both.
The new gateway uses `~/Applications/Portkey-API-renewal`; the legacy gateway
uses `~/Applications/Portkey-API-renewal-legacy`. Each installation has its own
configuration. Configuration is replaced from the fresh template on update,
with an owner-only backup of the previous config; logs are retained.

### 2. Load the commands in future terminal sessions

Add the line printed by setup to `~/.zshrc`, **after** any `source ~/.llmrc`
or other LLM shell setup. For the new gateway installation:

```zsh
source "$HOME/Applications/Portkey-API-renewal/scripts/portkey-shell.sh"
```

If only the legacy gateway is installed, use
`$HOME/Applications/Portkey-API-renewal-legacy/scripts/portkey-shell.sh`.
Either helper defines both commands. Set `PORTKEY_RENEWER_HOME` before setup
and before sourcing the helper if you use a different parent directory.

### 3. Check or renew

```zsh
check_portkey_api         # New gateway (check_portkey is an alias function)
check_portkey_api_legacy  # Legacy gateway; install it first with setup
```

Each checker tests the existing key with a one-token inference request and
renews only when needed. If browser authentication is required, sign in and
complete MFA when prompted. Supported browsers are Chrome, Edge, Firefox, and
Brave. A successful check also refreshes the matching environment variable in
the current shell; applications already running need to reload their key.

The new gateway reads and writes `PORTKEY_API_KEY` in macOS Keychain. The legacy
gateway uses `PORTKEY_API_KEY_LEGACY`. The helper supplies an explicit config
path and `PORTKEY_KEYCHAIN_SERVICE`, so the current working directory and stale
new-gateway environment keys cannot redirect a legacy check. The service
setting defaults to `PORTKEY_API_KEY` for direct console-script use; when
invoking a legacy console script directly, also set
`PORTKEY_KEYCHAIN_SERVICE=PORTKEY_API_KEY_LEGACY`.

Existing keys in `PORTKEY_API_KEY` are not moved to the legacy item automatically.
If only the legacy key existed there, the legacy checker obtains its key through
the normal browser session flow and stores it in the separate item.

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
