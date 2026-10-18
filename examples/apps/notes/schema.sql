-- One row per note. `pinned` is 1 to keep it at the top of the list.
CREATE TABLE notes (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL, -- the note's title, shown in the list
  body TEXT NOT NULL DEFAULT '', -- the note itself, plain text
  pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)), -- 1 keeps it at the top
  created_at TEXT NOT NULL DEFAULT (datetime('now')), -- when it was written (UTC)
  updated_at TEXT NOT NULL DEFAULT (datetime('now')) -- when it last changed (UTC)
);

CREATE INDEX notes_pinned ON notes (pinned);
