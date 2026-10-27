#!/usr/bin/env python3
"""Check the latest usable Portkey API key and renew when needed."""

import argparse
import base64
import binascii
import json
import os
import subprocess
import sys
import time
from contextlib import redirect_stderr
from datetime import datetime, timezone
from urllib.parse import urlsplit

from . import __app_name__, __version__
from .get_bearer import (
    extract_bearer_token_from_cookies,
    find_session_in_other_browsers,
    get_browser_cookies_for_domain,
)
from .logger import log_end, log_error, log_info, log_start, log_success
from .portkey_api import (
    describe_key,
    is_authentication_error,
    list_api_keys,
    select_latest_active_key,
    verify_api_key_inference,
)
from .renew_key import copy_to_clipboard, request_api_key_with_token
from .utils import (
    COOKIE_EXTRACTABLE_BROWSER_IDS,
    COOKIE_EXTRACTABLE_BROWSER_NAMES,
    Colors,
    build_subprocess_env,
    colored_print,
    get_browser_info,
    get_keychain_service_name,
    load_config,
    obfuscate_key,
    timestamp_print,
)


COOKIE_PROPAGATION_WAIT_SECONDS = 25


def open_browser(url):
    """Open a URL in the default macOS browser."""
    return subprocess.run(["open", url], check=False)


def bearer_token_is_expired(token, now=None):
    """Read a JWT expiry as a hint; return None when the token is not a JWT."""
    try:
        encoded_payload = token.split(".", 2)[1]
        encoded_payload += "=" * (-len(encoded_payload) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded_payload).decode("utf-8"))
        expires_at = float(payload["exp"])
    except (binascii.Error, IndexError, KeyError, TypeError, ValueError, UnicodeDecodeError):
        return None

    now = now or datetime.now(timezone.utc)
    return expires_at <= now.timestamp()


def _browser_cookie_domain(config):
    """Return the exact HTTPS host declared by the Portkey adapter config."""
    base_url = config["oauth"]["base_url"]
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Portkey base_url must declare an HTTPS host")
    return parsed.hostname


def handle_missing_browser_session(config, browser_info):
    """Open Portkey for supported browsers and explain how to create cookies."""
    base_url = config["oauth"]["base_url"]
    browser_id = browser_info.get("bundle_id")
    browser_name = browser_info.get("name", "your default browser")

    if browser_id not in COOKIE_EXTRACTABLE_BROWSER_IDS:
        colored_print(
            f"[INFO] {browser_name} is not supported for automated cookie extraction.",
            Colors.YELLOW,
        )
        colored_print(
            f"[INFO] Please set your default browser to {COOKIE_EXTRACTABLE_BROWSER_NAMES}, sign in to Portkey, then run this command again.",
            Colors.YELLOW,
        )
        return

    try:
        open_browser(base_url)
        colored_print(
            f"[AUTH] I opened Portkey in {browser_name}. Please log in, wait about {COOKIE_PROPAGATION_WAIT_SECONDS} seconds for cookies to refresh, then run this command again.",
            Colors.YELLOW,
        )
    except Exception:
        colored_print(
            f"[AUTH] Please open {base_url} in {browser_name}, log in, wait about {COOKIE_PROPAGATION_WAIT_SECONDS} seconds for cookies to refresh, then run this command again.",
            Colors.YELLOW,
        )
        log_error("Could not open browser for missing Portkey browser session")


def _read_browser_session(config, browser_info):
    """Read the current exact-domain browser cookies without displaying secrets."""
    domain = _browser_cookie_domain(config)
    with open(os.devnull, "w") as devnull:
        with redirect_stderr(devnull):
            cookies = get_browser_cookies_for_domain(browser_info["bundle_id"], domain)
    token_value, token_cookie_name = extract_bearer_token_from_cookies(cookies)
    return token_value, token_cookie_name, cookies


def _is_usable_token(token, previous_token=None):
    """A token is usable when present, different from the one being replaced, and not a known-expired JWT."""
    return bool(token) and token != previous_token and bearer_token_is_expired(token) is not True


def _find_session_in_other_browsers(config, browser_info, previous_token=None):
    """Look for a usable Portkey session in the supported browsers other than the default one."""
    found = find_session_in_other_browsers(
        _browser_cookie_domain(config),
        browser_info.get("bundle_id"),
        lambda token: _is_usable_token(token, previous_token),
    )
    if not found:
        return None
    token, cookies, other = found
    colored_print(
        f"[INFO] No usable Portkey session in {browser_info.get('name', 'the default browser')}; "
        f"using the session found in {other['name']}",
        Colors.CYAN,
    )
    log_info(f"Using Portkey session from {other['name']} instead of the default browser")
    return token, cookies


def _wait_for_cookie_propagation(seconds=COOKIE_PROPAGATION_WAIT_SECONDS):
    """Wait visibly before reading cookies after the user confirms login."""
    for remaining in range(seconds, 0, -1):
        print(
            f"\r[AUTH] Waiting for the browser cookie to propagate: {remaining:2d}s",
            end="",
            file=sys.stderr,
            flush=True,
        )
        time.sleep(1)
    if seconds:
        print("\r[AUTH] Browser cookie propagation wait complete.     ", file=sys.stderr)


def _refresh_browser_session(
    config,
    browser_info,
    previous_token=None,
    max_attempts=3,
    retry_delay=2,
    cookie_wait_seconds=COOKIE_PROPAGATION_WAIT_SECONDS,
):
    """Let the user sign in, then reload a fresh bearer and cookies once."""
    browser_id = browser_info.get("bundle_id")
    browser_name = browser_info.get("name", "your default browser")
    login_url = config["oauth"]["base_url"].rstrip("/")

    if browser_id not in COOKIE_EXTRACTABLE_BROWSER_IDS:
        handle_missing_browser_session(config, browser_info)
        return None

    try:
        _browser_cookie_domain(config)
        result = open_browser(login_url)
    except (KeyError, ValueError, OSError):
        colored_print("[ERROR] Could not open the configured Portkey login page", Colors.RED)
        return None

    if getattr(result, "returncode", 0) != 0:
        colored_print(f"[AUTH] Please open {login_url} in {browser_name} and sign in.", Colors.YELLOW)
        return None

    colored_print(
        f"[AUTH] Your Portkey browser session is expired. I opened {login_url} in {browser_name}.",
        Colors.YELLOW,
    )
    colored_print("[AUTH] Complete sign-in and MFA, then return here.", Colors.YELLOW)
    colored_print(
        f"[AUTH] Signing in with another supported browser ({COOKIE_EXTRACTABLE_BROWSER_NAMES}) also works.",
        Colors.YELLOW,
    )

    if not sys.stdin.isatty():
        colored_print("[AUTH] After signing in, rerun this command to load the refreshed session.", Colors.YELLOW)
        return None

    try:
        input(
            "Press Enter after the Portkey dashboard has loaded "
            f"to start the {cookie_wait_seconds}-second cookie timer..."
        )
    except (EOFError, KeyboardInterrupt):
        colored_print("\n[ERROR] Portkey authentication was cancelled", Colors.RED)
        return None

    _wait_for_cookie_propagation(cookie_wait_seconds)

    for attempt in range(max_attempts):
        if attempt:
            time.sleep(retry_delay)
        token_value, _, cookies = _read_browser_session(config, browser_info)
        if _is_usable_token(token_value, previous_token):
            return token_value, cookies
        # The user may have signed in with a different browser than the default one.
        other_session = _find_session_in_other_browsers(config, browser_info, previous_token)
        if other_session:
            return other_session

    colored_print("[ERROR] The browser still has no current Portkey session after sign-in", Colors.RED)
    return None


def _validate_or_refresh_session(config, browser_info, token_value, cookies):
    """Validate browser auth, prompting for one login and retry when required."""
    keys = None
    error = None

    if bearer_token_is_expired(token_value) is not True:
        try:
            success, keys, error = list_api_keys(config, token_value, cookies)
        except Exception as exc:
            print(f"❌ Portkey API request error: {type(exc).__name__}", file=sys.stderr)
            return False, token_value, cookies, None
        if success:
            return True, token_value, cookies, keys
        if not is_authentication_error(error):
            print(f"❌ Portkey API key list failed: {error}", file=sys.stderr)
            return False, token_value, cookies, None

    colored_print("[AUTH] Portkey requires a fresh browser login.", Colors.YELLOW)
    refreshed = _refresh_browser_session(config, browser_info, previous_token=token_value)
    if not refreshed:
        return False, token_value, cookies, None

    token_value, cookies = refreshed
    try:
        success, keys, error = list_api_keys(config, token_value, cookies)
    except Exception as exc:
        print(f"❌ Portkey API request error after sign-in: {type(exc).__name__}", file=sys.stderr)
        return False, token_value, cookies, None

    if not success:
        print(f"❌ Portkey browser session was rejected after sign-in: {error}", file=sys.stderr)
        return False, token_value, cookies, None

    colored_print("[SUCCESS] Refreshed Portkey browser session validated", Colors.GREEN)
    return True, token_value, cookies, keys


def read_existing_api_key():
    """Return the API key already on this machine, preferring Keychain over the environment."""
    service_name = get_keychain_service_name()
    result = subprocess.run(
        ["security", "find-generic-password", "-s", service_name, "-w"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        keychain_key = result.stdout.strip()
        if keychain_key:
            return keychain_key, f"Keychain service {service_name}"

    env_key = os.getenv(service_name, "").strip()
    if env_key:
        return env_key, f"environment {service_name}"
    return None, None


def probe_existing_api_key(config):
    """Test the key already held here against real inference before touching the browser.

    An API key is valid for about a month; the dashboard bearer token expires in minutes. So an
    expired bearer says nothing about the key, and renewing on that basis costs an interactive
    MFA login for nothing. Returns the key when it is proven usable, otherwise None.
    """
    api_key, source = read_existing_api_key()
    if not api_key:
        timestamp_print("[PROBE] No existing Portkey API key found locally.", Colors.YELLOW)
        return None

    timestamp_print(
        f"[PROBE] Testing existing key {obfuscate_key(api_key)} from {source} against inference...",
        Colors.BLUE,
    )
    status, detail, error = verify_api_key_inference(config, api_key)

    if status == "valid":
        colored_print(f"[SUCCESS] Existing Portkey API key works - {detail}", Colors.GREEN)
        log_success("Existing Portkey API key validated against inference")
        return api_key

    if status == "unauthorized":
        timestamp_print(
            f"[PROBE] Gateway rejected the existing key ({error}); renewal is required.",
            Colors.YELLOW,
        )
        log_info("Existing Portkey API key rejected by inference gateway")
        return None

    # Unreachable: the key is unproven, but so is any claim that it is dead. Forcing an MFA login
    # here would repeat the exact waste this probe exists to avoid, so keep the key and say so.
    colored_print(
        f"[WARNING] Could not reach the inference gateway ({error}); keeping the existing key.",
        Colors.YELLOW,
    )
    colored_print(
        "[WARNING] Check the proxy/VPN, or re-run with --force-login to renew regardless.",
        Colors.YELLOW,
    )
    log_info("Inference gateway unreachable; existing Portkey API key left in place")
    return api_key


def update_keychain(api_key):
    """Synchronize the configured Keychain service with the active key."""
    service_name = get_keychain_service_name()
    try:
        keychain_result = subprocess.run(
            ["security", "find-generic-password", "-s", service_name, "-w"],
            capture_output=True,
            text=True,
            check=False,
        )
        keychain_key = keychain_result.stdout.strip() if keychain_result.returncode == 0 else None

        if keychain_key == api_key:
            return

        print("🔄 Keychain key differs from active key, updating...", file=sys.stderr)
        subprocess.run(
            ["security", "delete-generic-password", "-s", service_name],
            capture_output=True,
            check=False,
        )
        result = subprocess.run(
            [
                "security",
                "add-generic-password",
                "-s",
                service_name,
                "-a",
                os.getenv("USER", "user"),
                "-w",
                api_key,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(f"🔐 Keychain synchronized with active API key ({service_name})", file=sys.stderr)
        else:
            print(f"⚠️  Failed to update keychain: {result.stderr.strip()}", file=sys.stderr)
    except Exception as keychain_error:
        print(f"⚠️  Keychain sync error: {keychain_error}", file=sys.stderr)


def check_current_api_key(final_token, cookies, return_key=False, prefetched_keys=None):
    """Return the newest active unexpired Portkey API key, creating one if needed."""
    config = load_config()

    list_url = (
        f"{config['oauth']['base_url'].rstrip('/')}"
        f"{config['oauth']['list_api_keys_endpoint']}?workspace_id={config['oauth']['workspace_id']}"
    )
    timestamp_print("[INFO] Checking Portkey API keys from: {}", Colors.CYAN, list_url)

    if prefetched_keys is None:
        try:
            success, keys, error = list_api_keys(config, final_token, cookies)
        except Exception as exc:
            print(f"❌ Portkey API request error: {type(exc).__name__}", file=sys.stderr)
            if return_key:
                return False, None
            return False

        if not success:
            print(f"❌ Portkey API key list failed: {error}", file=sys.stderr)
            if return_key:
                return False, None
            return False
    else:
        keys = prefetched_keys

    latest_key = select_latest_active_key(keys)
    if latest_key:
        api_key = latest_key["key"]
        timestamp_print("[SUCCESS] Latest active Portkey key: {}", Colors.GREEN, describe_key(latest_key))
        log_success(f"Latest active Portkey API key retrieved: {obfuscate_key(api_key)}")
        if return_key:
            return True, api_key
        return True

    colored_print("[RENEW] No active unexpired Portkey key found; creating a new key...", Colors.YELLOW)
    log_info("No active unexpired Portkey key found; creating a new key")
    try:
        renewed, new_key = request_api_key_with_token(final_token, cookies, silent=True, no_logging=True)
    except Exception as exc:
        print(f"❌ Error during key renewal: {exc}", file=sys.stderr)
        if return_key:
            return False, None
        return False

    if renewed and new_key:
        print(f"✅ New Portkey API key generated: {obfuscate_key(new_key)}", file=sys.stderr)
        if return_key:
            return True, new_key
        return True

    print("❌ Failed to generate new Portkey API key", file=sys.stderr)
    if return_key:
        return False, None
    return False


def _extract_session_token_and_cookies(config):
    """Extract the browser token and cookies used for Portkey calls."""
    browser_info = get_browser_info()

    if not browser_info or not browser_info.get("bundle_id"):
        colored_print("[ERROR] Could not detect default browser", Colors.RED)
        log_error("Could not detect default browser")
        sys.exit(1)

    colored_print(
        f"[INFO] Default browser: {browser_info['name']} ({browser_info['bundle_id']})",
        Colors.CYAN,
    )
    log_info(f"Default browser: {browser_info['name']}", "utils.py")
    try:
        domain = _browser_cookie_domain(config)
    except ValueError as exc:
        colored_print(f"[ERROR] {exc}", Colors.RED)
        sys.exit(1)
    colored_print(f"[INFO] Attempting to extract cookies for domain: {domain}", Colors.CYAN)

    token_value, token_cookie_name, cookies = _read_browser_session(config, browser_info)

    if not _is_usable_token(token_value):
        other_session = _find_session_in_other_browsers(config, browser_info)
        if other_session:
            token_value, cookies = other_session
            token_cookie_name = extract_bearer_token_from_cookies(cookies)[1]

    if not cookies:
        colored_print("[ERROR] No cookies found in browser session", Colors.RED)
        log_error("No cookies found in browser session")
        handle_missing_browser_session(config, browser_info)
        sys.exit(1)

    colored_print(f"[INFO] Found cookies: {', '.join(sorted(cookies))}", Colors.CYAN)
    if not token_value:
        colored_print("[ERROR] No bearer token found in cookies", Colors.RED)
        log_error("No bearer token found in cookies")
        colored_print("[INFO] Expected one of: token, auth_token, access_token, id_token", Colors.YELLOW)
        colored_print("[INFO] Please make sure you're logged in and authenticated", Colors.YELLOW)
        sys.exit(1)

    timestamp_print(
        f"[SUCCESS] Found bearer token in {browser_info.get('name', 'browser')} cookie '{token_cookie_name}'",
        Colors.GREEN,
    )
    log_success(
        f"Found bearer token in {browser_info.get('name', 'browser')} cookie '{token_cookie_name}'",
        "get_bearer.py",
    )
    return token_value, cookies, browser_info


def run_environment_analysis(current_api_key):
    """Run the existing environment analysis and optional keychain sync helper."""
    print("", file=sys.stderr)
    timestamp_print("[INFO] Cross-referencing with environment analysis...", Colors.CYAN)

    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "portkey_key_updater.analyse_env",
                "--verify-key",
                current_api_key,
                "--no-logging",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=build_subprocess_env(),
        )

        if result.returncode == 0:
            log_success("Environment analysis completed", "analyse_env.py")

        if result.stdout.strip():
            print(result.stdout.strip())

        if "🔄 KEY UPDATE" in result.stdout:
            print("Attempting to synchronize environment with active key...", file=sys.stderr)
            sync_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "portkey_key_updater.update_secretmgr",
                    "--key",
                    current_api_key,
                    "--no-logging",
                ],
                capture_output=True,
                text=True,
                cwd=".",
                env=build_subprocess_env(),
            )
            if sync_result.returncode == 0:
                print("Environment synchronization completed successfully", file=sys.stderr)
            else:
                print(f"❌ Environment synchronization failed: {sync_result.stderr.strip()}", file=sys.stderr)

        if result.returncode != 0:
            print("⚠️  Environment analysis completed with warnings", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print("⚠️  Environment analysis timed out", file=sys.stderr)
    except Exception as exc:
        print(f"⚠️  Could not run environment analysis: {exc}", file=sys.stderr)


def main():
    """Main function for standalone usage."""
    log_start()
    parser = argparse.ArgumentParser(
        description="Check the latest Portkey API key using bearer token from browser cookies",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  portkey-check                       # Validate the existing key, renew only if it is dead
  portkey-check --renew               # Force creation of a new Portkey API key
  portkey-check --force-login         # Skip the inference probe and go via the browser
  check-key --renew                   # Legacy alias
        """,
    )

    parser.add_argument("--version", action="version", version=f"{__app_name__} {__version__}")
    parser.add_argument("--renew", action="store_true", help="Force creation of a new Portkey API key")
    parser.add_argument(
        "--force-login",
        action="store_true",
        help="Skip the inference probe of the existing key and go straight to the browser session",
    )

    args = parser.parse_args()
    log_info("Command line flag: --renew (forced renewal requested)" if args.renew else "Command line flag: none")

    colored_print("Portkey API Key Validation...", Colors.PURPLE + Colors.BOLD)
    print("")

    config = load_config()

    # Cheapest path first. The key outlives the bearer by weeks, so prove it on the gateway before
    # spending a browser login and an MFA prompt on a question inference can answer in a second.
    if not args.renew and not args.force_login:
        proven_key = probe_existing_api_key(config)
        if proven_key:
            update_keychain(proven_key)
            if copy_to_clipboard(proven_key):
                print(
                    f"✅ Active Portkey API key copied to clipboard: {obfuscate_key(proven_key)}",
                    file=sys.stderr,
                )
            else:
                print(f"✅ Active Portkey API key found: {obfuscate_key(proven_key)}", file=sys.stderr)
                print(f"📋 API Key: {proven_key}")
            run_environment_analysis(proven_key)
            log_end()
            sys.exit(0)
        print("")

    token_value, cookies, browser_info = _extract_session_token_and_cookies(config)
    print("")

    valid_session, token_value, cookies, prefetched_keys = _validate_or_refresh_session(
        config,
        browser_info,
        token_value,
        cookies,
    )
    if not valid_session:
        log_end()
        sys.exit(1)

    if args.renew:
        timestamp_print("[FORCED] Creating new Portkey API key as requested...", Colors.YELLOW)
        success, current_api_key = request_api_key_with_token(token_value, cookies, silent=False, no_logging=True)
    else:
        success, current_api_key = check_current_api_key(
            token_value,
            cookies,
            return_key=True,
            prefetched_keys=prefetched_keys,
        )

    if not success or not current_api_key:
        log_end()
        sys.exit(1)

    update_keychain(current_api_key)

    if copy_to_clipboard(current_api_key):
        print(f"✅ Active Portkey API key copied to clipboard: {obfuscate_key(current_api_key)}", file=sys.stderr)
    else:
        print(f"✅ Active Portkey API key found: {obfuscate_key(current_api_key)}", file=sys.stderr)
        print(f"📋 API Key: {current_api_key}")

    run_environment_analysis(current_api_key)

    log_end()
    sys.exit(0)


if __name__ == "__main__":
    main()


check_current_key_status = check_current_api_key
