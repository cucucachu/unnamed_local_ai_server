-- Agent tool permissions (#342, docs/PLATFORM.md §4 "Agent tool permissions").
-- Every tool the agent can use has two controls: availability (on/off) and
-- permission (ask/allow). The admin's settings apply to everyone (a ceiling
-- for permission); each user's apply to themselves. They are stored apart and
-- never rewritten to match each other: an admin tightening a tool leaves the
-- users' rows alone, and the admin's wins when a call is evaluated.
--
-- `agent_tools` is the catalog agent-server registers at startup. The settings
-- tables have no foreign key to it, so replacing the catalog never drops
-- anyone's settings. A missing row (or NULL column) means the default.

CREATE TABLE agent_tools (
    name TEXT PRIMARY KEY CHECK (name ~ '^[a-z][a-z0-9_]{0,63}$'),
    label TEXT NOT NULL,
    description TEXT NOT NULL,
    tool_group TEXT NOT NULL,
    read_only BOOLEAN NOT NULL,
    position INTEGER NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE agent_tool_admin_settings (
    tool TEXT PRIMARY KEY CHECK (length(tool) <= 200),
    enabled BOOLEAN,
    ceiling TEXT CHECK (ceiling IN ('ask', 'allow')),
    updated_by UUID REFERENCES users (id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE agent_tool_user_settings (
    user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    tool TEXT NOT NULL CHECK (length(tool) <= 200),
    enabled BOOLEAN,
    permission TEXT CHECK (permission IN ('ask', 'allow')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, tool)
);
