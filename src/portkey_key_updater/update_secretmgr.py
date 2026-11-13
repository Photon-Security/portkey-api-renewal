#!/usr/bin/env python3
"""
API Key Keychain Updater
Updates macOS Keychain ONLY with current valid API key
Only operates when no GenAI hardcoded secrets are detected by security scanner
"""

import os
import sys
import subprocess
import argparse
from pathlib import Path
from datetime import datetime
from .logger import log_success, log_warning, log_error, log_info, log_start, log_end
from .utils import Colors, colored_print, get_keychain_service_name, get_security_report_path, obfuscate_key


def log_message(message, prefix="[INFO]", no_logging=False):
    """Print timestamped message with color"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    # Determine color based on prefix - but don't log to centralized system if no_logging=True
    if prefix in ["❌", "[ERROR]"]:
        color = Colors.RED
        prefix = "[ERROR]"
        if not no_logging:
            log_error(message)
    elif prefix in ["✅", "[SUCCESS]"]:
        color = Colors.GREEN
        prefix = "[SUCCESS]"
        if not no_logging:
            log_success(message)
    elif prefix in ["⚠️", "[WARNING]"]:
        color = Colors.YELLOW
        prefix = "[WARNING]"
        if not no_logging:
            log_warning(message)
    elif prefix in ["🔐", "[KEYCHAIN]"]:
        color = Colors.PURPLE
        prefix = "[KEYCHAIN]"
        if not no_logging:
            log_info(f"Keychain: {message}")
    elif prefix in ["📋", "[STEPS]"]:
        color = Colors.CYAN
        prefix = "[STEPS]"
        if not no_logging:
            log_info(f"Steps: {message}")
    elif prefix in ["🚨", "[ALERT]"]:
        color = Colors.RED + Colors.BOLD
        prefix = "[ALERT]"
        if not no_logging:
            log_error(f"Alert: {message}")
    else:
        color = Colors.CYAN
        prefix = "[INFO]"
        if not no_logging:
            log_info(message)
    
    colored_print(f"{prefix} [{timestamp}] {message}", color)

def run_security_scan():
    """Run security scanner to check for hardcoded API keys"""
    try:
        from sec_recmm import SecurityRecommendations
        scanner = SecurityRecommendations()
        has_hardcoded_keys, managed_keys = scanner.check_hardcoded_secrets()
        return has_hardcoded_keys, managed_keys, scanner
    except ImportError:
        log_message("Security scanner not available - proceeding without scan", "⚠️")
        return False, [], None
    except Exception as e:
        log_message(f"Failed to run security scan: {e}", "❌")
        return True, [], None  # Assume unsafe on error

def get_current_api_key():
    """Get current valid API key"""
    try:
        # Import and use get_bearer to authenticate 
        from .get_bearer import get_bearer_token_and_cookies
        result = get_bearer_token_and_cookies()
        
        if not result['success']:
            log_message("Failed to get bearer token", "❌")
            return None
        
        from .check_key import check_current_key_status
        is_valid, current_key = check_current_key_status(result['token'], result['cookies'], return_key=True)
        
        if is_valid and current_key:
            return current_key
        else:
            log_message("Failed to retrieve current API key", "❌")
            return None
            
    except Exception as e:
        log_message(f"Error getting current API key: {e}", "❌")
        return None

def update_keychain_key(service_name, account_name, new_key):
    """Update an account-specific entry without deleting its previous secret."""
    from .utils import store_keychain_secret

    return store_keychain_secret(new_key, service_name=service_name, account_name=account_name)


def update_environment_files(new_key, dry_run=False):
    """DISABLED: Environment file updates removed to prevent corruption"""
    log_message("Environment file updates disabled for safety", "⚠️")
    return []

def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Update API key in environment variables and keychain')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be updated without making changes')
    parser.add_argument('--force', action='store_true', help='Force update even if security issues are found (NOT RECOMMENDED)')
    parser.add_argument('--key', type=str, help='API key to use (if not provided, will be retrieved automatically)')
    parser.add_argument('--no-logging', action='store_true', help='Skip START/END logging (for subprocess calls)')
    args = parser.parse_args()
    
    # Only log start if not called as subprocess
    if not args.no_logging:
        log_start()
    
    # Run with minimal logging when --no-logging flag is set
    no_logging = args.no_logging
    
    try:
        # Step 1: Run security scan
        has_hardcoded_keys, managed_keys, scanner = run_security_scan()

        if has_hardcoded_keys and not args.force:
            log_message("🚨 SECURITY VIOLATION DETECTED!", "❌", no_logging)
            log_message(f"Found {len(managed_keys)} hardcoded managed API key(s) in your codebase:", "❌", no_logging)

            for managed_key in managed_keys:
                log_message(f"  • {obfuscate_key(managed_key['value'])} in {managed_key['file']}:{managed_key['line']}", "⚠️", no_logging)

            log_message("", "", no_logging)
            log_message("🔒 For security reasons, key update is BLOCKED until hardcoded keys are removed.", "❌", no_logging)

            if scanner:
                report_path = scanner.save_report(get_security_report_path())
                log_message(f"📊 Detailed security report: {report_path}", "📄", no_logging)

            if not args.no_logging:
                log_end()
            sys.exit(1)

        if args.force and has_hardcoded_keys:
            log_message(f"⚠️  FORCED UPDATE: Ignoring {len(managed_keys)} hardcoded managed key(s) (SECURITY RISK)", "🚨", no_logging)

        # Step 2: Get current API key
        if args.key:
            current_key = args.key
        else:
            current_key = get_current_api_key()
            if not current_key:
                log_message("Failed to retrieve current API key. Cannot proceed.", "❌", no_logging)
                if not args.no_logging:
                    log_end()
                sys.exit(1)

        # Step 3: Update keychain entry (only PORTKEY_API_KEY needed)
        import getpass
        current_user = getpass.getuser()
        service_name = get_keychain_service_name()
        
        if not args.dry_run:
            if update_keychain_key(service_name, current_user, current_key):
                log_success(f"{service_name} updated in keychain", "update_secretmgr.py")
            else:
                log_error(f"Failed to update {service_name} in keychain", "update_secretmgr.py")
                sys.exit(1)
        else:
            log_success(f"Would update {service_name} in keychain (dry run)", "update_secretmgr.py")

    except Exception as e:
        # Capture stdout/stderr for failure logging
        error_msg = f"Update failed: {str(e)}"
        if hasattr(e, 'stdout') and e.stdout:
            error_msg += f" | stdout: {e.stdout}"
        if hasattr(e, 'stderr') and e.stderr:
            error_msg += f" | stderr: {e.stderr}"
        log_error(error_msg, "update_secretmgr.py")
        
        if not args.no_logging:
            log_end()
        sys.exit(1)
    
    if not args.no_logging:
        log_end()

if __name__ == "__main__":
    main()
