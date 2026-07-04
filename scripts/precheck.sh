#!/usr/bin/env bash
#
# precheck.sh
#
# Standalone connectivity pre-check. For every domain the script probes with
# BOTH curl and wget, once with TLS verification and once without, retrying
# each attempt up to RETRIES times. This lets you distinguish:
#   * domain blocked at the firewall     -> both tools fail in both modes
#   * TLS interception / cert issues     -> tools succeed only in insecure mode
#   * one client is broken               -> curl succeeds but wget doesn't (or v.v.)
#
# The rich web/PDF report is produced by the Python app in app/. This script is
# the lightweight standalone equivalent (used by CLI users and CI).
#
set -o errexit
set -o nounset
set -o pipefail

RETRIES="${RETRIES:-3}"
TIMEOUT="${TIMEOUT:-10}"
RETRY_DELAY="${RETRY_DELAY:-2}"
PORT="${PORT:-443}"
FAIL_ON_SSL="${FAIL_ON_SSL:-false}"
DOMAINS_FILE="${DOMAINS_FILE:-/etc/precheck/domains.txt}"

if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  C_RESET=$'\033[0m'; C_RED=$'\033[31m'; C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'; C_BLUE=$'\033[34m'; C_BOLD=$'\033[1m'
else
  C_RESET=''; C_RED=''; C_GREEN=''; C_YELLOW=''; C_BLUE=''; C_BOLD=''
fi

log()  { printf '%s\n' "$*"; }
info() { printf '%s[INFO]%s %s\n' "$C_BLUE"   "$C_RESET" "$*"; }
ok()   { printf '%s[ OK ]%s %s\n' "$C_GREEN"  "$C_RESET" "$*"; }
warn() { printf '%s[WARN]%s %s\n' "$C_YELLOW" "$C_RESET" "$*"; }
err()  { printf '%s[FAIL]%s %s\n' "$C_RED"    "$C_RESET" "$*"; }

default_domains() {
  cat <<'EOF'
# --- Hugging Face ---
huggingface.co
hf.co
# --- NVIDIA NGC ---
ngc.nvidia.com
api.ngc.nvidia.com
auth.ngc.nvidia.com
catalog.ngc.nvidia.com
files.ngc.nvidia.com
nvcr.io
api.nvcr.io
# --- Docker Hub ---
hub.docker.com
docker.io
index.docker.io
registry-1.docker.io
auth.docker.io
production.cloudflare.docker.com
# --- GitHub ---
github.com
api.github.com
codeload.github.com
raw.githubusercontent.com
objects.githubusercontent.com
github.githubassets.com
ghcr.io
# --- PyPI ---
pypi.org
files.pythonhosted.org
pythonhosted.org
EOF
}

collect_domains() {
  local raw=""
  if [[ -f "$DOMAINS_FILE" ]]; then
    info "Loading domains from ${DOMAINS_FILE}" >&2
    raw="$(cat "$DOMAINS_FILE")"
  else
    info "No domains file at ${DOMAINS_FILE}; using built-in defaults" >&2
    raw="$(default_domains)"
  fi
  if [[ -n "${DOMAINS:-}" ]]; then
    raw="${raw}"$'\n'"${DOMAINS// /$'\n'}"
  fi
  printf '%s\n' "$raw" | sed 's/#.*//' | tr -d '\r' \
    | awk '{$1=$1; print}' | awk 'NF' | awk '!seen[$0]++'
}

# probe_once <tool> <domain> <mode>
#   tool:   curl | wget
#   mode:   verify | insecure
probe_once() {
  local tool="$1" domain="$2" mode="$3"
  local url="https://${domain}:${PORT}/"
  local rc=0 detail=""

  case "$tool" in
    curl)
      local -a a=(--silent --show-error --output /dev/null --location
                  --connect-timeout "$TIMEOUT" --max-time "$TIMEOUT"
                  --write-out '%{http_code}')
      [[ "$mode" == "insecure" ]] && a+=(--insecure)
      detail="$(curl "${a[@]}" "$url" 2>/dev/null)" || rc=$?
      if [[ "$rc" -eq 0 ]]; then printf 'http_%s' "$detail"; return 0; fi
      printf 'curl_exit_%s' "$rc"; return 1
      ;;
    wget)
      local -a a=(--quiet --tries=1 --spider
                  --timeout="$TIMEOUT" --dns-timeout="$TIMEOUT"
                  --connect-timeout="$TIMEOUT" --read-timeout="$TIMEOUT")
      [[ "$mode" == "insecure" ]] && a+=(--no-check-certificate)
      wget "${a[@]}" "$url" >/dev/null 2>&1 || rc=$?
      if [[ "$rc" -eq 0 ]]; then printf 'ok'; return 0; fi
      printf 'wget_exit_%s' "$rc"; return 1
      ;;
  esac
}

probe_with_retries() {
  local tool="$1" domain="$2" mode="$3"
  local attempt detail
  for (( attempt = 1; attempt <= RETRIES; attempt++ )); do
    if detail="$(probe_once "$tool" "$domain" "$mode")"; then
      printf '%s (%d/%d)' "$detail" "$attempt" "$RETRIES"; return 0
    fi
    [[ "$attempt" -lt "$RETRIES" ]] && sleep "$RETRY_DELAY"
  done
  printf '%s (%d/%d)' "$detail" "$RETRIES" "$RETRIES"; return 1
}

main() {
  log "${C_BOLD}=== Firewall / whitelist pre-check (curl + wget) ===${C_RESET}"
  info "Retries=${RETRIES}  Timeout=${TIMEOUT}s  Port=${PORT}  FailOnSSL=${FAIL_ON_SSL}"
  log ""

  local missing=()
  for t in curl wget; do
    command -v "$t" >/dev/null 2>&1 || missing+=("$t")
  done
  if (( ${#missing[@]} > 0 )); then
    err "Required tools missing: ${missing[*]}"; exit 2
  fi

  mapfile -t domains < <(collect_domains)
  local total="${#domains[@]}"
  (( total == 0 )) && { err "No domains to check."; exit 2; }
  info "Checking ${total} domain(s) with curl and wget."
  log ""

  local pass=0 fail=0 ssl_only=0
  local -a failed=() ssl_only_list=()

  for domain in "${domains[@]}"; do
    local cv ci wv wi
    local cv_ok=0 ci_ok=0 wv_ok=0 wi_ok=0
    cv="$(probe_with_retries curl "$domain" verify)"    && cv_ok=1 || true
    ci="$(probe_with_retries curl "$domain" insecure)"  && ci_ok=1 || true
    wv="$(probe_with_retries wget "$domain" verify)"    && wv_ok=1 || true
    wi="$(probe_with_retries wget "$domain" insecure)"  && wi_ok=1 || true

    local verify_ok=$(( cv_ok | wv_ok ))
    local insecure_ok=$(( ci_ok | wi_ok ))

    if (( verify_ok )); then
      ok  "${C_BOLD}${domain}${C_RESET}  curl=[v:${cv}|i:${ci}]  wget=[v:${wv}|i:${wi}]"
      pass=$(( pass + 1 ))
    elif (( insecure_ok )); then
      warn "${C_BOLD}${domain}${C_RESET}  reachable ONLY without SSL verify -> possible TLS interception. curl=[v:${cv}|i:${ci}] wget=[v:${wv}|i:${wi}]"
      ssl_only=$(( ssl_only + 1 )); ssl_only_list+=("$domain")
      if [[ "$FAIL_ON_SSL" == "true" ]]; then
        fail=$(( fail + 1 )); failed+=("$domain")
      else
        pass=$(( pass + 1 ))
      fi
    else
      err "${C_BOLD}${domain}${C_RESET}  UNREACHABLE. curl=[v:${cv}|i:${ci}]  wget=[v:${wv}|i:${wi}]"
      fail=$(( fail + 1 )); failed+=("$domain")
    fi
  done

  log ""
  log "${C_BOLD}=== Summary ===${C_RESET}"
  info "Total:        ${total}"
  ok   "Reachable:    ${pass}"
  (( ssl_only > 0 )) && warn "SSL-only:     ${ssl_only} (${ssl_only_list[*]})"
  if (( fail > 0 )); then
    err  "Unreachable:  ${fail} (${failed[*]})"
    log ""
    err "Pre-check FAILED. The domains above must be whitelisted on the firewall."
    exit 1
  fi
  log ""
  ok "Pre-check PASSED. All required domains are reachable."
  exit 0
}

main "$@"
