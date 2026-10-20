-- A person. Exported to other apps as `people` v1 (read-only), so keep these columns stable.
CREATE TABLE people (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL, -- how they're known, e.g. "Priya Shah"
  birthday TEXT, -- YYYY-MM-DD, or NULL if not known
  notes TEXT NOT NULL DEFAULT '', -- anything worth remembering about them
  nickname TEXT NOT NULL DEFAULT '', -- what friends call them, e.g. "Pree"; '' if none
  company TEXT NOT NULL DEFAULT '', -- where they work; '' if none
  job_title TEXT NOT NULL DEFAULT '', -- e.g. "Dentist"; '' if none
  relationship TEXT NOT NULL DEFAULT '', -- who they are to you, e.g. "sister", "neighbour"; '' if none
  photo TEXT -- a photo id from pickImage, or NULL
);

-- A phone number or email: one row each. Not exported. Rows of kind 'address' are
-- one-line addresses from before `addresses`; the edit form turns them into those.
CREATE TABLE contact_details (
  id INTEGER PRIMARY KEY,
  person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE, -- whose it is
  kind TEXT NOT NULL CHECK (kind IN ('phone', 'email', 'address')), -- what it is
  label TEXT NOT NULL DEFAULT '', -- e.g. "mobile", "work"; '' if none
  value TEXT NOT NULL -- the number or email itself
);

CREATE INDEX contact_details_person ON contact_details (person_id);

-- A postal address, in parts. Not exported.
CREATE TABLE addresses (
  id INTEGER PRIMARY KEY,
  person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE, -- whose it is
  label TEXT NOT NULL DEFAULT '', -- e.g. "home", "work"; '' if none
  street1 TEXT NOT NULL DEFAULT '', -- first street line
  street2 TEXT NOT NULL DEFAULT '', -- second street line (flat, unit); '' if none
  city TEXT NOT NULL DEFAULT '', -- town or city
  region TEXT NOT NULL DEFAULT '', -- state, province or county
  postcode TEXT NOT NULL DEFAULT '', -- postcode or ZIP
  country TEXT NOT NULL DEFAULT '' -- country; '' for your own
);

CREATE INDEX addresses_person ON addresses (person_id);

-- A website (personal site, profile page). Not exported.
CREATE TABLE websites (
  id INTEGER PRIMARY KEY,
  person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE, -- whose it is
  label TEXT NOT NULL DEFAULT '', -- e.g. "personal", "work"; '' if none
  url TEXT NOT NULL -- as typed, e.g. "priya.example.com"
);

CREATE INDEX websites_person ON websites (person_id);

-- A date worth remembering besides the birthday, e.g. an anniversary. Not exported.
CREATE TABLE dates (
  id INTEGER PRIMARY KEY,
  person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE, -- whose it is
  label TEXT NOT NULL DEFAULT '', -- e.g. "anniversary"; '' if none
  date TEXT NOT NULL -- YYYY-MM-DD; it comes round every year
);

CREATE INDEX dates_person ON dates (person_id);
