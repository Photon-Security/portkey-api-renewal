#!/usr/bin/env python3
"""
Extract JWT token from the default browser's session data
Uses browser-cookie3 library to handle encrypted cookies automatically
Includes interactive authentication flow when no cookies are found
"""

import sys
import os
import argparse
import json
import base64
import binascii
from pathlib import Path
from contextlib import redirect_stderr

import browser_cookie3
import subprocess
import time
from urllib.parse import unquote
from .logger import log_success, log_warning, log_error, log_info, log_start, log_end
from .utils import Colors, colored_print, load_config, get_browser_info, other_cookie_browsers
from .portkey_api import list_api_keys

TOKEN_COOKIE_CANDIDATES = ("token", "auth_token", "access_token", "id_token")


def extract_bearer_token_from_cookies(cookies):
    """Return the bearer token value from known Portkey session cookie names."""
    for cookie_name in TOKEN_COOKIE_CANDIDATES:
        cookie_data = cookies.get(cookie_name)
        if cookie_data and cookie_data.get("value"):
            value = unquote(cookie_data["value"]).strip()
            if value.lower().startswith("bearer "):
                value = value.split(None, 1)[1]
            return value, cookie_name
    return None, None


CHROMIUM_PROFILE_ROOTS = {
    "com.google.chrome": ("Google/Chrome", "Google/Chrome Beta", "Google/Chrome Dev"),
    "com.microsoft.edgemac": ("Microsoft Edge", "Microsoft Edge Beta", "Microsoft Edge Dev", "Microsoft Edge Canary"),
    "com.brave.Browser": ("BraveSoftware/Brave-Browser",),
    "com.brave.browser": ("BraveSoftware/Brave-Browser",),
}


def _cookie_files(browser_id):
    """Discover macOS Chromium profiles without reading any cookie values."""
    if sys.platform != "darwin" or browser_id not in CHROMIUM_PROFILE_ROOTS:
        return [None]  # Let the library discover Firefox/non-macOS stores.
    files = []
    for folder in CHROMIUM_PROFILE_ROOTS[browser_id]:
        root = Path.home() / "Library/Application Support" / folder
        for pattern in ("Default/Network/Cookies", "Default/Cookies",
                        "Profile */Network/Cookies", "Profile */Cookies"):
            files.extend(sorted(path for path in root.glob(pattern) if path.is_file()))
    return files


def _token_expiry(token):
    """Read JWT expiry only as a selection hint; server validation remains required."""
    try:
        payload = token.split(".", 2)[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (IndexError, KeyError, TypeError, ValueError, binascii.Error, UnicodeDecodeError):
        return None


def _session_rank(cookies):
    token, _ = extract_bearer_token_from_cookies(cookies)
    if not token:
        return (0, 0)
    expiry = _token_expiry(token)
    if expiry is not None and expiry <= time.time():
        return (1, expiry)
    return (2, expiry or 0)


def _cookie_read_warning(browser_id, path, exc):
    """Report actionable categories, never exception bodies or cookie values."""
    if "decrypt" in str(exc).lower():
        reason = ("Cookie decryption failed. Allow this Python installation to access the "
                  "browser's Safe Storage item in Keychain; check that the login Keychain is unlocked.")
    elif isinstance(exc, PermissionError):
        reason = "Cookie database access denied. Check Terminal/Python privacy permissions."
    elif isinstance(exc, OSError):
        reason = "Cookie database could not be opened. Check the browser profile and filesystem access."
    else:
        reason = "Cookie database read failed. Check browser-cookie3 compatibility and the browser profile."
    profile = path.parent.parent.name if path and path.parent.name == "Network" else path.parent.name if path else "default profile"
    # Profile directories are fixed Chromium names; do not display arbitrary paths.
    label = profile if profile == "Default" or profile.startswith("Profile ") else "browser profile"
    colored_print(f"[COOKIES] {browser_id} ({label}): {reason}", Colors.YELLOW)


def get_browser_cookies_for_domain(browser_id, domain, *, diagnose=False):
    """Read each profile, returning one coherent session for the configured host."""
    browser_functions = {
        "com.microsoft.edgemac": browser_cookie3.edge,
        "com.google.chrome": browser_cookie3.chrome,
        "com.brave.Browser": browser_cookie3.brave,
        "com.brave.browser": browser_cookie3.brave,
        "org.mozilla.firefox": browser_cookie3.firefox,
    }
    browser_func = browser_functions.get(browser_id)
    if not browser_func:
        colored_print(f"[COOKIES] Unsupported browser: {browser_id}. Use Chrome, Edge, Firefox, or Brave.", Colors.YELLOW)
        return {}

    files = _cookie_files(browser_id)
    if not files:
        colored_print(f"[COOKIES] No cookie databases found for {browser_id} in the standard profile locations.", Colors.YELLOW)
        return {}
    host = domain.lower().lstrip(".")
    # Include parent-domain cookies without decrypting unrelated sites' cookies.
    labels = host.split(".")
    domains = [".".join(labels[index:]) for index in range(max(1, len(labels) - 1))]
    best = {}
    for path in files:
        cookies = {}
        try:
            for cookie_domain in domains:
                kwargs = {"domain_name": cookie_domain}
                if path is not None:
                    kwargs["cookie_file"] = str(path)
                jar = browser_func(**kwargs)
                # Preserve the more specific cookie when parent queries return duplicates.
                for name, data in extract_cookies_from_jar(jar, host).items():
                    cookies.setdefault(name, data)
        except Exception as exc:
            _cookie_read_warning(browser_id, path, exc)
        if diagnose:
            profile = path.parent.parent.name if path and path.parent.name == "Network" else path.parent.name if path else "library-selected"
            token, name = extract_bearer_token_from_cookies(cookies)
            expiry = _token_expiry(token) if token else None
            print(f"[COOKIES] {browser_id} {profile}: {len(cookies)} host cookies; "
                  f"bearer cookie {name or 'absent'}; expired JWT {expiry is not None and expiry <= time.time()}")
        if cookies and (not best or _session_rank(cookies) > _session_rank(best)):
            best = cookies
    return best


def find_session_in_other_browsers(domain, default_bundle_id, is_usable=None):
    """Look for a Portkey bearer token in every supported browser except the default.

    Returns (token, cookies, browser_info) for the first browser whose token passes
    ``is_usable`` (any non-empty token when omitted), or None. Browsers that are not
    installed or cannot be read are skipped silently.
    """
    for browser in other_cookie_browsers(default_bundle_id):
        try:
            with open(os.devnull, "w") as devnull, redirect_stderr(devnull):
                cookies = get_browser_cookies_for_domain(browser["bundle_id"], domain)
        except Exception:
            continue
        token, _ = extract_bearer_token_from_cookies(cookies)
        if token and (is_usable is None or is_usable(token)):
            return token, cookies, browser
    return None

def extract_cookies_from_jar(cookie_jar, domain):
    """Extract and filter cookies from a cookie jar"""
    cookies = {}
    cookie_names = []
    request_host = domain.lower().lstrip('.')
    
    for cookie in cookie_jar:
        cookie_domain = cookie.domain.lower().lstrip('.')
        cookie_applies_to_host = (
            request_host == cookie_domain
            or request_host.endswith(f'.{cookie_domain}')
        )
        if cookie_applies_to_host:
            
            cookies[cookie.name] = {
                'value': cookie.value,
                'domain': cookie.domain,
                'path': cookie.path,
                'secure': cookie.secure,
                'httponly': hasattr(cookie, 'httponly') and cookie.httponly
            }
            cookie_names.append(cookie.name)
    
    if cookie_names:
        print(f" Total cookies found: {', '.join(cookie_names)}", file=sys.stderr)
    else:
        print(f" No cookies found for domain {domain}", file=sys.stderr)
    
    return cookies

def test_api_key_with_cookies(cookies, config):
    """Test that the extracted bearer token can list Portkey API keys."""
    final_token, cookie_name = extract_bearer_token_from_cookies(cookies)
    if not final_token:
        colored_print("[ERROR] No bearer token cookie found", Colors.RED)
        log_error("No bearer token cookie found")
        return False

    print(f" Found bearer token in cookie '{cookie_name}'", file=sys.stderr)
    
    try:
        print(" Testing bearer token with Portkey key list request", file=sys.stderr)
        success, _, error = list_api_keys(config, final_token, cookies)

        if success:
            colored_print("[SUCCESS] Bearer token extracted and validated", Colors.GREEN)
            log_success("Bearer token extracted and validated")
            colored_print("[INFO] Token is working - use portkey-renew to generate a key with this token", Colors.CYAN)
            return True

        colored_print(f"[ERROR] Token validation failed: {error}", Colors.RED)
        log_error(f"Token validation failed: {error}")
        return False
    except Exception as e:
        colored_print(f"[ERROR] API request error: {e}", Colors.RED)
        log_error(f"API request error: {e}")
        return False

def open_browser_for_authentication(browser_info, config):
    """Open the default browser and prompt user to authenticate"""
    login_url = config['oauth']['base_url']
    browser_name = browser_info.get('name', 'Unknown')
    browser_id = browser_info.get('bundle_id')
    
    print(f"🌐 Opening {browser_name} for interactive authentication...", file=sys.stderr)
    print(f"📋 You will be redirected to: {login_url}", file=sys.stderr)
    
    try:
        # Use macOS 'open' command to open URL in default browser
        subprocess.run(['open', login_url], check=True)
        print(f"✅ Browser opened successfully", file=sys.stderr)
        
        # Wait for user to authenticate
        print(f"", file=sys.stderr)
        colored_print("[AUTH] Please complete the following steps:", Colors.YELLOW)
        print(f"   1. Log in to your account in the browser window that just opened", file=sys.stderr)
        print(f"   2. Make sure you are fully authenticated and can access the dashboard", file=sys.stderr)
        print(f"   3. Keep the browser window open", file=sys.stderr)
        print(f"   4. Press Enter here when you have completed authentication", file=sys.stderr)
        print(f"", file=sys.stderr)
        
        # Wait for user confirmation
        input("Press Enter when you have completed authentication in the browser...")
        
        print(f" Attempting to extract cookies after authentication...", file=sys.stderr)
        return True
        
    except subprocess.CalledProcessError as e:
        colored_print(f"[ERROR] Failed to open browser: {e}", Colors.RED)
        log_error(f"Failed to open browser: {e}")
        return False
    except KeyboardInterrupt:
        colored_print("\n[ERROR] Authentication cancelled by user", Colors.RED)
        return False
    except Exception as e:
        colored_print(f"[ERROR] Error during browser authentication: {e}", Colors.RED)
        return False

def retry_cookie_extraction_with_delay(browser_id, domain, max_retries=3, delay=2):
    """Retry cookie extraction with delays to allow browser session to update"""
    for attempt in range(max_retries):
        if attempt > 0:
            print(f"🔄 Retry attempt {attempt + 1}/{max_retries} (waiting {delay}s for browser session to update)...", file=sys.stderr)
            time.sleep(delay)
        
        cookies = get_browser_cookies_for_domain(browser_id, domain)
        if cookies:
            print(f"✅ Successfully extracted cookies on attempt {attempt + 1}", file=sys.stderr)
            return cookies
    
    colored_print(f"[ERROR] Failed to extract cookies after {max_retries} attempts", Colors.RED)
    return {}

def get_bearer_token():
    """Extract bearer token and cookies for use by other scripts"""
    try:
        # Load configuration
        config = load_config()
        domain = config['oauth']['base_url'].replace('https://', '').replace('http://', '').split('/')[0]
        
        # Get default browser info
        browser_info = get_browser_info()
        if not browser_info or not browser_info.get('bundle_id'):
            return {'success': False, 'error': 'Could not detect default browser'}
        
        browser_id = browser_info.get('bundle_id')
        
        # Extract cookies from browser session
        cookies = get_browser_cookies_for_domain(browser_id, domain)
        
        if not cookies:
            return {'success': False, 'error': 'No cookies found in browser session'}
        
        token_value, _ = extract_bearer_token_from_cookies(cookies)
        
        if not token_value:
            return {'success': False, 'error': 'No bearer token cookie found'}
        
        # Return cookies in the original format for compatibility with check_key.py
        return {
            'success': True,
            'token': token_value,
            'cookies': cookies  # Keep original format with nested 'value' keys
        }
    
    except Exception as e:
        return {'success': False, 'error': str(e)}

def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        description="Extract JWT token from the default browser session data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  portkey-get-bearer                  # Extract and validate the browser bearer token
  get-bearer                          # Legacy alias
        """
    )
    parser.add_argument(
        "--diagnose-cookies", action="store_true",
        help="Report browser profile and cookie-read results without HTTP requests or Keychain writes",
    )
    args = parser.parse_args()
    if args.diagnose_cookies:
        from importlib.metadata import version
        from .utils import COOKIE_FALLBACK_BROWSERS
        from urllib.parse import urlsplit

        config = load_config()
        domain = urlsplit(config["oauth"]["base_url"]).hostname
        if not domain:
            colored_print("[ERROR] The configured control-plane URL has no host", Colors.RED)
            sys.exit(1)
        print(f"[COOKIES] browser-cookie3 {version('browser-cookie3')}; host {domain}")
        found = False
        for browser in COOKIE_FALLBACK_BROWSERS:
            files = _cookie_files(browser["bundle_id"])
            count = len(files) if files != [None] else "library-selected"
            print(f"[COOKIES] {browser['name']}: {count} cookie database(s)")
            cookies = get_browser_cookies_for_domain(browser["bundle_id"], domain, diagnose=True)
            token, name = extract_bearer_token_from_cookies(cookies)
            expiry = _token_expiry(token) if token else None
            current = bool(token) and (expiry is None or expiry > time.time())
            print(f"[COOKIES] {browser['name']}: {len(cookies)} host cookies; bearer cookie {name or 'absent'}; expired JWT {expiry is not None and expiry <= time.time()}")
            found = found or current
        sys.exit(0 if found else 1)

    log_start()
    colored_print(" Extracting Portkey JWT token from default browser session data...", Colors.CYAN)
    
    # Load configuration
    config = load_config()
    domain = config['oauth']['base_url'].replace('https://', '').replace('http://', '').split('/')[0]
    
    # Get default browser info
    browser_info = get_browser_info()
    if not browser_info or not browser_info.get('bundle_id'):
        colored_print("[ERROR] Could not detect default browser", Colors.RED)
        log_error("Could not detect default browser")
        log_end()
        sys.exit(1)
    
    browser_id = browser_info.get('bundle_id')
    browser_name = browser_info.get('name', 'Unknown')
    colored_print(f" Using default browser: {browser_name} ({browser_id})", Colors.CYAN)
    
    # Extract cookies from browser session
    cookies = get_browser_cookies_for_domain(browser_id, domain)
    
    if not cookies:
        colored_print("[ERROR] No cookies found in browser session", Colors.RED)
        log_error("No cookies found in browser session")
        colored_print("[AUTH] Starting interactive authentication flow...", Colors.YELLOW)
        
        # Open browser for interactive authentication
        auth_success = open_browser_for_authentication(browser_info, config)
        
        if not auth_success:
            colored_print("[ERROR] Interactive authentication failed", Colors.RED)
            log_error("Interactive authentication failed")
            log_end()
            sys.exit(1)
        
        # Retry cookie extraction after authentication
        cookies = retry_cookie_extraction_with_delay(browser_id, domain)
        
        if not cookies:
            colored_print("[ERROR] Still no cookies found after authentication", Colors.RED)
            colored_print("[INFO] Troubleshooting:", Colors.CYAN)
            colored_print("   1. Make sure you completed the login process", Colors.WHITE)
            colored_print("   2. Verify you can access the dashboard", Colors.WHITE)
            colored_print("   3. Try running the script again", Colors.WHITE)
            if browser_id == 'com.apple.safari':
                colored_print("   4. For Safari: Grant Terminal 'Full Disk Access' in System Preferences", Colors.WHITE)
            log_end()
            sys.exit(1)
    
    # Test API key generation with extracted cookies
    success = test_api_key_with_cookies(cookies, config)

    if not success:
        colored_print("[ERROR] FAILED TO EXTRACT BEARER TOKEN FROM COOKIES", Colors.RED)
        log_error("Failed to extract bearer token from cookies")
        log_end()
        sys.exit(1)

    # Extract the token value to save to keychain
    token_value, _ = extract_bearer_token_from_cookies(cookies)

    if token_value:
        # Save bearer token to macOS keychain as STUDIO_TOKEN
        try:
            print("", file=sys.stderr)
            print("🔐 Saving bearer token to keychain as STUDIO_TOKEN...", file=sys.stderr)

            from .utils import store_keychain_secret
            stored = store_keychain_secret(token_value, service_name="STUDIO_TOKEN")

            if stored:
                colored_print("[SUCCESS] Bearer token saved to keychain as STUDIO_TOKEN", Colors.GREEN)
                log_success("Bearer token saved to keychain as STUDIO_TOKEN")
                print("", file=sys.stderr)
                colored_print("[INFO] Add this to your ~/.zshrc or ~/.bashrc:", Colors.CYAN)
                print('export STUDIO_TOKEN=$(security find-generic-password -s "STUDIO_TOKEN" -w)')
            else:
                colored_print("[ERROR] Failed to save bearer token to Keychain", Colors.RED)
                log_error("Failed to save bearer token to Keychain")
                log_end()
                sys.exit(1)
        except Exception as keychain_error:
            colored_print(f"[ERROR] Keychain error: {keychain_error}", Colors.RED)
            log_error(f"Keychain error: {keychain_error}")

    log_end()

if __name__ == "__main__":
    main()


get_bearer_token_and_cookies = get_bearer_token
