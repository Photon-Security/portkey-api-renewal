# Portkey Authentication Analysis

The updater uses the existing authenticated Portkey browser session. It does not ask the user for a password or store the browser bearer token as the API key.

## Session Extraction

`get_bearer.py` reads the Portkey session cookies straight from the browser's on-disk cookie store using `browser_cookie3`. It does not drive the browser.

### Which browser is read

Only the **default macOS browser** is read. There is no scan of other installed browsers and no fallback.

1. `utils.get_default_browser()` asks macOS Launch Services for the handler of `public.html` (then the `http` scheme) and returns its bundle ID.
2. `get_browser_cookies_for_domain()` maps that single bundle ID to one `browser_cookie3` reader.
3. If the default browser is not in the table, extraction returns no cookies and the tool tells the user to switch their default browser.

| Default browser | Bundle ID | Result |
| --- | --- | --- |
| Google Chrome | `com.google.chrome` | Supported |
| Microsoft Edge | `com.microsoft.edgemac` | Supported |
| Brave | `com.brave.Browser` / `com.brave.browser` | Supported |
| Firefox | `org.mozilla.firefox` | Supported |
| Safari | `com.apple.safari` | Not supported (sandboxing); explicit error |
| Opera, Vivaldi, Chromium, other | various | Not supported; "Unsupported browser" |

So a user who is signed in to Portkey in Firefox but whose default browser is Safari gets "no cookies" even though a valid session exists. The fix is to change the default browser, not to sign in again.

### Remote debugging is not used

No remote debugging port, DevTools protocol, Selenium, Playwright, or browser extension is involved, and the browser does not need to be launched with any special flag. The tool reads the cookie database files the browser already keeps on disk:

- **Chromium family (Chrome, Edge, Brave):** the `Cookies` SQLite file under `~/Library/Application Support/<Browser>/`. Values are encrypted, so `browser_cookie3` fetches the browser's "Safe Storage" password (for example `Chrome Safe Storage`) with `/usr/bin/security find-generic-password`, derives the key, and decrypts locally.
- **Firefox:** `cookies.sqlite` in the default profile named by `profiles.ini`. Firefox cookies are not encrypted, so no Keychain access is needed.

Consequences:

- The browser can be open or closed. The database is opened read-only, with a temp-copy fallback if it is locked.
- The first run on a Chromium browser triggers a macOS Keychain prompt for the Safe Storage item. Choose **Always Allow** or the prompt returns.
- Cookies reach disk with a delay after sign-in. This is why the tool waits about 25 seconds after login (`COOKIE_PROPAGATION_WAIT_SECONDS`) before reading again.

### Which profile is read

The reader does not look at every profile.

- **Chrome:** the first match of `Default/Cookies`, then `Profile */Cookies` in sorted order. If a `Default` profile exists, it is the only one read. A user signed in to Portkey only under `Profile 1` is treated as signed out.
- **Firefox:** the default profile from `profiles.ini`.
- Edge and Brave use the same shared Chromium logic (profile path lists not individually verified).

### Cookie selection

The configured Portkey host is queried first. If that finds nothing, all cookies are loaded and filtered afterwards. In both cases only cookies for the exact host or one of its parent domains are kept, so lookalike hosts such as `portkey.example.com.attacker.test` are rejected.

The bearer token is the first non-empty value among the cookies `token`, `auth_token`, `access_token`, `id_token`, in that order. A `Bearer ` prefix is stripped. It is sent as:

```text
Authorization: Bearer <token>
```

### Why failures look like "No cookies found"

Every failure in this stage is swallowed: both extraction attempts catch all exceptions, and `check_key.py` also redirects stderr while reading. These distinct causes therefore all produce the same `No cookies found in browser session`:

- Not signed in, or the session has expired.
- Signed in under a different Chrome profile or a non-default browser.
- Default browser not supported.
- Keychain access to the Safe Storage item denied. `browser_cookie3` then falls back to a default password that fails to decrypt.

To see the real error, run this with the same browser (replace `chrome` with `edge`, `brave` or `firefox` as needed):

```bash
python3 -c "import browser_cookie3 as b; print(len(list(b.chrome(domain_name='your-portkey-domain.com'))))"
```

## Token Validation

Bearer-token validation uses the same endpoint as normal key discovery:

```text
GET {base_url}/albus/v2/api-keys?workspace_id={workspace_id}
```

A `200` response with a JSON `data` array means the browser token can read workspace keys. Non-`200` responses are treated as authentication or permission failures and the tool asks the user to refresh their Portkey browser session.

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
