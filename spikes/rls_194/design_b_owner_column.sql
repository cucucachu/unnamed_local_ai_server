\set ON_ERROR_STOP 1
DROP INDEX IF EXISTS threads_id_text_owner_idx;
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['checkpoints','checkpoint_blobs','checkpoint_writes','turn_stats'] LOOP
    EXECUTE format('DROP POLICY IF EXISTS owner_only ON %I', t);
    EXECUTE format($p$ALTER TABLE %I ADD COLUMN IF NOT EXISTS owner_user_id uuid DEFAULT nullif(current_setting('app.user_id', true), '')::uuid$p$, t);
    EXECUTE format('UPDATE %I x SET owner_user_id = th.owner_user_id FROM threads th WHERE th.id::text = x.thread_id', t);
    EXECUTE format($p$CREATE POLICY owner_only ON %I USING (owner_user_id = (SELECT nullif(current_setting('app.user_id', true), '')::uuid)) WITH CHECK (owner_user_id = (SELECT nullif(current_setting('app.user_id', true), '')::uuid) AND thread_id IN (SELECT id::text FROM threads WHERE owner_user_id = (SELECT nullif(current_setting('app.user_id', true), '')::uuid)))$p$, t);
  END LOOP;
END $$;
VACUUM ANALYZE;
