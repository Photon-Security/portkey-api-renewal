# Download and source this script in zsh on macOS.
# PORTKEY_GATEWAY_SETUP_V1: setup installs and loads the public shell helpers.
setup_portkey_api_renewer() {
  local gateway oauth_base portkey_ws portkey_org infer_base infer_model installer install_dir
  printf 'Select your gateway profile:\n  1) Legacy\n  2) New\n'
  while true; do
    printf 'Enter 1 or 2: '
    IFS= read -r gateway || return 1
    case "$gateway" in
      1) install_dir="${PORTKEY_RENEWER_HOME:-$HOME/Applications}/Portkey-API-renewal-legacy"; break ;;
      2) install_dir="${PORTKEY_RENEWER_HOME:-$HOME/Applications}/Portkey-API-renewal"; break ;;
      *) printf 'Please enter 1 or 2.\n' >&2 ;;
    esac
  done
  printf 'Control-plane base URL (https://...): '
  IFS= read -r oauth_base || return 1
  printf 'Workspace ID: '
  IFS= read -r portkey_ws || return 1
  printf 'Organisation ID: '
  IFS= read -r portkey_org || return 1
  printf 'Inference base URL (https://.../v1): '
  IFS= read -r infer_base || return 1
  printf 'Inference model: '
  IFS= read -r infer_model || return 1
  case "$oauth_base" in https://?*) ;; *) return 1 ;; esac
  case "$infer_base" in https://?*) ;; *) return 1 ;; esac
  [[ -n "$portkey_ws" && -n "$portkey_org" && -n "$infer_model" ]] || return 1
  command -v uv >/dev/null || { curl -LsSf https://astral.sh/uv/install.sh | sh || return 1; source "$HOME/.local/bin/env" || return 1; }
  installer="$(curl -fsSL https://raw.githubusercontent.com/Photon-Security/portkey-api-renewal/refs/heads/main/install.sh)" || return 1
  [[ "$installer" == *PORTKEY_CONFIG_OVERRIDES_V1* ]] || { printf 'The published installer does not support gateway settings.\n' >&2; return 1; }
  PORTKEY_INSTALL_DIR="$install_dir" PORTKEY_OAUTH_BASE="$oauth_base" PORTKEY_OAUTH_WORKSPACE_ID="$portkey_ws" PORTKEY_OAUTH_ORGANISATION_ID="$portkey_org" PORTKEY_OAUTH_KEY_NAME="PortKey" PORTKEY_INFERENCE_BASE="$infer_base" PORTKEY_INFERENCE_MODEL="$infer_model" /bin/zsh -c "$installer" || return 1
  source "$install_dir/scripts/portkey-shell.sh" || return 1
  printf '\nSetup complete. Commands loaded in this terminal:\n  check_portkey_api         (new gateway)\n  check_portkey_api_legacy  (legacy gateway)\n\nFor future terminals, add this after other LLM setup in ~/.zshrc:\n  source "%s/scripts/portkey-shell.sh"\n' "$install_dir"
}
setup_portkey_api_renewer
