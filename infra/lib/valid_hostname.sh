# Shared HOMEAI_DOMAIN check (M15-03). Sourced by the Caddy and WireGuard
# entrypoints and by scripts/verify_caddy_domain.sh.
#
# A valid name is an ASCII DNS hostname (RFC 1123 labels) with at least one
# dot, no whitespace, quotes, or Caddyfile/dnsmasq metacharacters. Callers
# must check this *before* interpolating the value into a Caddyfile site
# address or a dnsmasq --address= flag.

valid_hostname() {
  # Usage: valid_hostname "$name" — empty stderr+nonzero on failure.
  # Prints nothing on success (never log secrets; this is not the token).
  _name="${1-}"
  if [ -z "$_name" ]; then
    echo "hostname is empty" >&2
    return 1
  fi
  case "$_name" in
    *[[:space:]]*|*[\'\"]*|*[\$\`\\]*)
      echo "hostname contains whitespace, quotes, or shell metacharacters" >&2
      return 1
      ;;
  esac
  # One or more labels: start/end alnum, interior alnum or hyphen, 1-63
  # chars each; whole name <= 253. At least one dot so a single label
  # (e.g. "localhost") cannot become an ACME site address.
  if ! printf '%s' "$_name" | grep -Eq \
    '^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$'; then
    echo "hostname is not a valid DNS name" >&2
    return 1
  fi
  if [ "${#_name}" -gt 253 ]; then
    echo "hostname is longer than 253 characters" >&2
    return 1
  fi
  return 0
}
