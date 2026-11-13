# Portkey Authentication Analysis

The updater uses the existing authenticated Portkey browser session. It does not ask the user for a password or store the browser bearer token as the API key.

## Session Extraction

`get_bearer.py` reads the Portkey session cookies straight from the browser's on-disk cookie store using `browser_cookie3`. It does not drive the browser.

### Which browser is read

The **default macOS browser** is always tried first and drives the normal flow: its cookies are read, and when the session is missing or expired it is the browser opened for sign-in.

1. `utils.get_default_browser()` asks macOS Launch Services for the handler of `public.html` (then the `http` scheme) and returns its bundle ID.
2. `get_browser_cookies_for_domain()` maps that bundle ID to one `browser_cookie3` reader.
3. If the default browser yields no usable bearer token, the tool tries the other supported browsers in this order: Chrome, Edge, Brave, Firefox (`find_session_in_other_browsers()`). The first one holding a usable token wins.

"Usable" means a bearer cookie is present and, when it is a JWT, not already expired. After a sign-in prompt the token must also differ from the one being replaced, so a stale session in another browser is not mistaken for the new login. Browsers that are not installed or cannot be read are skipped silently.

This fallback runs in three places: the initial read in `portkey-check` and `portkey-renew`, and again after the user confirms sign-in. A user who signs in with Firefox while Safari or Chrome is the default is therefore still picked up. The sign-in page is opened in the default browser, and the prompt tells the user another supported browser works too.

| Browser | Bundle ID | Result |
| --- | --- | --- |
| Google Chrome | `com.google.chrome` | Supported, default or fallback |
| Microsoft Edge | `com.microsoft.edgemac` | Supported, default or fallback |
| Brave | `com.brave.Browser` / `com.brave.browser` | Supported, default or fallback |
| Firefox | `org.mozilla.firefox` | Supported, default or fallback |
| Safari | `com.apple.safari` | Not supported (sandboxing); never read, but a supported browser can still be used as fallback |
| Opera, Vivaldi, Chromium, other | various | Not supported |

Reading a Chromium-family browser that is installed can trigger its own macOS Keychain prompt ("Chrome Safe Storage", "Microsoft Edge Safe Storage", ...), even when the user never signed in there. Choose **Always Allow** to stop repeat prompts.

### Remote debugging is not used

No remote debugging port, DevTools protocol, Selenium, Playwright, or browser extension is involved, and the browser does not need to be launched with any special flag. The tool reads the cookie database files the browser already keeps on disk:

- **Chromium family (Chrome, Edge, Brave):** the `Cookies` SQLite file under `~/Library/Application Support/<Browser>/`. Values are encrypted, so `browser_cookie3` fetches the browser's "Safe Storage" password (for example `Chrome Safe Storage`) with `/usr/bin/security find-generic-password`, derives the key, and decrypts locally.
- **Firefox:** `cookies.sqlite` in the default profile named by `profiles.ini`. Firefox cookies are not encrypted, so no Keychain access is needed.

Consequences:

- The browser can be open or closed. The database is opened read-only, with a temp-copy fallback if it is locked.
- The first run on a Chromium browser triggers a macOS Keychain prompt for the Safe Storage item. Choose **Always Allow** or the prompt returns.
- Cookies reach disk with a delay after sign-in. This is why the tool waits about 25 seconds after login (`COOKIE_PROPAGATION_WAIT_SECONDS`) before reading again.

### Which profile is read

On macOS, the updater explicitly discovers `Default` and `Profile *` cookie databases for Chrome, Edge, and Brave, including both `Cookies` and `Network/Cookies` layouts. Chrome and Edge release-channel directories are included. Firefox continues to use the library's selected default profile.

Each profile is read independently. A current bearer takes priority over an expired JWT or a profile with only load-balancer cookies; among current JWTs the later expiry wins. Cookies from different profiles are never combined. The server must still validate the selected bearer.

### Cookie selection

The configured host and its parent domains are queried, stopping before a single-label domain. Results are filtered to the exact host or applicable parent domains. This retrieves parent-domain authentication cookies even when the exact host already has `AWSALB` cookies, without loading every site's cookies.

The bearer is the first non-empty cookie named `token`, `auth_token`, `access_token`, or `id_token`. A `Bearer ` prefix is stripped. The server receives `Authorization: Bearer <token>`.

### Investigating a retrieval regression

The original implementation delegated profile selection to `browser_cookie3`, which chooses the first matching database. It also returned immediately when *any* host cookies were found, and swallowed every extraction exception. Consequently, moving a login to another work profile, receiving a parent-domain auth cookie, or losing Safe Storage access could appear as a missing login. Repeating sign-in did not correct those conditions.

These behaviors were present in the initial repository version; the reviewed history does not establish a recent extraction-code regression. In the investigation on 2026-11-13, Edge 157.0.4341.46 used cookie schema 24, and the installed `browser-cookie3` 0.20.1 successfully retrieved the configured host's cookie names, including `auth_token`. Encrypted fixture tests verify both schema 23 and schema 24 (which prepends a domain hash). This provides no evidence of a general macOS encryption incompatibility on that machine; Stephanie's exact trigger still requires evidence from her installation.

Read/decryption failures now produce `[COOKIES]` messages with actionable categories. Exception bodies and credential values are never displayed. Failed profiles do not prevent reading later profiles.

Run this from the installed environment with its configured control-plane host:

```bash
portkey-get-bearer --diagnose-cookies
```

The command reports the library version, discovered database counts, selected cookie names/counts, and bearer expiry state. It makes no HTTP requests or Keychain writes, and does not display cookie values. Chromium readers may prompt to **read** their Safe Storage item. A decryption warning means checking access for the current Python installation and the unlocked login Keychain; it does not prove access denial, since incompatible encrypted data can produce the same category. An absent bearer with readable databases means checking the signed-in profile and session. Exit status is zero when a non-expired bearer is found and one otherwise; this is extraction evidence, not proof of server acceptance.

## Corrections to local storage

API-key and bearer writes use `security add-generic-password -U` with the current account and service. The updater never deletes the old item before attempting its replacement, and checks report failure if persistence fails. `portkey-renew` now saves successful creations to `PORTKEY_API_KEY` and uses the checker's session-validation/login retry flow. Raw keys are not printed when clipboard access fails.

## Token Validation

Bearer-token validation uses the same endpoint as normal key discovery:

```text
GET {base_url}/albus/v2/api-keys?workspace_id={workspace_id}
```

A `200` response with a JSON `data` array means the browser token can read workspace keys. Authentication errors (HTTP 401/403 or AB05) trigger one browser sign-in and validated retry. Other failures are reported without requesting another login.

## API Key Validity

Since 1.1.0 the existing key is tested first, before any browser work. The key outlives the bearer token by weeks, so asking the gateway is cheaper than a browser login:

1. `probe_existing_api_key()` reads the key from Keychain (`PORTKEY_API_KEY`) or the environment and sends a one-token completion to the gateway in the `inference` section of `config.json`.
2. An authentication failure means the key is dead. Any other answer proves the key passed authentication. An unreachable gateway keeps the key and warns about the proxy or VPN.
3. If the key passes, the tool stops. No cookies or MFA are involved. `--force-login` skips this step.

When the key is dead or `--renew` is given, the bearer token path above is used and keys are selected from the key metadata. A key is usable when:

- `status` is `active`
- `key` is present
- `expires_at` is null, missing, or later than the current UTC time

When several keys match, the newest `created_at` wins. If no key matches, the tool creates a new key with the configured Portkey payload.

## Local Secret Storage

The active API key is stored in macOS Keychain under `PORTKEY_API_KEY`. Shell integrations should read that service instead of hardcoding the key:

```bash
export PORTKEY_API_KEY=$(security find-generic-password -s "PORTKEY_API_KEY" -w)
export OPENAI_API_KEY="$PORTKEY_API_KEY"
```
