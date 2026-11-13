# Source this file after other LLM shell configuration (zsh or bash).
# PORTKEY_RENEWER_HOME optionally selects the parent installation directory.
unalias check_portkey_api check_portkey check_portkey_api_legacy 2>/dev/null || true

_portkey_check_gateway() {
    local profile="$1" install_dir service key
    shift
    case "$profile" in
        new)
            install_dir="${PORTKEY_RENEWER_HOME:-$HOME/Applications}/Portkey-API-renewal"
            service="PORTKEY_API_KEY"
            ;;
        legacy)
            install_dir="${PORTKEY_RENEWER_HOME:-$HOME/Applications}/Portkey-API-renewal-legacy"
            service="PORTKEY_API_KEY_LEGACY"
            ;;
        *) return 2 ;;
    esac
    if [[ ! -x "$install_dir/.venv/bin/portkey-check" || ! -f "$install_dir/config/config.json" ]]; then
        printf 'Install the %s gateway with setup_portkey_api_renewer first.\n' "$profile" >&2
        return 1
    fi
    PORTKEY_KEY_UPDATER_CONFIG="$install_dir/config/config.json" \
    PORTKEY_KEYCHAIN_SERVICE="$service" \
        "$install_dir/.venv/bin/portkey-check" "$@" || return $?
    # Refresh the caller's environment only after a successful check.
    key="$(security find-generic-password -s "$service" -a "$(id -un)" -w 2>/dev/null)" || return 1
    case "$profile" in
        new) export PORTKEY_API_KEY="$key" ;;
        legacy) export PORTKEY_API_KEY_LEGACY="$key" ;;
    esac
}

check_portkey_api() { _portkey_check_gateway new "$@"; }
check_portkey() { check_portkey_api "$@"; }
check_portkey_api_legacy() { _portkey_check_gateway legacy "$@"; }
