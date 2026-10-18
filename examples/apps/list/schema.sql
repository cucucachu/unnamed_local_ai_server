-- One row per checklist item. `done` is 1 once it's ticked off.
CREATE TABLE items (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL, -- the item's text
  done INTEGER NOT NULL DEFAULT 0 CHECK (done IN (0, 1)), -- 1 once it's ticked off
  created_at TEXT NOT NULL DEFAULT (datetime('now')) -- when it was added (UTC)
);

CREATE INDEX items_done ON items (done);
