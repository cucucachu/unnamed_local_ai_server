#!/usr/bin/env bash
# verify_network.sh — M6-01: verify the LAN-only network posture against the
# FULL RUNNING STACK (not just the host scripts in isolation — M0-02 already
# wrote and ran infra/host/setup-avahi.sh/setup-ufw.sh; this is the
# after-the-fact check that their combined effect, plus the actual compose
# stack, together deliver on the isolation claim in docs/NETWORKING.md:
# reachable by name from the LAN, port 80 only, nothing else exposed).
#
# Must be run with sudo — checks 4/5 read ufw/iptables state, both of which
# refuse to run (or lie) as a non-root user (`ufw status` exits with "You
# need to be root to run this script"; bare `iptables -L` as non-root fails
# with "Could not fetch rule set generation id: Permission denied").
#
# Usage:
#   sudo scripts/verify_network.sh
#
# 8 checks, run in this order:
#   1. `avahi-resolve -n homeai.local` resolves to this host's current LAN
#      IPv4 address (not an IPv6/link-local address, not a Docker bridge
#      address — the exact two failure modes setup-avahi.sh itself guards
#      against, re-verified here against the live daemon).
#   2. `GET http://homeai.local/api/health` returns 200 — proves mDNS
#      resolution AND the Caddy reverse proxy AND agent-server are all
#      working together end to end, from the host's own point of view.
#      Uses Python's urllib rather than curl — curl is not installed on this
#      host (same finding as every other scripts/e2e/*.sh script in this
#      repo; see e.g. verify_isolation.sh's header).
#   3. Port audit, DOCKER STACK SCOPE (not "every process on this dev
#      machine" — see the check's own comment below for why that scoping
#      choice is deliberate): `docker compose config` shows only caddy with
#      a ports: mapping, and live `docker ps` output confirms no running
#      container other than caddy has a host-published port, and that
#      caddy's own published port is exactly 80.
#   4. `ufw status verbose` shows the firewall active, default-deny
#      incoming, and the LAN-subnet allow rule for tcp/80 that setup-ufw.sh
#      installs.
#   5. `iptables -L DOCKER-USER -n -v` contains the DROP rule setup-ufw.sh
#      inserts directly into that chain — the actual enforcement point for
#      Docker-published ports, since Docker's own NAT rules are consulted
#      ahead of plain `ufw allow`/`deny` (see docs/NETWORKING.md's "How
#      LAN-only isolation works" section for the full explanation of why
#      check 4 alone is not sufficient).
#   6. M7-01 no-egress: for each of `agent-server`, `model-runner`,
#      `code-exec-manager`, `postgres` (every service moved onto the
#      `homeai-internal` network, `internal: true`), a real outbound TCP
#      connect attempt from inside that container to a public IP
#      (`1.1.1.1:443`, 3s timeout) is asserted to FAIL. Confirms `internal:
#      true` is actually enforced by the live Docker network, not just
#      declared in `docker-compose.yml`.
#   7. M7-01 internal reachability still works: from inside `agent-server`,
#      a TCP connect to `model-runner:8080` and to `postgres:5432` (both on
#      the same `homeai-internal` network) both succeed — proves the
#      internal-only move didn't also sever the connectivity the app
#      actually needs.
#   8. M7-01 UI still served on :80 from the LAN: `GET http://homeai.local/`
#      returns 200 with the Expo web bundle's `<script>` tag (same check
#      `scripts/e2e/gate_m2.sh` uses) — `caddy` staying on `homeai-net` (the
#      one network that keeps a published port) means the network split
#      didn't affect the one thing every device on the LAN actually hits.
#
# Every check failing is printed in RED and the suite continues (collects
# ALL failures, same convention as verify_isolation.sh) then exits 1 at the
# end if anything failed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$REPO_ROOT"

if [[ "${EUID}" -ne 0 ]]; then
  echo "error: must be run with sudo (checks 4/5 read ufw/iptables state, which refuse to work as non-root)" >&2
  exit 1
fi

RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
NC=$'\033[0m'

PASS_COUNT=0
FAIL_COUNT=0
FAILED_CHECKS=()

log() {
  echo "[verify-network] $(date '+%H:%M:%S') $*"
}

pass() {
  PASS_COUNT=$((PASS_COUNT + 1))
  printf '%sPASS%s [%d] %s\n' "$GREEN" "$NC" "$1" "$2"
}

fail() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  FAILED_CHECKS+=("$1")
  printf '%sFAIL%s [%d] %s\n' "$RED" "$NC" "$1" "$2"
  if [ -n "${3:-}" ]; then
    printf '%s       -> %s%s\n' "$RED" "$3" "$NC"
  fi
}

env_var() {
  local key="$1" default="$2"
  local val=""
  if [[ -f "${REPO_ROOT}/.env" ]]; then
    val="$(grep -E "^${key}=" "${REPO_ROOT}/.env" | tail -n1 | cut -d= -f2- || true)"
  fi
  echo "${val:-${default}}"
}

LAN_SUBNET="$(env_var LAN_SUBNET 192.168.1.0/24)"
DEFAULT_IFACE="$(ip route show default 2>/dev/null | awk '/^default/ {print $5; exit}')"

log "=== M6-01: network verification suite ==="
log "LAN_SUBNET=${LAN_SUBNET} DEFAULT_IFACE=${DEFAULT_IFACE:-<unresolved>}"

# ---- check 1: mDNS resolution ---------------------------------------------
#
# Retries up to 5 times (1s apart) rather than a single shot — same
# convention as setup-avahi.sh's own verification loop, and for the same
# reason: observed live on this exact host (M6-01 development), a single
# avahi-resolve call can occasionally get a stale/racing IPv6 answer for a
# moment even with use-ipv6=no correctly configured and no competing mDNS
# responder (confirmed via `resolvectl mdns` showing systemd-resolved's own
# mDNS fully disabled) — 4 of 5 back-to-back calls resolved correctly. A
# persistent misconfiguration still fails hard: this only accepts the FIRST
# IPv4 answer that also matches the current LAN interface's real address,
# so a genuinely wrong steady-state (IPv6 always, or a stale Docker-bridge
# address) exhausts all 5 attempts and fails exactly as before.

check_1() {
  local resolved_addr iface_addr attempt

  if [[ -z "${DEFAULT_IFACE}" ]]; then
    fail 1 "avahi-resolve -n homeai.local returns this host's LAN IPv4 address" \
      "could not auto-detect the default-route interface to cross-check against"
    return
  fi
  iface_addr="$(ip -4 -o addr show dev "${DEFAULT_IFACE}" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1)"
  if [[ -z "${iface_addr}" ]]; then
    fail 1 "avahi-resolve -n homeai.local returns this host's LAN IPv4 address" \
      "${DEFAULT_IFACE} (the LAN interface) currently has no IPv4 address"
    return
  fi

  for attempt in 1 2 3 4 5; do
    resolved_addr="$(avahi-resolve -n homeai.local 2>/dev/null | awk '{print $2}')" || true
    if [[ "${resolved_addr}" == "${iface_addr}" ]]; then
      pass 1 "avahi-resolve -n homeai.local -> ${resolved_addr} (matches ${DEFAULT_IFACE}, attempt ${attempt}/5)"
      return
    fi
    sleep 1
  done

  if [[ -z "${resolved_addr}" ]]; then
    fail 1 "avahi-resolve -n homeai.local returns this host's LAN IPv4 address" \
      "avahi-resolve returned nothing after 5 attempts — is avahi-daemon running? (sudo infra/host/setup-avahi.sh)"
  elif [[ "${resolved_addr}" == *:* ]]; then
    fail 1 "avahi-resolve -n homeai.local returns this host's LAN IPv4 address" \
      "still resolving to an IPv6 address (${resolved_addr}) after 5 attempts — see docs/NETWORKING.md's IPv6 gotcha"
  else
    fail 1 "avahi-resolve -n homeai.local returns this host's LAN IPv4 address" \
      "resolved to ${resolved_addr} after 5 attempts, but ${DEFAULT_IFACE} (the LAN interface) is currently ${iface_addr} — see docs/NETWORKING.md's Docker-bridge gotcha"
  fi
}

# ---- check 2: end-to-end health check over mDNS + proxy -------------------

check_2() {
  local out rc
  if out="$(python3 -c "
import urllib.request, urllib.error, sys
try:
    with urllib.request.urlopen('http://homeai.local/api/health', timeout=5) as resp:
        print(resp.status, resp.read().decode())
        sys.exit(0 if resp.status == 200 else 1)
except Exception as e:
    print(f'error: {e}')
    sys.exit(1)
" 2>&1)"; then
    rc=0
  else
    rc=$?
  fi
  if [ "$rc" -eq 0 ]; then
    pass 2 "GET http://homeai.local/api/health -> ${out}"
  else
    fail 2 "GET http://homeai.local/api/health -> 200 (mDNS + Caddy + agent-server end to end)" "$out"
  fi
}

# ---- check 3: port audit, docker-stack scope -------------------------------
#
# Deliberately scoped to the Docker Compose stack, not "every listening
# socket on this dev machine" — a developer's own host can legitimately run
# unrelated tooling on other ports (IDE helpers, other projects, etc.) that
# have nothing to do with this ticket's actual security question ("does the
# homeai stack expose anything besides caddy:80"). The ticket's own
# acceptance wording confirms this scope: "fail if any other HOMEAI
# CONTAINER publishes a port" — not any other process. sshd on 22 is
# explicitly named as an allowed EXCEPTION precisely because it's the one
# non-Docker, host-level service this setup expects to be listening
# LAN-wide; it is logged for visibility but never gates this check either
# way (its own exposure is ufw's job — check 4 — not this one's).
check_3() {
  local compose_offenders live_offenders sshd_state

  compose_offenders="$(docker compose config --format json 2>/dev/null | python3 -c "
import json, sys
cfg = json.load(sys.stdin)
bad = [name for name, svc in cfg.get('services', {}).items() if name != 'caddy' and svc.get('ports')]
print('\n'.join(bad))
")"

  live_offenders="$(docker ps --format '{{.Names}}\t{{.Ports}}' | awk -F'\t' '
    $2 ~ /(0\.0\.0\.0|\[::\]):[0-9]+->/ && $1 !~ /caddy/ { print }
    $2 ~ /(0\.0\.0\.0|\[::\]):[0-9]+->/ && $1 ~ /caddy/ {
      line = $2
      n = split(line, parts, ", ")
      for (i = 1; i <= n; i++) {
        if (parts[i] ~ /(0\.0\.0\.0|\[::\]):[0-9]+->/ && parts[i] !~ /(0\.0\.0\.0|\[::\]):80->/) {
          print $1 " publishes non-80 port: " parts[i]
        }
      }
    }
  ')"

  sshd_state="inactive"
  if systemctl is-active --quiet ssh 2>/dev/null || systemctl is-active --quiet sshd 2>/dev/null; then
    sshd_state="active (allowed exception, port 22)"
  fi
  log "  (check 3) sshd: ${sshd_state}"

  if [[ -z "${compose_offenders}" ]] && [[ -z "${live_offenders}" ]]; then
    pass 3 "only caddy publishes a host port, and only port 80 (docker compose config + docker ps)"
  else
    local detail="${compose_offenders}${compose_offenders:+; }${live_offenders}"
    fail 3 "only caddy publishes a host port, and only port 80 (docker compose config + docker ps)" "${detail}"
  fi
}

# ---- check 4: ufw ----------------------------------------------------------

check_4() {
  local status_out
  if ! status_out="$(ufw status verbose 2>&1)"; then
    fail 4 "ufw active, default-deny incoming, LAN-subnet allow rule for tcp/80" "$status_out"
    return
  fi

  local errors=()
  if ! grep -qE '^Status:\s*active' <<<"$status_out"; then
    errors+=("ufw is not active")
  fi
  if ! grep -qE '^Default:\s*deny \(incoming\)' <<<"$status_out"; then
    errors+=("default incoming policy is not 'deny'")
  fi
  # ufw prints the rule as "80/tcp    ALLOW IN    <LAN_SUBNET>" — allow
  # arbitrary whitespace between columns (ufw column-aligns them, so exact
  # spacing depends on what else is in the ruleset).
  if ! grep -qE "^80/tcp[[:space:]]+ALLOW IN[[:space:]]+${LAN_SUBNET//./\\.}" <<<"$status_out"; then
    errors+=("no '80/tcp ALLOW IN ${LAN_SUBNET}' rule found")
  fi

  if [ "${#errors[@]}" -eq 0 ]; then
    pass 4 "ufw active, default-deny incoming, LAN-subnet allow rule for tcp/80"
  else
    fail 4 "ufw active, default-deny incoming, LAN-subnet allow rule for tcp/80" \
      "$(IFS='; '; echo "${errors[*]}")"$'\n'"$status_out"
  fi
}

# ---- check 5: DOCKER-USER iptables rule ------------------------------------

check_5() {
  if [[ -z "${DEFAULT_IFACE}" ]]; then
    fail 5 "DOCKER-USER chain contains the LAN-only DROP rule for tcp/80" \
      "could not auto-detect the default-route interface — cannot verify which interface the rule should be scoped to"
    return
  fi

  local rule_out
  if ! rule_out="$(iptables -L DOCKER-USER -n -v 2>&1)"; then
    fail 5 "DOCKER-USER chain contains the LAN-only DROP rule for tcp/80" "$rule_out"
    return
  fi

  # Substring match rather than strict column parsing — iptables-legacy vs
  # iptables-nft compatibility mode format their -v -n output slightly
  # differently (spacing, whether "!" is its own column or glued to the
  # address), and this combination of tokens (DROP + tcp + this exact
  # interface + this exact LAN subnet + dpt:80, all appearing together on
  # one line) is specific enough that a false positive is effectively
  # impossible — this is exactly the rule setup-ufw.sh inserts:
  #   iptables -I DOCKER-USER -i <iface> ! -s <LAN_SUBNET> -p tcp --dport 80 -j DROP
  local matched
  matched="$(awk -v iface="$DEFAULT_IFACE" -v subnet="$LAN_SUBNET" '
    /DROP/ && /tcp/ && $0 ~ iface && index($0, subnet) > 0 && /dpt:80/ { found=1 }
    END { exit !found }
  ' <<<"$rule_out"; echo $?)"

  if [[ "$matched" == "0" ]]; then
    pass 5 "DOCKER-USER chain contains the LAN-only DROP rule for tcp/80 on ${DEFAULT_IFACE}"
  else
    fail 5 "DOCKER-USER chain contains the LAN-only DROP rule for tcp/80 on ${DEFAULT_IFACE}" \
      "no line in 'iptables -L DOCKER-USER -n -v' matched DROP+tcp+${DEFAULT_IFACE}+${LAN_SUBNET}+dpt:80"$'\n'"$rule_out"
  fi
}

# ---- check 6: M7-01 no-egress ----------------------------------------------
#
# `agent-server`, `model-runner`, `code-exec-manager`, and `postgres` were
# all moved onto `homeai-internal` (`internal: true`) by M7-01. This probes
# each one for real, from inside the container, rather than trusting the
# compose file's declared intent.

# Prints "python3" or "bash" (whichever tool the container actually has),
# or "none" if neither is present to even attempt the probe.
egress_probe_tool() {
  local cid="$1"
  if docker exec "$cid" sh -c 'command -v python3' >/dev/null 2>&1; then
    echo python3
  elif docker exec "$cid" sh -c 'command -v bash' >/dev/null 2>&1; then
    echo bash
  else
    echo none
  fi
}

# $1: container id/name. Returns 0 if an outbound TCP connect to a public
# IP (1.1.1.1:443, 3s timeout) FAILS (the expected, no-egress outcome), 1 if
# it SUCCEEDS (egress leaked -- assertion violated), 2 if neither python3
# nor bash's `/dev/tcp` pseudo-device is available in the container to even
# run the probe (inconclusive, per the ticket's own "use python3 -c or bash
# /dev/tcp, whichever the image has" wording -- if it has neither, that's
# a script gap to report, not a silent pass).
egress_blocked() {
  local cid="$1" tool
  tool="$(egress_probe_tool "$cid")"
  case "$tool" in
    python3)
      if docker exec "$cid" python3 -c "
import socket, sys
try:
    socket.create_connection(('1.1.1.1', 443), 3)
    sys.exit(1)  # connected -- egress leaked
except Exception:
    sys.exit(0)  # blocked/timed out -- expected
" >/dev/null 2>&1; then
        return 0
      else
        return 1
      fi
      ;;
    bash)
      if docker exec "$cid" timeout 3 bash -c 'exec 3<>/dev/tcp/1.1.1.1/443' >/dev/null 2>&1; then
        return 1  # connected -- egress leaked
      else
        return 0  # blocked/timed out -- expected
      fi
      ;;
    *)
      return 2
      ;;
  esac
}

check_6() {
  local svc cid rc
  for svc in agent-server model-runner code-exec-manager postgres; do
    cid="$(docker compose ps -q "$svc" 2>/dev/null)"
    if [ -z "$cid" ]; then
      fail 6 "no-egress: ${svc} (homeai-internal) cannot reach 1.1.1.1:443" \
        "container not running -- is the stack up? (docker compose up -d)"
      continue
    fi
    egress_blocked "$cid"
    rc=$?
    case "$rc" in
      0) pass 6 "no-egress: ${svc} (homeai-internal) cannot reach 1.1.1.1:443" ;;
      1) fail 6 "no-egress: ${svc} (homeai-internal) cannot reach 1.1.1.1:443" \
           "connection to 1.1.1.1:443 SUCCEEDED -- egress leaked, internal: true not holding" ;;
      2) fail 6 "no-egress: ${svc} (homeai-internal) cannot reach 1.1.1.1:443" \
           "neither python3 nor bash found in the ${svc} container -- could not run the probe" ;;
    esac
  done
}

# ---- check 7: M7-01 internal reachability still works ---------------------

check_7() {
  local cid out rc

  cid="$(docker compose ps -q agent-server 2>/dev/null)"
  if [ -z "$cid" ]; then
    fail 7 "agent-server -> model-runner:8080 and agent-server -> postgres:5432 still succeed (homeai-internal)" \
      "agent-server container not running -- is the stack up? (docker compose up -d)"
    return
  fi

  if out="$(docker exec "$cid" python3 -c "
import socket, sys
try:
    socket.create_connection(('model-runner', 8080), 3)
except Exception as e:
    print(f'model-runner:8080 error: {e}')
    sys.exit(1)
" 2>&1)"; then
    rc=0
  else
    rc=$?
  fi
  if [ "$rc" -eq 0 ]; then
    pass 7 "agent-server -> model-runner:8080 succeeds (homeai-internal)"
  else
    fail 7 "agent-server -> model-runner:8080 succeeds (homeai-internal)" "$out"
  fi

  if out="$(docker exec "$cid" python3 -c "
import socket, sys
try:
    socket.create_connection(('postgres', 5432), 3)
except Exception as e:
    print(f'postgres:5432 error: {e}')
    sys.exit(1)
" 2>&1)"; then
    rc=0
  else
    rc=$?
  fi
  if [ "$rc" -eq 0 ]; then
    pass 7 "agent-server -> postgres:5432 succeeds (homeai-internal)"
  else
    fail 7 "agent-server -> postgres:5432 succeeds (homeai-internal)" "$out"
  fi
}

# ---- check 8: M7-01 UI still served on :80 from the LAN --------------------
#
# Same assertion `scripts/e2e/gate_m2.sh`'s `step_web_build_serves` makes
# (the Expo web bundle's own `<script src=".../_expo/static/js/...">` tag),
# run here over the real mDNS name instead of `localhost` -- proves `caddy`
# staying on `homeai-net` (the one network that keeps its published port)
# means the network split had no effect on what every device on the LAN
# actually hits.

check_8() {
  local out rc
  if out="$(python3 -c "
import urllib.request, sys
try:
    with urllib.request.urlopen('http://homeai.local/', timeout=5) as resp:
        status = resp.status
        body = resp.read().decode(errors='replace')
except Exception as e:
    print(f'error: {e}')
    sys.exit(1)
has_tag = '_expo/static/js/' in body
if status != 200 or not has_tag:
    print(f'status={status} expo_bundle_tag_found={has_tag}')
    sys.exit(1)
print(f'status={status}, Expo bundle tag found')
" 2>&1)"; then
    rc=0
  else
    rc=$?
  fi
  if [ "$rc" -eq 0 ]; then
    pass 8 "GET http://homeai.local/ -> 200 with Expo bundle tag (UI still served on :80 from the LAN) -- ${out}"
  else
    fail 8 "GET http://homeai.local/ -> 200 with Expo bundle tag (UI still served on :80 from the LAN)" "$out"
  fi
}

summary() {
  local total=$((PASS_COUNT + FAIL_COUNT))
  echo
  if [ "$FAIL_COUNT" -eq 0 ]; then
    printf '%sALL %d/%d CHECKS PASSED%s\n' "$GREEN" "$PASS_COUNT" "$total" "$NC"
  else
    printf '%s%d/%d CHECKS PASSED - %d FAILED (checks: %s)%s\n' \
      "$RED" "$PASS_COUNT" "$total" "$FAIL_COUNT" "${FAILED_CHECKS[*]}" "$NC"
  fi
}

main() {
  check_1
  check_2
  check_3
  check_4
  check_5
  check_6
  check_7
  check_8
  summary
}

main
[ "$FAIL_COUNT" -eq 0 ]
