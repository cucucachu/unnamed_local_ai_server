#!/usr/bin/env bash
# check_socket_exclusivity.sh — M4-03: enforce that code-exec-manager is the
# ONLY compose service with docker.sock mounted.
#
# README.md's "Isolation boundary" / "Documented future hardening (not v1)"
# sections are explicit that code-exec-manager is meant to be the sole
# docker.sock holder in this stack (a docker-socket-proxy in front of it is
# a deliberately-deferred fast-follow, not a v1 requirement) - this script
# is the automated check for that invariant, reused as-is by later tickets
# (M4-05 and a future CI gate) rather than each reimplementing the same
# `docker compose config` parse.
#
# Resolves the FULL effective compose config (`docker compose config
# --format json` - this repo's compose version supports it, confirmed
# working against `docker-compose.yml` as of M4-03) so it catches a
# docker.sock mount regardless of whether it's declared as a short (`- src:
# dst`) or long (`source:`/`target:`) volume form, or introduced via a
# `docker-compose.override.yml` merge - `docker compose config` always
# normalizes to the long form once resolved, which is what's inspected
# below via `jq`.
#
# Fails (exit 1) if ANY service other than `code-exec-manager` has a
# volume/mount whose source OR target path contains "docker.sock". Exits 0
# if only `code-exec-manager` (or no service at all) mounts it.
#
# Usage:
#   scripts/check_socket_exclusivity.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$REPO_ROOT"

ALLOWED_SERVICE="code-exec-manager"

log() {
  echo "[check-socket-exclusivity] $(date '+%H:%M:%S') $*"
}

log "Resolving compose config..."
CONFIG_JSON="$(docker compose config --format json)"

# One offending "service: source -> target" line per violation, empty output
# if the invariant holds.
OFFENDERS="$(echo "$CONFIG_JSON" | jq -r --arg allowed "$ALLOWED_SERVICE" '
  .services
  | to_entries[]
  | select(.key != $allowed)
  | .key as $svc
  | (.value.volumes // [])[]
  | select(
      ((.source // "") | test("docker\\.sock"))
      or ((.target // "") | test("docker\\.sock"))
    )
  | "\($svc): \(.source // "?") -> \(.target // "?")"
')"

if [ -n "$OFFENDERS" ]; then
  log "FAIL: found docker.sock mount(s) outside of '${ALLOWED_SERVICE}':"
  echo "$OFFENDERS" | while IFS= read -r line; do
    log "  - ${line}"
  done
  exit 1
fi

log "OK: no service other than '${ALLOWED_SERVICE}' mounts docker.sock."
exit 0
