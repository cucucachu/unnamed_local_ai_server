-- One row per thing kept at home: how much is left, where, and when it expires.
CREATE TABLE items (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL, -- what it is, e.g. "Basmati rice"
  quantity REAL NOT NULL DEFAULT 1 CHECK (quantity >= 0), -- how much is left, in `unit`
  unit TEXT NOT NULL DEFAULT '', -- e.g. "kg", "cans", "L"; '' for a plain count
  location TEXT NOT NULL DEFAULT 'Pantry', -- where it's kept: Pantry, Fridge, Freezer, or any other place
  expires_on TEXT, -- best-before as YYYY-MM-DD, or NULL if it keeps
  note TEXT NOT NULL DEFAULT '' -- brand, which shelf, anything else
);

CREATE INDEX items_location ON items (location, name);
CREATE INDEX items_expires ON items (expires_on);
