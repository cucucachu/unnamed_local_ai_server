-- Delivered routines (M21-08, docs/MEMORY.md §4.4): the admin's on/off switch
-- for each routine agent-server delivers to every user (e.g. Memory).
--
-- `delivered_routines` is the list agent-server registers at startup, like
-- `agent_tools`. The settings table has no foreign key to it, so replacing
-- the list never drops the admin's choice. A missing row means on.

CREATE TABLE delivered_routines (
    key TEXT PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]{0,63}$'),
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    position INTEGER NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE delivered_routine_admin_settings (
    key TEXT PRIMARY KEY CHECK (length(key) <= 64),
    enabled BOOLEAN NOT NULL,
    updated_by UUID REFERENCES users (id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
