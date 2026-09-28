-- Runs against a scratch restore of the homeai dump (see README); clones
-- the thread with the most checkpoints 1,000 times. Holds no data itself.
\timing on
CREATE TEMP TABLE source AS
  SELECT thread_id AS src FROM checkpoints c
  WHERE EXISTS (SELECT FROM threads t WHERE t.id::text = c.thread_id)
  GROUP BY thread_id ORDER BY count(*) DESC LIMIT 1;
CREATE TEMP TABLE users AS SELECT gen_random_uuid() AS uid, g AS n FROM generate_series(1,20) g;
CREATE TEMP TABLE newthreads AS
  SELECT gen_random_uuid() AS id, u.uid FROM users u, generate_series(1,50);
INSERT INTO threads (id, title, owner_user_id) SELECT id, 'bench', uid FROM newthreads;
INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata)
  SELECT n.id::text, c.checkpoint_ns, c.checkpoint_id, c.parent_checkpoint_id, c.type, c.checkpoint, c.metadata
  FROM newthreads n, checkpoints c WHERE c.thread_id = (SELECT src FROM source);
INSERT INTO checkpoint_blobs (thread_id, checkpoint_ns, channel, version, type, blob)
  SELECT n.id::text, c.checkpoint_ns, c.channel, c.version, c.type, c.blob
  FROM newthreads n, checkpoint_blobs c WHERE c.thread_id = (SELECT src FROM source);
INSERT INTO checkpoint_writes (thread_id, checkpoint_ns, checkpoint_id, task_id, task_path, idx, channel, type, blob)
  SELECT n.id::text, c.checkpoint_ns, c.checkpoint_id, c.task_id, c.task_path, c.idx, c.channel, c.type, c.blob
  FROM newthreads n, checkpoint_writes c WHERE c.thread_id = (SELECT src FROM source);
INSERT INTO turn_stats SELECT n.id::text, t.final_message_id, t.status, t.duration_ms, t.started_at
  FROM newthreads n, turn_stats t WHERE t.thread_id = (SELECT src FROM source);
INSERT INTO user_settings (user_id, key, value) SELECT uid, k, 'true'::jsonb FROM users, unnest(array['hitl_enabled','thinking_enabled']) k;
ANALYZE;
SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY 1;
