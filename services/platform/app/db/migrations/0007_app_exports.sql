-- M14-04: cross-app exports are granted at install as `granted_reads`,
-- separate from `granted_permissions` (D13). Empty `homeai.reads` is
-- stored as [] and needs no prompt; a non-empty list must be sent back.

ALTER TABLE app_instances ADD COLUMN granted_reads JSONB NOT NULL DEFAULT '[]';
