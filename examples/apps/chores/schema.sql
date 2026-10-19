-- A chore that repeats every few days; the members in chore_assignees take turns at it.
CREATE TABLE chores (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL, -- what to do, e.g. "Take out the bins"
  every_days INTEGER NOT NULL DEFAULT 7 CHECK (every_days >= 1), -- how often, in days
  next_due TEXT NOT NULL, -- the local date it's next due, YYYY-MM-DD
  turn INTEGER NOT NULL DEFAULT 0, -- whose turn: an index into the chore's rotation, wrapping around
  note TEXT NOT NULL DEFAULT '' -- how to do it, or anything else
);

-- Who takes turns at a chore, in rotation order. Empty means anyone.
CREATE TABLE chore_assignees (
  id INTEGER PRIMARY KEY,
  chore_id INTEGER NOT NULL REFERENCES chores(id) ON DELETE CASCADE, -- the chore
  user_id TEXT NOT NULL, -- a space member's user id (useMembers)
  position INTEGER NOT NULL DEFAULT 0, -- their place in the rotation, from 0
  UNIQUE (chore_id, user_id)
);

-- One row each time a chore was done; who did it is _created_by.
CREATE TABLE completions (
  id INTEGER PRIMARY KEY,
  chore_id INTEGER NOT NULL REFERENCES chores(id) ON DELETE CASCADE, -- the chore
  done_on TEXT NOT NULL -- the doer's local date, YYYY-MM-DD
);

CREATE INDEX chores_next_due ON chores (next_due);
CREATE INDEX completions_chore ON completions (chore_id, done_on);
