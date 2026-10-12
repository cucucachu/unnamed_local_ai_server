-- Integrations registry (M18-03, docs/INTEGRATIONS.md, docs/PLATFORM.md §4
-- "Integrations"). An integration is one MCP server an admin installed for
-- the household. What it offers is what the integrations gateway last
-- discovered. Its tools' availability and permission settings live in
-- 0014's tables under the key `mcp:<integration id>:<tool name>`.
--
-- Credentials are ciphertext only: the gateway encrypts and decrypts them,
-- the platform never can. One row per user who connected, plus at most one
-- shared row (user_id NULL) for an integration whose credential is shared.

CREATE TABLE integrations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug TEXT NOT NULL UNIQUE CHECK (slug ~ '^[a-z][a-z0-9-]{0,39}$'),
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
    url TEXT NOT NULL CHECK (length(url) <= 2000),
    -- The one line the model sees; starts from the server's own.
    description TEXT NOT NULL DEFAULT '' CHECK (length(description) <= 500),
    credential_model TEXT NOT NULL CHECK (credential_model IN ('per_user', 'shared', 'none')),
    auth_method TEXT NOT NULL CHECK (auth_method IN ('none', 'token', 'oauth')),
    -- SHA-256 of a self-signed certificate the admin confirmed.
    cert_fingerprint TEXT CHECK (cert_fingerprint ~ '^[0-9a-f]{64}$'),
    -- The admin's explicit exception for a private address (this host and port only).
    private_address BOOLEAN NOT NULL DEFAULT false,
    server_info JSONB,
    protocol_version TEXT,
    instructions TEXT NOT NULL DEFAULT '',
    discovered_at TIMESTAMPTZ,
    installed_by UUID REFERENCES users (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((auth_method = 'none') = (credential_model = 'none'))
);

CREATE TABLE integration_tools (
    integration_id UUID NOT NULL REFERENCES integrations (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    title TEXT,
    description TEXT NOT NULL DEFAULT '',
    input_schema JSONB NOT NULL,
    -- The server's hints (readOnlyHint, destructiveHint, ...): shown, never trusted.
    annotations JSONB NOT NULL DEFAULT '{}',
    position INTEGER NOT NULL,
    PRIMARY KEY (integration_id, name)
);

CREATE TABLE integration_resources (
    integration_id UUID NOT NULL REFERENCES integrations (id) ON DELETE CASCADE,
    uri TEXT NOT NULL,
    name TEXT NOT NULL,
    title TEXT,
    description TEXT NOT NULL DEFAULT '',
    mime_type TEXT,
    position INTEGER NOT NULL,
    PRIMARY KEY (integration_id, uri)
);

CREATE TABLE integration_prompts (
    integration_id UUID NOT NULL REFERENCES integrations (id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    title TEXT,
    description TEXT NOT NULL DEFAULT '',
    arguments JSONB NOT NULL DEFAULT '[]',
    position INTEGER NOT NULL,
    PRIMARY KEY (integration_id, name)
);

CREATE TABLE integration_credentials (
    integration_id UUID NOT NULL REFERENCES integrations (id) ON DELETE CASCADE,
    -- NULL: the household's shared credential.
    user_id UUID REFERENCES users (id) ON DELETE CASCADE,
    ciphertext BYTEA NOT NULL,
    status TEXT NOT NULL DEFAULT 'connected' CHECK (status IN ('connected', 'needs_reconnecting')),
    set_by UUID REFERENCES users (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE NULLS NOT DISTINCT (integration_id, user_id)
);
