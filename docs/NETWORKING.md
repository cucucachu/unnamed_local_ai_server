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

### Known gotcha: avahi publishing an unusable IPv6 (link-local) record

By default `avahi-daemon` publishes both an A (IPv4) and AAAA (IPv6) record
for `homeai.local`. On a host with no routable IPv6 on the LAN — just a
link-local `fe80::...` address, which is the common case for a home network —
that AAAA record is useless to other devices: link-local addresses only mean
something in combination with the *originating* device's own network
interface (a "zone index"), which mDNS doesn't and can't communicate to a
different device. Some clients (observed: phone browsers on a fresh
navigation, and Expo Go's React Native networking stack) pick the AAAA
record over the A record and the connection just **hangs** — no error, just
an infinite loading spinner — rather than falling back to IPv4.

`setup-avahi.sh` fixes this by setting `use-ipv6=no` in
`/etc/avahi/avahi-daemon.conf` and verifying `avahi-resolve -n homeai.local`
returns an IPv4 address (not something containing a `:`). If `homeai.local`
ever starts hanging again for phone clients specifically (while curl/wget
from another Linux box on the LAN works fine), re-run `sudo
infra/host/setup-avahi.sh --keep-hostname` and check its "Resolution" line.

### Known gotcha: avahi publishing a Docker bridge address instead of the LAN IP

By default avahi publishes records on **every** non-loopback interface it
sees — including Docker's virtual bridges (`docker0`, plus a `br-*` per
compose project). Those addresses (e.g. `172.18.0.1`) are only reachable
from the host itself, never from a phone on the LAN, but avahi doesn't know
that and may answer a query with one of them instead of the real LAN IP.

`setup-avahi.sh` fixes this by setting `allow-interfaces=<iface>` in
`avahi-daemon.conf`, where `<iface>` is auto-detected from `ip route show
default` (same technique `setup-ufw.sh` uses for the `DOCKER-USER` rule),
and verifies the resolved address actually matches that interface's current
IP. If you ever change which NIC has the default route (e.g. switch from
Wi-Fi to Ethernet), re-run `sudo infra/host/setup-avahi.sh --keep-hostname`
to re-point avahi at the new interface.

### How this handles the host's IP changing (DHCP)

This laptop gets its LAN IP from DHCP, so it can and will change (router
reboot, lease renewal, reconnecting to the network, etc.) — nothing in this
setup assumes a fixed IP. `homeai.local` (mDNS) is precisely the mechanism
that makes that a non-issue: `avahi-daemon` watches its allowed interface
for address changes at runtime and re-announces automatically, with no
restart needed. The frontend never hardcodes an IP anywhere — the web build
is same-origin relative, and native builds use `EXPO_PUBLIC_API_HOST=http://
homeai.local` (`.env.example`) — so as long as avahi is correctly scoped to
the real LAN interface (the two gotchas above), a changed IP is invisible
to every client; they just re-resolve the name.

The one thing DHCP-provided IPs don't give you is a guarantee that the
*name* survives a full reboot with a fresh interface, or that some other
device on the network won't answer `homeai.local` first if this host is
briefly offline — neither is a practical problem for a single-host home
LAN. If you want the IP itself to also be stable (e.g. for a router-level
firewall rule, or troubleshooting without relying on mDNS), the standard
options, roughly in order of how much they're worth the effort here:

- **DHCP reservation on the router** — bind this laptop's MAC to a fixed IP
  in the router's DHCP settings. Doesn't change anything in this repo;
  purely a router-side setting. Most home routers support it.
- **Static IP on the host** (`netplan`/`NetworkManager`) — more
  fragile if you ever move to a different network (laptop, after all), so a
  DHCP reservation is generally preferable for a laptop specifically.
- **Do nothing** — mDNS already solves the "how do clients find it"
  problem; a changing IP is only inconvenient if you're bypassing
  `homeai.local` and hardcoding an IP somewhere yourself.

## Verifying from a phone

_(Completed by ticket M6-01, once `caddy` is actually serving something on
port 80 — see `docs/HOST-CHECKS.md` for the Tier B checklist items.)_
