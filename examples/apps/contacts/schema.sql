-- A person. Exported to other apps as `people` v1 (read-only), so keep these columns stable.
CREATE TABLE people (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL, -- how they're known, e.g. "Priya Shah"
  birthday TEXT, -- YYYY-MM-DD, or NULL if not known
  notes TEXT NOT NULL DEFAULT '' -- anything worth remembering about them
);

-- A way to reach a person: one row per phone number, email or address. Not exported.
CREATE TABLE contact_details (
  id INTEGER PRIMARY KEY,
  person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE, -- whose it is
  kind TEXT NOT NULL CHECK (kind IN ('phone', 'email', 'address')), -- what it is
  label TEXT NOT NULL DEFAULT '', -- e.g. "mobile", "work"; '' if none
  value TEXT NOT NULL -- the number, address or email itself
);

CREATE INDEX contact_details_person ON contact_details (person_id);
