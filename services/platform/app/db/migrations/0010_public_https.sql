-- M15-05: opt-in public HTTPS. The flag lives in platform_state (same
-- singleton KV as bootstrap_admin_id). Absence is treated as false; this
-- row makes the default inspectable. Not a secret.
INSERT INTO platform_state (key, value)
VALUES ('public_https', 'false'::jsonb)
ON CONFLICT (key) DO NOTHING;
