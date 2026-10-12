-- The header a token-auth server expects its token in (M18-07,
-- docs/INTEGRATIONS.md §5). NULL: `Authorization: Bearer <token>`; set: the
-- token as is in that header. `app.core.integrations` refuses names that
-- would confuse HTTP, the egress proxy or MCP itself.

ALTER TABLE integrations
    ADD COLUMN token_header TEXT CHECK (token_header ~ '^[A-Za-z0-9-]{1,64}$'),
    ADD CHECK (token_header IS NULL OR auth_method = 'token');
