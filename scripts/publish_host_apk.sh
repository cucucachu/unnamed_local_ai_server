#!/usr/bin/env bash
# Copy the host-app APK to the root of every active user's personal space
# (Files → Personal), so anyone can download it to their phone.
#
#   scripts/publish_host_apk.sh [path/to/apk]
#
# Defaults to services/frontend/dist/homeai-host-release.apk and the file
# name HomeAI.apk (HOMEAI_APK_NAME). An existing file of that name is
# replaced. Runs inside the live platform container (docker compose -p
# homeai exec), which owns /data/spaces: each copy gets the user's uid, the
# space's gid and mode 0660, like an upload.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APK="${1:-$ROOT/services/frontend/dist/homeai-host-release.apk}"
NAME="${HOMEAI_APK_NAME:-HomeAI.apk}"
COMPOSE=(docker compose -p homeai)

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[[ -f "$APK" ]] || die "no APK at $APK"
case "$NAME" in
  */* | .* | "") die "HOMEAI_APK_NAME must be a plain file name" ;;
esac

# shellcheck disable=SC2016  # expanded by the container's shell
rows="$("${COMPOSE[@]}" exec -T postgres sh -c \
  'psql -U "$POSTGRES_USER" -d homeai_platform -Atc "
    SELECT s.id, u.uid, s.gid, u.username FROM spaces s
    JOIN users u ON u.id = s.owner_user_id
    WHERE s.kind = '\''personal'\'' AND s.archived_at IS NULL AND u.disabled_at IS NULL
    ORDER BY u.username"')"
[[ -n "$rows" ]] || die "no personal spaces found"

while IFS='|' read -r space_id uid gid username; do
  # The container's rootfs is read-only, so the APK streams in on stdin.
  # files/ is group-writable by exec containers: write a fresh mktemp file
  # (O_EXCL) and rename it into place, so a planted symlink is replaced, not
  # followed.
  # shellcheck disable=SC2016  # expanded by the container's shell
  "${COMPOSE[@]}" exec -T platform sh -euc '
    dir="/data/spaces/$1/files"
    tmp="$(mktemp "$dir/.homeai-apk.XXXXXX")"
    trap "rm -f \"\$tmp\"" EXIT
    cat > "$tmp"
    chown "$2:$3" "$tmp"
    chmod 0660 "$tmp"
    mv -fT "$tmp" "$dir/$4"
    trap - EXIT
  ' sh "$space_id" "$uid" "$gid" "$NAME" < "$APK"
  printf 'published %s to %s (Personal)\n' "$NAME" "$username"
done <<< "$rows"
