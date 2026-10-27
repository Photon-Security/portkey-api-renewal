# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project uses Semantic Versioning.

## [Unreleased]

### Added

- Fall back to the other supported browsers (Chrome, Edge, Brave, Firefox) when the default browser holds no
  usable Portkey session, both on the first read and after the sign-in prompt, so signing in with a
  non-default browser works. Safari remains unsupported.

### Changed

- `install.sh` now handles first install and update in one path. It downloads the latest source, refreshes
  the code with `rsync --delete` (removing obsolete files), and keeps `config/config.json`, `logs/`,
  `.venv` and `dontsync/`. A broken virtual environment is rebuilt. It reports `installed`, `updated X -> Y`
  or `already on X`. Previously an existing install was never updated.
- A failed download aborts before anything in the install directory is touched.
- `PORTKEY_INSTALL_DIR` and `PORTKEY_ZIP_URL` override the install location and download URL (for testing).

### Fixed

- Installer download URL, README badges, clone and update commands now point at `Photon-Security/portkey-api-renewal`
  instead of the old `Enelass` owner.

### Documentation

- `docs/auth_analysis.md` now documents which browser and profile are read, that no remote debugging is used,
  why failures all surface as "No cookies found", and the existing-key probe.

## [1.1.0] - 2026-10-27

### Added

- Single-source versioning: `__version__` in `src/portkey_key_updater/__init__.py` feeds the package metadata
  (hatch dynamic version), and `--version` is available on `main.py` and `portkey-check`.
- Added README platform/version badges for macOS, zsh, Python, Portkey, checks, and releases.
- Added GitHub Actions workflows for changelog validation and tag-based GitHub releases.
- `verify_api_key_inference()` in `portkey_api.py` — proves an API key is still good by spending it on a
  one-token completion against the inference gateway configured in `inference.base_url`, returning a three-state
  result: `valid`, `unauthorized`, or `unreachable`.
- `inference_settings()` resolving the probe's base URL, endpoint, model and timeout from
  `config.json` → `$AIPE_API_BASE` → built-in defaults, plus a new `inference` section in
  `config/config.json` and `config/config.template.json`.
- `read_existing_api_key()` and `probe_existing_api_key()` in `check_key.py`, reading the key already on
  the machine from the Keychain (service `PORTKEY_API_KEY`) or the environment.
- `--force-login` flag to skip the probe and go straight to the browser session.

### Changed

- **`portkey-check` no longer opens a browser to answer a question inference can answer in a second.**
  Keys are minted on the dashboard (bearer auth, expires in minutes) but spent on
  the gateway (`x-portkey-api-key`, valid about a month). The old flow inferred the
  key's health from the bearer's, so an expired bearer triggered an interactive MFA login even when the
  key was good for weeks. `main()` now probes the existing key before any cookie or bearer work and
  exits successfully when it passes: ~1 second and no MFA, against ~33 seconds plus a login and a
  25-second cookie wait.
- Only an *authentication* failure counts as a dead key. Any other gateway answer proves the key got
  past auth and needs no renewal — a retired model, for instance, replies `HTTP 404`.
- An unreachable gateway keeps the existing key and warns about the proxy/VPN rather than forcing a
  login, since forcing one there would repeat the waste the probe exists to avoid.

### Fixed

- Browser cookie matching now accepts only the exact host or its parent domains, rejecting lookalike hosts.
- The bearer token is no longer printed, even obfuscated.
- The probe omits `x-portkey-config`, which the discovery endpoint requires but `/chat/completions`
  rejects with `HTTP 400 Invalid config passed`. Pinned by a test.
- The API key never reaches stdout, stderr or the returned detail strings — only `obfuscate_key()`
  output and `_response_error()` descriptions. Also pinned by a test.

## [1.0.0] - 2026-03-24

- Reorganized the project into a packaged CLI with a `src/` layout and console entry points.
- Added the top-level `main.py` dispatcher and packaging regression tests.
- Moved runtime configuration into `config/`, added `LICENSE`, and standardized repository structure for distribution.
- Refreshed the documentation set and simplified the README with the workflow comparison diagram.

## [0.4.0] - 2026-01-20

- Improved browser bearer-token handling and configuration defaults for more reliable key renewal.
- Added UI design assets and updated the README to better explain the workflow and setup steps.

## [0.3.0] - 2025-11-14

- Reorganized the documentation into the `docs/` directory and refined the architecture and standalone guides.
- Renamed the secret manager sync command and tightened related CLI and logging behavior.
- Updated the renewal/check flow and added the workflow diagram asset used in project docs.

## [0.2.0] - 2025-10-10

- Introduced centralized logging and expanded environment and renewal diagnostics.
- Added broader agent tooling and ignore rules for local automation artifacts.
- Cleaned up repository-generated files and aligned script naming for secret manager updates.

## [0.1.0] - 2025-09-09

- Initial project import with installer, renewal scripts, reporting tools, configuration template, and docs.
- Added macOS-focused browser token extraction, key rotation, security analysis, and reporting utilities.
- Added the first README, packaging metadata, and screenshot assets for the toolkit.
