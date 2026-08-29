# Networking

How this host is made reachable at `homeai.local` on the LAN, and how it stays
LAN-only. See also the README's "Network isolation" architecture note.

## One-time host setup

Run these in order (all idempotent, all under `infra/host/`, all need `sudo`):

1. **Workspace** — `sudo infra/host/setup-workspace.sh`
   Creates `$WORKSPACE_DIR` (default `/srv/homeai/workspace`), owned by
   `$HOMEAI_UID:$HOMEAI_GID`.
2. **GPU driver check** — read `infra/host/setup-gpu-drivers.md` and follow it
   manually (kernel version, `amdgpu`, Mesa/Vulkan, `render`/`video` group
   membership). Not a script — see that doc for why.
3. **Avahi (mDNS)** — `sudo infra/host/setup-avahi.sh`
   Sets the hostname to `homeai` (unless `--keep-hostname` is passed) and
   verifies `homeai.local` resolves over mDNS.
4. **Firewall** — `sudo infra/host/setup-ufw.sh`
   LAN-only `ufw` rule for port 80 plus the `DOCKER-USER` iptables rule (see
   below for why both are needed).

Re-run any of these safely at any time — they're all written to converge on
the same end state rather than fail on a second run.

## How LAN-only isolation works

Three independent layers, because no single one is sufficient on its own:

1. **Only `caddy` publishes a host port.** Every other compose service
   (`agent-server`, `model-runner`, `postgres`, `code-exec-manager`) is only
   reachable from inside the `homeai-net` bridge network — there's no host
   port to attack even from the LAN.
2. **`ufw`** enforces LAN-only access for anything running *directly on the
   host* (default-deny incoming, `192.168.x.x/24 -> tcp/80` allowed,
   `OpenSSH` allowed if installed). On its own this does **not** protect
   `caddy`'s published port — see the next point.
3. **The `DOCKER-USER` iptables chain.** Docker manages its own
   iptables/nftables NAT rules for published container ports, and inserts
   them *ahead of* `ufw`'s chain — this is a well-known Docker/ufw
   interaction, not a bug in this setup. So a bare `ufw allow`/`deny` on port
   80 is silently bypassed for traffic Docker is forwarding to the `caddy`
   container. The real fix, which `setup-ufw.sh` implements, is a rule
   directly in `DOCKER-USER` (which Docker guarantees to consult first):

   ```
   iptables -I DOCKER-USER -i <default-route-iface> ! -s <LAN_SUBNET> -p tcp --dport 80 -j DROP
   ```

   `setup-ufw.sh` auto-detects `<default-route-iface>` from
   `ip route show default` and reads `<LAN_SUBNET>` from `.env`. On a host
   with a single LAN-facing NIC this is close to a no-op in practice, but
   it's kept anyway — belt and braces, and it documents intent.

   **Persistence gotcha**: rules added to `DOCKER-USER` via a bare `iptables`
   command only live in the kernel's in-memory ruleset and are lost on
   reboot. `setup-ufw.sh` installs `iptables-persistent`
   (`netfilter-persistent`) non-interactively and runs
   `netfilter-persistent save` after adding the rule so it survives reboots.

4. **mDNS (`avahi-daemon`)** advertises `homeai.local` so LAN devices can find
   the host by name without router DNS changes — this is a convenience, not
   a security boundary.

Also out of scope for v1 by design (see README): TLS/HTTPS, auth,
docker-socket-proxy, router/VLAN changes.

### Known gotcha: `avahi-daemon` doesn't notice a live hostname change

`avahi-daemon` reads the system hostname once at startup and registers its
mDNS records from that snapshot — running `hostnamectl set-hostname` while
it's already running does **not** make it start answering to the new name.
`setup-avahi.sh` handles this by restarting the service whenever it changes
the hostname. If you ever change the hostname manually outside that script,
follow it with:

```bash
sudo systemctl restart avahi-daemon
```

## Verifying from a phone

_(Completed by ticket M6-01, once `caddy` is actually serving something on
port 80 — see `docs/HOST-CHECKS.md` for the Tier B checklist items.)_
