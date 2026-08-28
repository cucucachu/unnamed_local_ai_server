# M6-01 — mDNS + firewall verification, NETWORKING.md complete

**Milestone**: M6 · **Size**: S · **Depends on**: M0-02 · **Blocks**: M6-03

## Context

M0-02 wrote and ran the scripts; this ticket verifies the LAN posture against the *full running
stack* and finishes `docs/NETWORKING.md` (PLAN.md P5-2). Isolation claim to verify: reachable
by name from the LAN, port 80 only, nothing else exposed.

## Spec

1. **`scripts/verify_network.sh`** (run on the host, stack up):
   - `avahi-resolve -n homeai.local` returns a LAN IP of this host.
   - `curl http://homeai.local/api/health` → ok (mDNS + proxy end to end from the host).
   - Port audit: `ss -tlnp` (or `docker ps --format`) shows Docker publishing **only** port 80
     (plus sshd on 22 outside Docker); fail if any other homeai container publishes a port.
   - `ufw status` contains the LAN-subnet rule for 80 and default-deny incoming.
   - `iptables -L DOCKER-USER -n` contains the M0-02 rule (or documents single-NIC no-op).
2. **Finish `docs/NETWORKING.md`**: add "Port & exposure audit" (what verify_network checks and
   why), "Adding a device" (it's just WiFi + the URL; Expo Go needs `EXPO_PUBLIC_API_HOST`),
   "Troubleshooting mDNS" (Android quirks: some Android versions don't resolve `.local` in the
   browser — document the fallback: use the host's LAN IP directly, and that this is a known
   platform limitation, not a bug), "What would change for internet exposure" (one paragraph:
   don't; VPN/wireguard is the sanctioned path, references PLAN.md fast-follows).

## Out of scope

TLS, VPN setup, router configuration.

## Acceptance criteria (Tier A)

- [ ] `scripts/verify_network.sh` green on the host with the full stack up.
- [ ] `docs/NETWORKING.md` sections complete (grep the four headings).

## Tier B (append to docs/HOST-CHECKS.md under M6)

- [ ] Phone browser (iOS + Android if available): `http://homeai.local` loads the app; note
      Android mDNS result.
- [ ] From a non-LAN network (phone on cellular): the address does not load.
