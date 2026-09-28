-- One row per thing to buy. `checked` is 1 once it's in the cart.
CREATE TABLE items (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  quantity TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  checked INTEGER NOT NULL DEFAULT 0 CHECK (checked IN (0, 1)),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX items_checked ON items (checked);
