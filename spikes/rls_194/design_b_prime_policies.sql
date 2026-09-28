\set ON_ERROR_STOP 1
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['checkpoint_blobs','checkpoint_writes','turn_stats'] LOOP
    EXECUTE format('DROP POLICY IF EXISTS owner_only ON %I', t);
    EXECUTE format($p$CREATE POLICY owner_only ON %I USING (owner_user_id = (SELECT nullif(current_setting('app.user_id', true), '')::uuid)) WITH CHECK (owner_user_id = (SELECT nullif(current_setting('app.user_id', true), '')::uuid))$p$, t);
  END LOOP;
END $$;
