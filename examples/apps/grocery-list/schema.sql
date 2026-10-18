-- One row per thing to buy. `checked` is 1 once it's in the cart.
CREATE TABLE items (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL, -- what to buy
  quantity TEXT NOT NULL DEFAULT '', -- free text: "2", "1 kg", "a few"
  note TEXT NOT NULL DEFAULT '', -- brand, aisle, anything else
  checked INTEGER NOT NULL DEFAULT 0 CHECK (checked IN (0, 1)), -- 1 once it's in the cart
  created_at TEXT NOT NULL DEFAULT (datetime('now')) -- when it was added (UTC)
);

CREATE INDEX items_checked ON items (checked);
