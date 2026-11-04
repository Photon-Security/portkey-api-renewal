#!/usr/bin/env zsh
#
# Portkey Key Updater Installation Script
# Checks for existing installation and sets up the environment
#

set -e  # Exit on any error

# Help function
show_help() {
    echo "Portkey Key Updater Installation Script"
    echo ""
    echo "Usage: ./install.sh [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --help      Show this help message"
    echo "  --daemon    Install and start daemon service"
    echo "  --uninstall Remove daemon service and uninstall"
    echo ""
    echo "Examples:"
    echo "  ./install.sh                # Standard installation"
    echo "  ./install.sh --daemon      # Install with daemon service"
    echo "  ./install.sh --uninstall   # Remove daemon and uninstall"
}

# Parse command line arguments
DAEMON_MODE=false
UNINSTALL_MODE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --help|-h)
            show_help
            exit 0
            ;;
        --daemon)
            DAEMON_MODE=true
            shift
            ;;
        --uninstall)
            UNINSTALL_MODE=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Configuration
INSTALL_DIR="${PORTKEY_INSTALL_DIR:-$HOME/Applications/Portkey-API-renewal}"
GITHUB_ZIP_URL="${PORTKEY_ZIP_URL:-https://github.com/Photon-Security/portkey-api-renewal/archive/refs/heads/main.zip}"
# The config is replaced only after source sync and dependency installation succeed.
# PORTKEY_CONFIG_OVERRIDES_V1: wrapper-to-installer configuration contract.
PRESERVE_PATHS=("config/config.json" "config/config.json.backup.*" "logs" ".venv" "dontsync")
REQUIRED_FILES=("pyproject.toml" "src/portkey_key_updater/check_key.py" "src/portkey_key_updater/get_bearer.py" "src/portkey_key_updater/renew_key.py" "src/portkey_key_updater/utils.py")

# Functions
print_info() {
    echo -e "${CYAN}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_command() {
    if ! command -v "$1" &> /dev/null; then
        print_error "$1 is not installed. Please install it first."
        return 1
    fi
    return 0
}

check_directory_exists() {
    if [[ -d "$INSTALL_DIR" ]]; then
        print_info "Directory $INSTALL_DIR exists"
        return 0
    else
        print_info "Directory $INSTALL_DIR does not exist"
        return 1
    fi
}

check_installation_files_exist() {
    local missing_files=()
    
    for file in "${REQUIRED_FILES[@]}"; do
        if [[ ! -f "$INSTALL_DIR/$file" ]]; then
            missing_files+=("$file")
        fi
    done
    
    if [[ ${#missing_files[@]} -eq 0 ]]; then
        print_success "All required installation files found"
        return 0
    else
        print_warning "Missing installation files: ${missing_files[*]}"
        return 1
    fi
}

installed_version() {
    # Prints the version declared by a source tree, or "unknown" for installs that predate __version__.
    local init_file="$1/src/portkey_key_updater/__init__.py"
    local version=""
    if [[ -f "$init_file" ]]; then
        version=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$init_file" | head -1)
    fi
    echo "${version:-unknown}"
}

fetch_latest_source() {
    # Downloads and extracts the latest source; sets FETCH_SOURCE and FETCH_TMP for cleanup.
    FETCH_TMP=$(mktemp -d)
    local zip_file="$FETCH_TMP/portkey-api-renewal.zip"

    print_info "Downloading from GitHub..." >&2
    if ! curl -fsSL -o "$zip_file" "$GITHUB_ZIP_URL"; then
        print_error "Failed to download $GITHUB_ZIP_URL" >&2
        rm -rf "$FETCH_TMP"
        return 1
    fi
    if ! unzip -q "$zip_file" -d "$FETCH_TMP/src"; then
        print_error "Failed to extract archive" >&2
        rm -rf "$FETCH_TMP"
        return 1
    fi

    local extracted_dir
    extracted_dir=$(find "$FETCH_TMP/src" -mindepth 1 -maxdepth 1 -type d | head -1)
    if [[ -z "$extracted_dir" || ! -f "$extracted_dir/pyproject.toml" ]]; then
        print_error "Downloaded archive does not look like the Portkey key updater" >&2
        rm -rf "$FETCH_TMP"
        return 1
    fi
    FETCH_SOURCE="$extracted_dir"
}

sync_source() {
    # Copies a fresh source tree over INSTALL_DIR, keeping user-owned paths and removing obsolete files.
    local source_dir="$1"
    local rsync_args=(-a --delete)
    local preserved   # not "path": in zsh that name is tied to $PATH
    for preserved in "${PRESERVE_PATHS[@]}"; do
        rsync_args+=(--exclude "/$preserved")
    done
    mkdir -p "$INSTALL_DIR"
    rsync "${rsync_args[@]}" "$source_dir/" "$INSTALL_DIR/"
}

virtual_environment_is_healthy() {
    [[ -x "$INSTALL_DIR/.venv/bin/python" ]] && "$INSTALL_DIR/.venv/bin/python" -c "import sys" >/dev/null 2>&1
}

setup_virtual_environment() {
    print_info "Setting up virtual environment with uv..."

    if ! check_command "uv"; then
        print_error "uv is required but not installed"
        print_info "Install uv with: curl -LsSf https://astral.sh/uv/install.sh | sh"
        return 1
    fi

    cd "$INSTALL_DIR"

    if ! virtual_environment_is_healthy; then
        # Missing, or broken because the interpreter it points at moved or was removed.
        rm -rf "$INSTALL_DIR/.venv"
        print_info "Creating virtual environment with Python 3.12..."
        if ! uv venv --python 3.12; then
            print_error "Failed to create virtual environment with Python 3.12"
            print_info "Ensure Python 3.12 is installed on your system"
            return 1
        fi
    fi

    print_info "Installing package and dependencies..."
    if ! uv pip install --python "$INSTALL_DIR/.venv/bin/python" -e .; then
        print_error "Failed to install package dependencies"
        return 1
    fi

    print_success "Virtual environment ready"
    print_info "To activate: source $INSTALL_DIR/.venv/bin/activate"
    return 0
}

# Prepare a complete config before touching the existing one. The template is from
# the freshly synced source tree; old config values are never merged into it.
replace_config() {
    local config_dir="$INSTALL_DIR/config"
    local config_file="$config_dir/config.json"
    local template="$config_dir/config.template.json"
    local candidate backup=""
    if [[ ! -f "$template" || -L "$template" ]]; then
        print_error "Missing or unsafe config template: $template"
        return 1
    fi
    if [[ -e "$config_file" && ! -f "$config_file" || -L "$config_file" ]]; then
        print_error "Config path is not a regular file: $config_file"
        return 1
    fi
    candidate=$(umask 077; mktemp "$config_dir/.config.json.tmp.XXXXXXXX") || {
        print_error "Failed to create temporary config"
        return 1
    }
    if ! python3 - "$template" "$candidate" <<'PYCONFIG'
import json
import os
import sys

names = {
    "PORTKEY_OAUTH_BASE": ("oauth", "base_url"),
    "PORTKEY_OAUTH_WORKSPACE_ID": ("oauth", "workspace_id"),
    "PORTKEY_OAUTH_ORGANISATION_ID": ("oauth", "organisation_id"),
    "PORTKEY_OAUTH_KEY_NAME": ("oauth", "key_name"),
    "PORTKEY_INFERENCE_BASE": ("inference", "base_url"),
    "PORTKEY_INFERENCE_MODEL": ("inference", "model"),
}
try:
    values = {name: os.environ.get(name) for name in names}
    if any(value is not None for value in values.values()):
        if any(not value for value in values.values()):
            raise ValueError("gateway settings must contain all six nonempty values")
    with open(sys.argv[1], encoding="utf-8") as source:
        config = json.load(source)
    if not isinstance(config, dict) or any(
        not isinstance(config.get(section), dict) for section in ("oauth", "inference")
    ):
        raise ValueError("config template needs oauth and inference objects")
    for name, value in values.items():
        if value is not None:
            section, field = names[name]
            config[section][field] = value
    with open(sys.argv[2], "w", encoding="utf-8") as output:
        json.dump(config, output, indent=2)
        output.write("\n")
except (OSError, ValueError, TypeError) as exc:
    print(f"Cannot prepare config: {exc}", file=sys.stderr)
    sys.exit(1)
PYCONFIG
    then
        rm -f "$candidate"
        print_error "Existing config was preserved"
        return 1
    fi
    if ! chmod 600 "$candidate"; then
        rm -f "$candidate"
        print_error "Failed to secure temporary config; existing config was preserved"
        return 1
    fi
    if [[ -e "$config_file" ]]; then
        backup=$(umask 077; mktemp "$config_dir/config.json.backup.XXXXXXXX") || {
            rm -f "$candidate"
            print_error "Failed to reserve a unique config backup path"
            return 1
        }
        if ! cp "$config_file" "$backup" || ! chmod 600 "$backup"; then
            rm -f "$candidate" "$backup"
            print_error "Failed to back up config; existing config was preserved"
            return 1
        fi
    fi
    if ! mv -f "$candidate" "$config_file"; then
        rm -f "$candidate"
        print_error "Failed to replace config; existing config was preserved"
        [[ -n "$backup" ]] && print_info "Config backup: $backup"
        return 1
    fi
    [[ -n "$backup" ]] && print_info "Config backup: $backup"
    print_info "Replaced config/config.json from the freshly installed template"
    return 0
}

# Main execution: first install and update are the same path.
main() {
    print_info "Portkey Key Updater Installation Script"
    print_info "======================================="

    check_command curl || exit 1
    check_command unzip || exit 1
    check_command rsync || exit 1

    local previous_version="not installed"
    if check_directory_exists && check_installation_files_exist; then
        previous_version=$(installed_version "$INSTALL_DIR")
        print_info "Existing installation found (version: $previous_version), checking for updates"
    else
        print_info "No complete installation found, installing"
    fi

    local source_dir
    fetch_latest_source || exit 1
    source_dir="$FETCH_SOURCE"
    local latest_version
    latest_version=$(installed_version "$source_dir")

    if ! sync_source "$source_dir"; then
        print_error "Failed to copy files into $INSTALL_DIR"
        rm -rf "$FETCH_TMP"
        exit 1
    fi
    rm -rf "$FETCH_TMP"

    if ! check_installation_files_exist; then
        print_error "Installation incomplete - some required files are still missing"
        exit 1
    fi

    if ! setup_virtual_environment; then
        exit 1
    fi

    if ! replace_config; then
        exit 1
    fi

    if [[ "$previous_version" == "not installed" ]]; then
        print_success "Installed version $latest_version"
    elif [[ "$previous_version" == "$latest_version" ]]; then
        print_success "Already on version $latest_version (files refreshed)"
    else
        print_success "Updated $previous_version -> $latest_version"
    fi
    print_info "Location: $INSTALL_DIR"
    print_info "Your logs/ were left untouched"
}

# Daemon management functions
create_daemon_plist() {
    local plist_path="$HOME/Library/LaunchAgents/com.portkey.keyupdater.plist"
    
    print_info "Creating daemon plist at $plist_path"
    
    # Load config to get schedule settings
    local schedule_hour=9
    local schedule_minute=30
    
    if [[ -f "$INSTALL_DIR/config/config.json" ]]; then
        # Extract schedule from config/config.json if it exists
        if command -v python3 >/dev/null 2>&1; then
            schedule_hour=$(python3 -c "import json; print(json.load(open('$INSTALL_DIR/config/config.json')).get('daemon', {}).get('schedule_hour', 9))" 2>/dev/null || echo "9")
            schedule_minute=$(python3 -c "import json; print(json.load(open('$INSTALL_DIR/config/config.json')).get('daemon', {}).get('schedule_minute', 30))" 2>/dev/null || echo "30")
        fi
    fi
    
    print_info "Scheduling daemon to run daily at ${schedule_hour}:$(printf "%02d" $schedule_minute)"
    
    cat > "$plist_path" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.portkey.keyupdater</string>
    <key>ProgramArguments</key>
    <array>
        <string>$INSTALL_DIR/.venv/bin/portkey-check</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$INSTALL_DIR</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>$schedule_hour</integer>
        <key>Minute</key>
        <integer>$schedule_minute</integer>
    </dict>
    <key>RunAtLoad</key>
    <false/>
    <key>StandardOutPath</key>
    <string>$HOME/Library/Logs/portkey-keyupdater.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/Library/Logs/portkey-keyupdater-error.log</string>
</dict>
</plist>
EOF

    print_success "Daemon plist created"
    return 0
}

start_daemon() {
    local plist_path="$HOME/Library/LaunchAgents/com.portkey.keyupdater.plist"
    
    print_info "Loading daemon with launchctl"
    
    if launchctl load "$plist_path"; then
        print_success "Daemon loaded and started"
        print_info "Daemon will run daily at ${schedule_hour}:$(printf "%02d" $schedule_minute)"
        print_info "Logs: $HOME/Library/Logs/portkey-keyupdater.log"
        return 0
    else
        print_error "Failed to load daemon"
        return 1
    fi
}

stop_and_remove_daemon() {
    local plist_path="$HOME/Library/LaunchAgents/com.portkey.keyupdater.plist"
    
    print_info "Stopping and removing daemon"
    
    # Unload daemon if running
    if launchctl list com.portkey.keyupdater >/dev/null 2>&1; then
        print_info "Unloading daemon"
        launchctl unload "$plist_path" 2>/dev/null || true
    fi
    
    # Remove plist file
    if [[ -f "$plist_path" ]]; then
        print_info "Removing plist file"
        rm -f "$plist_path"
        print_success "Daemon removed"
    else
        print_info "No daemon plist found"
    fi
    
    return 0
}

uninstall_application() {
    print_info "Uninstalling Portkey Key Updater"
    
    # Stop and remove daemon first
    stop_and_remove_daemon
    
    # Remove application directory
    if [[ -d "$INSTALL_DIR" ]]; then
        print_info "Removing installation directory: $INSTALL_DIR"
        rm -rf "$INSTALL_DIR"
        print_success "Installation directory removed"
    else
        print_info "Installation directory not found"
    fi
    
    # Remove log files
    local log_files=("$HOME/Library/Logs/portkey-keyupdater.log" "$HOME/Library/Logs/portkey-keyupdater-error.log")
    for log_file in "${log_files[@]}"; do
        if [[ -f "$log_file" ]]; then
            print_info "Removing log file: $log_file"
            rm -f "$log_file"
        fi
    done
    
    print_success "Uninstallation completed"
    return 0
}

# Run main function with mode selection
if [[ "$UNINSTALL_MODE" == true ]]; then
    uninstall_application
    exit 0
fi

main "$@"

if [[ "$DAEMON_MODE" == true ]]; then
    print_info "Setting up daemon service"
    
    if create_daemon_plist && start_daemon; then
        print_success "Daemon service setup completed"
        print_info "The key updater will now run automatically daily as configured"
    else
        print_error "Failed to setup daemon service"
        exit 1
    fi
fi
