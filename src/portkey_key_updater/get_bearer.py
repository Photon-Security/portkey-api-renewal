#!/usr/bin/env python3
"""
Extract JWT token from the default browser's session data
Uses browser-cookie3 library to handle encrypted cookies automatically
Includes interactive authentication flow when no cookies are found
"""

import sys
import os
import json
import argparse
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


def get_browser_cookies_for_domain(browser_id, domain):
    """Extract cookies from the default browser using browser-cookie3 with fallbacks"""
    
    # Map browser IDs to browser-cookie3 functions (Safari excluded due to sandboxing)
    browser_functions = {
        'com.microsoft.edgemac': browser_cookie3.edge,
        'com.google.chrome': browser_cookie3.chrome,
        'com.brave.Browser': browser_cookie3.brave,
        'com.brave.browser': browser_cookie3.brave,  # Alternative Brave bundle ID
        'org.mozilla.firefox': browser_cookie3.firefox
    }
    
    browser_func = browser_functions.get(browser_id)
    if not browser_func:
        if browser_id == 'com.apple.safari':
            colored_print("[ERROR] Safari is not supported due to strict sandboxing restrictions", Colors.RED)
            log_error("Safari is not supported due to strict sandboxing restrictions")
            colored_print("[INFO] Please use Chrome, Edge, Firefox, or Brave for automated token extraction", Colors.CYAN)
            colored_print("[INFO] You can temporarily set one of these as your default browser", Colors.CYAN)
        else:
            colored_print(f"[ERROR] Unsupported browser: {browser_id}", Colors.RED)
            log_error(f"Unsupported browser: {browser_id}")
        return {}
    
    # Try multiple approaches for better compatibility
    cookies = {}
    
    # Approach 1: Try with specific domain
    try:
        print(f" Attempting to extract cookies for domain: {domain}", file=sys.stderr)
        cookie_jar = browser_func(domain_name=domain)
        cookies = extract_cookies_from_jar(cookie_jar, domain)
        if cookies:
            return cookies
    except Exception as e:
        pass  # Silent fallback
    
    # Approach 2: Try without domain filter (get all cookies)
    try:
        cookie_jar = browser_func()
        cookies = extract_cookies_from_jar(cookie_jar, domain)
        if cookies:
            return cookies
    except Exception as e:
        pass  # Silent fallback
    
    return {}

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
    log_start()
    parser = argparse.ArgumentParser(
        description="Extract JWT token from the default browser session data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  portkey-get-bearer                  # Extract and validate the browser bearer token
  get-bearer                          # Legacy alias
        """
    )
    parser.parse_args()

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

            # Delete existing keychain entry if it exists
            subprocess.run(['security', 'delete-generic-password', '-s', 'STUDIO_TOKEN'],
                         capture_output=True, check=False)

            # Add new keychain entry
            result = subprocess.run(['security', 'add-generic-password', '-s', 'STUDIO_TOKEN',
                                   '-a', os.getenv('USER', 'user'), '-w', token_value],
                                  capture_output=True, text=True)

            if result.returncode == 0:
                colored_print("[SUCCESS] Bearer token saved to keychain as STUDIO_TOKEN", Colors.GREEN)
                log_success("Bearer token saved to keychain as STUDIO_TOKEN")
                print("", file=sys.stderr)
                colored_print("[INFO] Add this to your ~/.zshrc or ~/.bashrc:", Colors.CYAN)
                print('export STUDIO_TOKEN=$(security find-generic-password -s "STUDIO_TOKEN" -w)')
            else:
                colored_print(f"[ERROR] Failed to save to keychain: {result.stderr.strip()}", Colors.RED)
                log_error(f"Failed to save bearer token to keychain: {result.stderr.strip()}")
        except Exception as keychain_error:
            colored_print(f"[ERROR] Keychain error: {keychain_error}", Colors.RED)
            log_error(f"Keychain error: {keychain_error}")

    log_end()

if __name__ == "__main__":
    main()


get_bearer_token_and_cookies = get_bearer_token
