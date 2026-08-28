# M0-02 — Host setup scripts (workspace, avahi, ufw, GPU doc)

**Milestone**: M0 · **Size**: M · **Depends on**: M0-01 · **Blocks**: M6-01

## Context

One-time host preparation, run manually (not from compose): the shared workspace directory,
mDNS name advertising, LAN-only firewall, and a written GPU-driver verification procedure.
PLAN.md §"Network isolation" and P0-1/P0-3.

## Spec

All scripts in `infra/host/`, bash, `set -euo pipefail`, idempotent (safe to re-run), must be
run with sudo, and print a clear summary of what they did. Each reads `.env` from the repo root
(`source` after stripping comments, or parse with grep) for its inputs.

1. **`setup-workspace.sh`**
   - Creates `$WORKSPACE_DIR` (default `/srv/homeai/workspace`).
   - `chown -R $HOMEAI_UID:$HOMEAI_GID`, `chmod 775`.
   - Prints resulting ownership.

2. **`setup-avahi.sh`**
   - Installs `avahi-daemon` (apt), enables + starts the service.
   - Sets system hostname to `homeai` (`hostnamectl set-hostname homeai`) so mDNS advertises
     `homeai.local`. Warn (don't fail) if hostname already differs deliberately — overwrite is
     the default behavior, add `--keep-hostname` flag to skip.
   - Verifies with `avahi-resolve -n homeai.local` (retry up to 10 s).

3. **`setup-ufw.sh`**
   - Installs ufw if missing.
   - `ufw allow from $LAN_SUBNET to any port 80 proto tcp`
   - `ufw allow OpenSSH` (do NOT lock the user out).
   - Default deny incoming, allow outgoing, then `ufw --force enable`.
   - Prints `ufw status verbose` at the end.
   - **Important**: must also handle the Docker caveat — Docker publishes ports via iptables
     and bypasses ufw. Since only Caddy publishes a port, bind it to all interfaces but document
     in the script header and in `docs/NETWORKING.md` that the DOCKER-USER chain is the real
     enforcement point, and add the standard DOCKER-USER rule:
     `iptables -I DOCKER-USER -i <wan-iface> ! -s $LAN_SUBNET -p tcp --dport 80 -j DROP` — implement
     as a separate managed block in the script with the interface auto-detected from the default
     route (`ip route show default`). If the host has a single NIC on the LAN this is a no-op in
     practice; keep it anyway (belt and braces, and it documents intent).

4. **`setup-gpu-drivers.md`** (doc, not script)
   - How to verify: kernel ≥ 6.10 recommended for gfx1150, Mesa/RADV recent, `/dev/dri/renderD128`
     exists, user in `render` and `video` groups, `vulkaninfo --summary` shows the Radeon 890M.
   - How to install Mesa/Vulkan packages on Ubuntu 24.04 (`mesa-vulkan-drivers`, `vulkan-tools`).
   - How to get `RENDER_GID`/`VIDEO_GID` for `.env`.

5. **`docs/NETWORKING.md`** — start it: sections "One-time host setup" (ordered: workspace →
   gpu doc → avahi → ufw), "How LAN-only isolation works" (proxy-only publish + ufw +
   DOCKER-USER + mDNS, from PLAN.md), "Verifying from a phone". M6-01 completes it.

## Out of scope

Running the scripts against the live firewall as part of this ticket's tests beyond what's
listed below (M6-01 does final verification); TLS; router changes.

## Acceptance criteria (Tier A)

- [ ] `bash -n` passes and `shellcheck` reports no errors (`shellcheck` may be `apt`-installed)
      for all three scripts.
- [ ] `setup-workspace.sh` run for real: `/srv/homeai/workspace` exists with `.env` UID/GID.
- [ ] `setup-avahi.sh` run for real: `avahi-resolve -n homeai.local` returns the host IP.
- [ ] `setup-ufw.sh` run for real on the host: `ufw status` shows port-80-from-LAN rule and SSH
      allowed; an `iptables -L DOCKER-USER` entry exists per spec.
- [ ] `docs/NETWORKING.md` exists with the three sections.

## Tier B (append to docs/HOST-CHECKS.md under M0)

- [ ] From a phone on WiFi: `http://homeai.local` resolves (page may 502 until M0-03 — resolving
      is the check).
