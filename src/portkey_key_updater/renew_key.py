#!/usr/bin/env python3
"""Create a new Portkey API key using browser session authentication."""

import argparse
import subprocess
import sys

from .get_bearer import (
    extract_bearer_token_from_cookies,
    find_session_in_other_browsers,
    get_browser_cookies_for_domain,
)
from .logger import log_end, log_error, log_info, log_start, log_success, log_warning
from .portkey_api import create_api_key
from .utils import (
    COOKIE_EXTRACTABLE_BROWSER_IDS,
    COOKIE_EXTRACTABLE_BROWSER_NAMES,
    Colors,
    colored_print,
    get_browser_info,
    load_config,
    obfuscate_key,
)


def copy_to_clipboard(text):
    """Copy text to clipboard using macOS pbcopy."""
    try:
        process = subprocess.run(["pbcopy"], input=text, text=True, timeout=5)
        return process.returncode == 0
    except Exception:
        return False


def open_browser(url):
    """Open a URL in the default macOS browser."""
    return subprocess.run(["open", url], check=False)


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
            f"[AUTH] I opened Portkey in {browser_name}. Please log in, wait about 20 seconds for cookies to refresh, then run this command again.",
            Colors.YELLOW,
        )
    except Exception as exc:
        colored_print(
            f"[AUTH] Please open {base_url} in {browser_name}, log in, wait about 20 seconds for cookies to refresh, then run this command again.",
            Colors.YELLOW,
        )
        log_error(f"Could not open browser for missing Portkey browser session: {exc}")


def request_api_key_with_token(final_token, cookies, silent=False, no_logging=False):
    """Create a new Portkey API key using a bearer token and browser cookies.

    Returns:
        tuple: (success, api_key) where api_key is None if creation failed
    """
    config = load_config()

    if not silent:
        endpoint = config["oauth"]["create_api_key_endpoint"]
        print(f"🔑 Requesting new Portkey API key from: {endpoint}", file=sys.stderr)

    try:
        success, api_key, error = create_api_key(config, final_token, cookies)
    except Exception as exc:
        if not silent:
            colored_print(f"[ERROR] Portkey API request error: {exc}", Colors.RED)
            log_error(f"Portkey API request error: {exc}")
        return False, None

    if success and api_key:
        if not silent:
            colored_print("[SUCCESS] Created Portkey API key", Colors.GREEN)
            if not no_logging:
                log_success("Portkey API key created successfully")
            print(f"API_KEY: {obfuscate_key(api_key)}")
        return True, api_key

    if not silent:
        colored_print(f"[ERROR] Portkey API key creation failed: {error}", Colors.RED)
        log_error(f"Portkey API key creation failed: {error}")
    return False, None


def _extract_session_token_and_cookies(config):
    """Load browser session cookies and return the token cookie value."""
    browser_info = get_browser_info()

    if not browser_info or not browser_info.get("bundle_id"):
        colored_print("[ERROR] Could not detect default browser", Colors.RED)
        log_error("Could not detect default browser")
        sys.exit(1)

    colored_print(
        f"[INFO] Default browser: {browser_info['name']} ({browser_info['bundle_id']})",
        Colors.CYAN,
    )
    domain = config["oauth"]["base_url"].replace("https://", "").replace("http://", "").split("/")[0]
    cookies = get_browser_cookies_for_domain(browser_info["bundle_id"], domain)

    if not extract_bearer_token_from_cookies(cookies)[0]:
        other_session = find_session_in_other_browsers(domain, browser_info["bundle_id"])
        if other_session:
            _, cookies, other_browser = other_session
            browser_info = other_browser
            colored_print(f"[INFO] Using the Portkey session found in {other_browser['name']}", Colors.CYAN)

    if not cookies:
        colored_print("[ERROR] No cookies found in browser session", Colors.RED)
        log_error("No cookies found in browser session")
        handle_missing_browser_session(config, browser_info)
        sys.exit(1)

    colored_print(f"[INFO] Found cookies: {', '.join(sorted(cookies))}", Colors.CYAN)
    token_value, token_cookie_name = extract_bearer_token_from_cookies(cookies)

    if not token_value:
        colored_print("[ERROR] No bearer token found in cookies", Colors.RED)
        log_error("No bearer token found in cookies")
        colored_print("[INFO] Expected one of: token, auth_token, access_token, id_token", Colors.CYAN)
        colored_print("[INFO] Please make sure you're logged in and authenticated", Colors.CYAN)
        sys.exit(1)

    colored_print(
        f"[SUCCESS] Found bearer token in {browser_info.get('name', 'browser')} cookie '{token_cookie_name}'",
        Colors.GREEN,
    )
    log_success(f"Found bearer token in {browser_info.get('name', 'browser')} cookie '{token_cookie_name}'")
    return token_value, cookies


def main():
    """Main function for standalone usage."""
    log_start()
    parser = argparse.ArgumentParser(
        description="Generate a Portkey API key using bearer token from browser cookies",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  portkey-renew                         # Interactive mode - prompts before generating key
  portkey-renew --silent                # Silent mode - generates key without prompting
  renew-key --silent                    # Legacy alias
        """,
    )
    parser.add_argument(
        "--silent",
        action="store_true",
        help="Generate key without interactive confirmation",
    )

    args = parser.parse_args()

    print("🔑 Portkey API Key Generator", file=sys.stderr)
    print("", file=sys.stderr)

    config = load_config()
    token_value, cookies = _extract_session_token_and_cookies(config)

    if not args.silent:
        print("", file=sys.stderr)
        colored_print("[WARNING] This will generate a new Portkey API key for your account.", Colors.YELLOW)
        log_warning("User initiated Portkey API key generation")
        colored_print("[INFO] Existing Portkey keys are not revoked automatically.", Colors.CYAN)
        print("", file=sys.stderr)

        try:
            response = input("Do you want to proceed with API key generation? [y/N]: ").strip().lower()
            if response not in ["y", "yes"]:
                colored_print("[ERROR] API key generation cancelled by user", Colors.RED)
                log_info("API key generation cancelled by user")
                sys.exit(0)
        except KeyboardInterrupt:
            colored_print("\n[ERROR] API key generation cancelled by user", Colors.RED)
            sys.exit(0)

        print("", file=sys.stderr)

    print("🔄 Generating Portkey API key...", file=sys.stderr)
    success, api_key = request_api_key_with_token(token_value, cookies)

    if success and api_key:
        colored_print("[SUCCESS] Portkey API key generated successfully!", Colors.GREEN)
        log_success("Portkey API key generated successfully")

        if copy_to_clipboard(api_key):
            print("📋 API key copied to clipboard!", file=sys.stderr)
        else:
            print("⚠️  Could not copy to clipboard (pbcopy not available)", file=sys.stderr)
            print(f"📋 API Key: {api_key}")

        log_end()
        sys.exit(0)

    colored_print("[ERROR] Failed to generate Portkey API key", Colors.RED)
    log_error("Failed to generate Portkey API key")
    log_end()
    sys.exit(1)


if __name__ == "__main__":
    main()
