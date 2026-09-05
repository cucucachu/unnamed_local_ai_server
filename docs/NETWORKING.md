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
   LAN-only `ufw` rules for ports 80 and 443 plus the matching
   `DOCKER-USER` iptables rules (see below for why both are needed).

Re-run any of these safely at any time — they're all written to converge on
the same end state rather than fail on a second run.

## How LAN-only isolation works

Three independent layers, because no single one is sufficient on its own:

1. **Only `caddy` publishes host ports (80 and 443).** Every other compose
   service (`agent-server`, `model-runner`, `postgres`, `code-exec-manager`)
   is only reachable from inside a Docker bridge network — there's no host
   port to attack even from the LAN. (As of M7-01, that network is
   `homeai-internal`, not `homeai-net` — see docs/ARCHITECTURE.md §5's
   "Network segmentation" section for the full internal/egress split; this
   LAN-only story is unaffected either way.) 443 is the one intentional
   exception to the original v1 "no new published ports" rule (M9-05,
   local HTTPS).
2. **`ufw`** enforces LAN-only access for anything running *directly on the
   host* (default-deny incoming, `192.168.x.x/24 -> tcp/80` and `tcp/443`
   allowed, `OpenSSH` allowed if installed). On its own this does **not**
   protect `caddy`'s published ports — see the next point.
3. **The `DOCKER-USER` iptables chain.** Docker manages its own
   iptables/nftables NAT rules for published container ports, and inserts
   them *ahead of* `ufw`'s chain — this is a well-known Docker/ufw
   interaction, not a bug in this setup. So a bare `ufw allow`/`deny` on
   ports 80/443 is silently bypassed for traffic Docker is forwarding to
   the `caddy` container. The real fix, which `setup-ufw.sh` implements, is
   a rule per published port directly in `DOCKER-USER` (which Docker
   guarantees to consult first):

   ```
   iptables -I DOCKER-USER -i <default-route-iface> ! -s <LAN_SUBNET> -p tcp --dport 80 -j DROP
   iptables -I DOCKER-USER -i <default-route-iface> ! -s <LAN_SUBNET> -p tcp --dport 443 -j DROP
   ```

   `setup-ufw.sh` auto-detects `<default-route-iface>` from
   `ip route show default` and reads `<LAN_SUBNET>` from `.env`. On a host
   with a single LAN-facing NIC this is close to a no-op in practice, but
   it's kept anyway — belt and braces, and it documents intent.

   **Persistence gotcha**: rules added to `DOCKER-USER` via a bare `iptables`
   command only live in the kernel's in-memory ruleset and are lost on
   reboot. The obvious fix — `apt-get install iptables-persistent` — is
   actually a trap on this Ubuntu release: `ufw`'s own package declares
   `Breaks: iptables-persistent, netfilter-persistent` (confirmed via
   `apt-cache show ufw`), so installing either one **silently uninstalls
   `ufw` itself** as part of the same transaction (this was hit live during
   M6-01's development — a non-interactive `apt-get install -y` sails right
   past the "will be REMOVED: ufw" line). `setup-ufw.sh` instead installs a
   small systemd oneshot unit, `homeai-docker-user-fw.service`, ordered
   `After=docker.service`/`Requires=docker.service`, that re-inserts the
   same rule idempotently on every boot — no conflicting package, and
   correctly ordered after Docker (re)creates the `DOCKER-USER` chain from
   scratch on every `dockerd` start (which is what actually wipes a rule
   added any earlier in the boot sequence — plain boot-order luck, not
   reboot-survival, is why a cron `@reboot` entry alone wouldn't be
   reliable either). `sudo systemctl status homeai-docker-user-fw.service`
   shows whether it last ran successfully.

4. **mDNS (`avahi-daemon`)** advertises `homeai.local` so LAN devices can find
   the host by name without router DNS changes — this is a convenience, not
   a security boundary.

Also out of scope for v1 by design (see README): public ACME
certificates, forcing HTTPS / HSTS, auth, docker-socket-proxy,
router/VLAN changes. Local HTTPS for `homeai.local` (Caddy internal CA)
is in — see "Local HTTPS (Caddy internal CA)" below.

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

## Port & exposure audit

`scripts/verify_network.sh` (run with `sudo`, full stack up) is the
after-the-fact check that the three isolation layers above are actually
holding, against the real running system rather than just "the setup
scripts exited 0 once." Five checks:

1. **mDNS resolution** — `avahi-resolve -n homeai.local` returns an IPv4
   address matching the LAN interface's current IP (re-checks the exact two
   `setup-avahi.sh` gotchas above: no IPv6/link-local answer, no stale
   Docker-bridge answer).
2. **End-to-end reachability** — `GET http://homeai.local/api/health`
   returns `200`, proving mDNS + Caddy + agent-server all work together
   from the host's own point of view, not just each piece in isolation.
3. **Port audit, Docker-stack scope** — `docker compose config` and live
   `docker ps` output agree that **only** `caddy` publishes host ports,
   and that it publishes **only** ports 80 and 443. Deliberately scoped to
   the compose stack, not to every process on the dev machine: an
   unrelated host tool (an IDE helper, another project's dev server, etc.)
   listening on some other port isn't a regression in *this* stack's
   isolation posture, and flagging it would just be noise. `sshd` on port
   22 (if installed) is the one explicitly-allowed non-Docker exception —
   its own exposure is `ufw`'s job (check 4), not this check's.
4. **`ufw` posture** — active, default-deny incoming, and the LAN-subnet
   allow rules for tcp/80 and tcp/443 that `setup-ufw.sh` installs.
5. **`DOCKER-USER` chain** — contains the actual enforcement rules for
   both published ports (`ufw` alone does not restrict Docker-published
   ports; see "How LAN-only isolation works" above for why).

Re-run this any time after touching `docker-compose.yml`'s port mappings,
the firewall scripts, or the network hardware itself (new NIC, switched
from Wi-Fi to Ethernet, etc.).

## Adding a device

HTTP needs no per-device setup beyond "join the same Wi-Fi/LAN and know
the URL" — no accounts to create, no router changes.
`http://homeai.local` stays available on purpose (no HTTP→HTTPS redirect)
so Expo Go and phones that have not installed the local CA keep working.

HTTPS (`https://homeai.local`) needs the Caddy local CA installed once
per device — see "Local HTTPS (Caddy internal CA)" below. That is
confidentiality on the LAN (and a browser secure context for microphone
access), **not** authentication.

- **Any browser** (phone, laptop, tablet): navigate to `http://homeai.local`
  immediately, or `https://homeai.local` after installing the CA. If mDNS
  doesn't resolve on that specific device (see "Troubleshooting mDNS"
  below), use the host's LAN IP directly instead — `verify_network.sh`
  prints it, or check `ip -4 addr` on the host. HTTPS by IP will not match
  the `homeai.local` certificate; use the name, or stay on HTTP.
- **Expo Go** (iOS/Android): the native app has no "origin" to be relative
  to the way the web build does, so it needs an explicit API host. Set
  `EXPO_PUBLIC_API_HOST=http://homeai.local` in `services/frontend/.env`
  (see `.env.example`) before starting the Expo dev server, or
  `http://<LAN-IP>` if mDNS isn't resolving on that device. Once the phone
  trusts the CA you may set `EXPO_PUBLIC_API_HOST=https://homeai.local`
  (`lib/api.ts` maps that to `wss://`). Leave `http://` if the device
  does not have the CA — phones without it would break if we forced
  HTTPS.

## Local HTTPS (Caddy internal CA)

Caddy issues a certificate for `homeai.local` from its own local CA
(`tls internal` in `infra/caddy/Caddyfile`). Each device that wants
`https://homeai.local` (lock icon, no warning, `wss://` chat, browser
microphone access) installs that root **once**. HTTP on `:80` is not
redirected and stays the default for anything that has not installed
the CA.

TLS here is confidentiality on the LAN, not authentication — there is
still no login. Anyone on the allowed subnet who can reach the host has
the full API, HTTP or HTTPS.

### Get the root certificate

The public root (never the private key) is available two ways:

1. **HTTP on the LAN** — open `http://homeai.local/ca.crt` in a browser
   or fetch it from another device. Serving the CA over HTTP is a
   documented trade-off: the LAN is already trusted, and a device cannot
   use HTTPS to fetch the CA before it trusts the CA.
2. **Host copy** — `scripts/export-ca.sh` writes the same file to
   `${BACKUP_DIR}/homeai-root-ca.crt` (default
   `/srv/homeai/backups/homeai-root-ca.crt`). Re-run after the first
   HTTPS boot or after a rotation. Writing under `/srv` may need
   `sudo scripts/export-ca.sh`.

The CA lives in the `caddy-data` Docker volume (`/data` in the caddy
container) so it stays stable across `docker compose up` / container
recreates.

### Install on a device

**Android (Chrome / system user CA)**

1. Download `http://homeai.local/ca.crt` on the phone (Chrome will
   usually treat it as a download, not install it automatically).
2. Settings → Security → Encryption & credentials → Install a
   certificate → CA certificate (wording varies by OEM / Android
   version). Select the downloaded file.
3. Chrome 64+: also enable the user CA for that profile if prompted
   ("Trust on first use" / "CA installed" notification). Some OEMs
   hide this under Settings → Privacy → More security settings.
4. Open `https://homeai.local` — the warning should be gone. A chat
   turn should stream over `wss://`.

**iOS (Safari / system)**

1. Open `http://homeai.local/ca.crt` in Safari. iOS offers to download
   a profile.
2. Settings → Profile Downloaded → Install. Enter the device passcode.
3. Settings → General → About → Certificate Trust Settings → enable
   full trust for the Home AI / Caddy root.
4. Open `https://homeai.local` in Safari.

**macOS**

1. Fetch the cert (`http://homeai.local/ca.crt` or copy
   `homeai-root-ca.crt` from the host).
2. Double-click it, or `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain homeai-root-ca.crt`.
3. Keychain Access → the cert → Get Info → Trust → "When using this
   certificate: Always Trust" if the `security` command was not used.
4. Restart Chrome if it was already open (Chrome on macOS uses the
   system keychain).

**Windows**

1. Download `ca.crt` / `homeai-root-ca.crt`.
2. `certlm.msc` (Local Computer) → Trusted Root Certification
   Authorities → Certificates → All Tasks → Import. Or:
   `certutil -addstore -f Root homeai-root-ca.crt` (elevated).
3. Restart the browser.

**Linux / Chrome**

Chrome and Chromium on Linux do **not** use the OpenSSL system store.
Either:

- Import into Chrome: Settings → Privacy and security → Security →
  Manage certificates → Authorities → Import `homeai-root-ca.crt`,
  check "Trust this certificate for identifying websites", or
- System store (Firefox ESR / `curl` / Python):
  `sudo cp homeai-root-ca.crt /usr/local/share/ca-certificates/homeai-root-ca.crt && sudo update-ca-certificates`

**Firefox** (any OS) uses its own store: Settings → Privacy & Security
→ Certificates → View Certificates → Authorities → Import. Check
"Trust this CA to identify websites."

### Rotate the CA

Rotation is rare (the volume keeps the same CA across normal restarts).
Do it if the private key may have leaked, or after a deliberate
`docker volume rm` of `caddy-data`.

1. Stop caddy: `docker compose stop caddy`
2. Remove the volume (this destroys the old CA **and** its private
   key): `docker compose down` is not enough — the named volume
   persists. `docker volume rm homeai_caddy-data` after caddy is gone.
3. `docker compose up -d caddy` — Caddy mints a new local CA on first
   HTTPS request.
4. `scripts/export-ca.sh` (or re-download `http://homeai.local/ca.crt`).
5. On every device that had the old CA: delete the old root, then
   install the new one using the same steps as above. Until they do,
   `https://homeai.local` will warn; `http://homeai.local` is
   unaffected.

## Troubleshooting mDNS

`.local` resolution is a client-side feature, not something this server can
force — if a specific device won't resolve `homeai.local`, that's almost
always the device, not this setup:

- **iOS / macOS / most modern Android**: mDNS ("Bonjour") support is
  built into the OS's own resolver; `homeai.local` should just work in any
  browser or app once it's on the LAN.
- **Some Android versions / some browsers**: `.local` name resolution in
  the OS-level resolver used by `WebView`/Chrome on Android has historically
  been inconsistent across OEM builds and Android versions — this is a
  well-known platform limitation (Android's own DNS resolver doesn't
  implement multicast DNS the way iOS/desktop OSes do; some ROMs patch it
  in, many don't), not a bug in `avahi-daemon` or this repo's setup.
  **Fallback**: use the host's LAN IP directly (`http://192.168.x.x`)
  instead of `http://homeai.local` — works identically, just without the
  friendly name. `verify_network.sh`'s check 1 output shows the current IP,
  or run `ip -4 addr show` on the host.
- **Expo Go specifically**: React Native's networking stack inherits
  whatever the OS resolver does, so the same Android quirk above applies;
  same fallback (`EXPO_PUBLIC_API_HOST=http://<LAN-IP>` instead of
  `http://homeai.local`).
- **A previously-working device suddenly can't resolve it**: check whether
  the host's IP changed and mDNS just hasn't been given a moment to
  re-announce (should be near-instant — see "How this handles the host's
  IP changing" above), or whether the *device* changed networks (mDNS
  never crosses subnets/routers by design — both the host and the client
  must be on the same LAN segment).

## What would change for internet exposure

Nothing here is designed for it, and the recommendation is: don't — this
setup's entire security model (no auth, no rate limiting, an
`execute_code` tool that runs arbitrary shell commands) assumes a trusted
LAN, and none of that is safe to expose to the public internet as-is.
Local HTTPS encrypts the LAN hop; it does not authenticate callers.
If remote access is ever genuinely needed (e.g. checking on a home server
while traveling), the sanctioned path is a VPN — WireGuard is the natural
fit (lightweight, works well on a home router or as a container on this
same host) — so a remote device joins the LAN itself (or an
equivalent virtual one) and everything above just works unchanged, rather
than trying to safely expose `homeai.local`/ports 80 and 443 directly to
the internet. See README.md's "Documented fast-follows" for where this and
the remaining hardening (shared-password auth at the proxy, public ACME
certs) are tracked — none of that is in scope for v1.

## Verifying from a phone

See `docs/HOST-CHECKS.md`'s `## M6` section for the Tier B checklist:
phone-browser reachability (iOS + Android, noting any Android mDNS
fallback needed) and confirming a non-LAN network (phone on cellular)
cannot reach it at all.
