-- OAuth sign-in for integrations (docs/INTEGRATIONS.md §5.1).
--
-- `oauth` is what the gateway last discovered from the server's metadata:
-- `{"summary": {issuer, sign_in_host, token_host, registration, revocable},
-- "endpoints": [url, ...]}`. The endpoints (token, registration,
-- revocation, metadata documents) join the egress rules. NULL: not
-- discovered, or not an OAuth server.
ALTER TABLE integrations ADD COLUMN oauth JSONB;

-- The OAuth client HomeAI registered with a server, one per return address
-- (the address a user's app reached HomeAI on). A client ID is public.
CREATE TABLE integration_oauth_clients (
    integration_id UUID NOT NULL REFERENCES integrations (id) ON DELETE CASCADE,
    redirect_uri TEXT NOT NULL CHECK (length(redirect_uri) <= 500),
    issuer TEXT NOT NULL CHECK (length(issuer) <= 2000),
    client_id TEXT NOT NULL CHECK (length(client_id) BETWEEN 1 AND 500),
    kind TEXT NOT NULL CHECK (kind IN ('dynamic', 'client_id_url')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (integration_id, redirect_uri)
);
