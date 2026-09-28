# WireGuard on this host (M15-01)

Human-only steps. Agents must **not** run these against the live firewall,
router, or `ufw`. Creating a device QR in Settings works on the LAN without
them; reaching the box from cellular / another network needs the UDP hole
you punch yourself.

## 1. Kernel module

The `wireguard` compose service is `NET_ADMIN` only. It does **not** load
the module (`SYS_MODULE` / `privileged: true` are forbidden on this stack).

```bash
sudo modprobe wireguard
lsmod | grep wireguard
```

To load on boot (Debian/Ubuntu):

```bash
echo wireguard | sudo tee /etc/modules-load.d/wireguard.conf
```

If `modprobe` fails, install the tools/headers your kernel expects
(`linux-modules-extra-$(uname -r)` or the distro `wireguard` package) and
retry. `scripts/e2e/wireguard_smoke.sh` fails clearly when the module is
missing; it does not skip.

## 2. Confirm the sidecar

From the repo, after `docker compose up -d --build --no-deps platform wireguard`
(under `flock /tmp/homeai-stack.lock`, never recreating `model-runner` or
`postgres`):

```bash
docker compose ps wireguard
docker compose logs wireguard --tail 50
```

UDP **51820** should be published on the host. Tunnel subnet is
**10.13.13.0/24** (server **10.13.13.1**). That range is reserved so it
does not collide with a typical home LAN (`192.168.x`) or Docker
(`172.x`).

## 3. LAN QR

On the home Wi-Fi, Settings → Remote access → create a device → scan the
QR with the official WireGuard app. Default `Endpoint` is
`homeai.local:51820` (`WIREGUARD_ENDPOINT` in `.env`).

## 4. Off-LAN (you do this; the software does not)

These steps expose **only** WireGuard UDP, not HTTP/HTTPS:

1. Router: forward **UDP 51820** on the WAN to this host's LAN address.
2. Host firewall, if you use `ufw`:

   ```bash
   sudo ufw allow 51820/udp comment 'homeai wireguard'
   sudo ufw reload
   ```

   Docker-published ports bypass `ufw` the same way `:80`/`:443` do
   (`docs/NETWORKING.md`). The `ufw allow` is belt-and-braces and documents
   intent. Do **not** add a `DOCKER-USER` DROP for 51820 from non-LAN
   sources — that would also drop the forwarded WAN packets the VPN needs.
   WireGuard's cryptography is the access control on this port.

3. Set `WIREGUARD_ENDPOINT` to your public `host:51820` (or a DNS name)
   and recreate **platform** only (`--no-deps`) so **new** QR codes use it.
   Existing peers keep whatever Endpoint they already have.

4. Recreate the device QR after changing the endpoint (or edit Endpoint in
   the app).

Do **not** forward TCP 80 or 443. Public HTTPS is M15-05 and is opt-in.

## 5. DNS

Client configs set `DNS = 10.13.13.1`. The sidecar answers `homeai.local`
with `10.13.13.1` and proxies `:80`/`:443` to Caddy. Other names are not
resolved in v1 (split / recursive DNS is M15-03). With split-tunnel
`AllowedIPs = 10.13.13.0/24`, only traffic to the box uses the VPN.
