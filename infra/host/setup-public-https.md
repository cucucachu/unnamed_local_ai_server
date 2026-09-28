# Public HTTPS (opt-in, human host steps)

Agents must **not** run these against the live firewall or router. Enabling
the Settings toggle does **not** punch `ufw` / `DOCKER-USER` / port
forwards. That is the whole point of the opt-in: policy first, WAN later.

## Order

1. **Domain mode (M15-03)** — set `HOMEAI_DOMAIN` (and `DUCKDNS_TOKEN`) so
   Caddy serves `https://$HOMEAI_DOMAIN` with ACME DNS-01. Split-DNS the
   name to this host's LAN IPv4 (`docs/NETWORKING.md`). Do not set
   `HOMEAI_DOMAIN` just to try this; that starts ACME. Tests may use
   `WEBAUTHN_RP_ID=localhost` without a domain; a real public listener
   needs the real name as the RP ID.
2. **Settings toggle** — on the LAN or VPN, a stepped-up admin turns on
   **Allow public HTTPS**. Enabling without an RP ID is refused
   (`422 domain_required`). Disabling is always allowed.
3. **WAN 443 (optional, you accept the threat model)** — only after 1 and
   2, allow inbound **TCP 443** (ufw + router forward, and whatever
   `DOCKER-USER` change you are willing to make). Do **not** require
   HTTP-01. **TCP 80 stays LAN-scoped** (`/ca.crt`, Expo Go). There is
   **no HSTS on `:80` or `https://homeai.local`**. Optional HSTS is only
   on `https://$HOMEAI_DOMAIN` (Caddy `auto_https disable_redirects`
   stays — HTTP is not redirected to HTTPS).

UDP 51820 (WireGuard) is a separate human step (`setup-wireguard.md`).

## Threat notes (also in `docs/NETWORKING.md`)

- Passkey-only login is **classifier + DB flag**, not the firewall. If you
  skip step 3, the public internet still cannot reach `:443`.
- Docker SNAT: a WAN client on host-published `:80`/`:443` may show Caddy
  a `172.16.0.0/12` address. While the flag is on, that range is **not**
  LAN. Caddy strips client `X-HomeAI-Via` then sets `X-HomeAI-Via: vpn`
  when the TCP peer is the `wireguard` compose service, so tunnel HTTP
  stays privileged.
- Last-hop `X-Forwarded-For` only. Do not add `trusted_proxies`. Do not
  enable PROXY protocol on `:80`/`:443`. Do not put Caddy in
  `network_mode: host`.
- Native apps: Expo Go (`X-HomeAI-Client: native`) still uses password;
  the host app (`host`) uses device pairing (M15-06). Enrollment, invites,
  and admin stay M15-02
  (`403 public_origin` on a public origin) — public HTTPS does not invent
  a second policy.
