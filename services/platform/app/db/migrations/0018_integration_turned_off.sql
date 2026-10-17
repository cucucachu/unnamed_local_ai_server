-- An admin can turn an integration off without deleting it (#379,
-- docs/INTEGRATIONS.md §4). NULL: on. While set, the gateway refuses every
-- call with `turned_off` and the agent's list leaves it out; credentials,
-- tool settings and OAuth clients are all kept.

ALTER TABLE integrations ADD COLUMN turned_off_at TIMESTAMPTZ;
