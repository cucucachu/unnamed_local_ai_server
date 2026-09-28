# WireGuard sidecar (M15-01). Uses the host kernel module; this image only
# needs wg-quick, a DNS stub, and a TCP proxy to Caddy. Not privileged —
# compose grants NET_ADMIN only.
FROM alpine:3.21

RUN apk add --no-cache wireguard-tools iptables dnsmasq socat iproute2 bash wget

COPY entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh

# UDP 51820 is published by compose on the host; 80/443/53 are tunnel-only.
EXPOSE 51820/udp

ENTRYPOINT ["/entrypoint.sh"]
