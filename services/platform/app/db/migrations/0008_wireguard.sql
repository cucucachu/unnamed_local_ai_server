-- WireGuard peers (M15-01, docs/PLATFORM.md §8). The server private key lives
-- under /data/platform/wireguard/ (0600), not in this database. Peer private
-- keys are returned once in the client config and never stored.
--
-- Tunnel subnet 10.13.13.0/24 is reserved for this interface (server
-- 10.13.13.1; peers 10.13.13.2–254). It is chosen not to collide with a
-- typical home LAN (192.168.x) or Docker bridges (172.x).
--
-- sessions.device_id tags a login to a peer so revoking that device can drop
-- only those sessions, not every LAN browser of the same user.

CREATE TABLE wireguard_peers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 64),
    public_key TEXT NOT NULL UNIQUE,
    address INET NOT NULL UNIQUE
        CHECK (family(address) = 4 AND address << '10.13.13.0/24'::cidr
               AND host(address) <> '10.13.13.1'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX wireguard_peers_user_id_idx ON wireguard_peers (user_id);

ALTER TABLE sessions
    ADD COLUMN device_id UUID REFERENCES wireguard_peers (id) ON DELETE SET NULL;
CREATE INDEX sessions_device_id_idx ON sessions (device_id) WHERE device_id IS NOT NULL;
