-- A date to count down to: a trip, a deadline, or a yearly anniversary.
CREATE TABLE countdowns (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL, -- e.g. "Lisbon trip"
  day TEXT NOT NULL, -- the date, YYYY-MM-DD; for a yearly one, the first time it happened
  yearly INTEGER NOT NULL DEFAULT 0 CHECK (yearly IN (0, 1)), -- 1 to count to the next anniversary every year
  note TEXT NOT NULL DEFAULT '' -- anything worth remembering
);

-- A contact's birthday (from the Contacts app's `people` export) not to show here.
CREATE TABLE hidden_birthdays (
  id INTEGER PRIMARY KEY,
  instance TEXT NOT NULL, -- the Contacts copy's instance id (`_instance` of contacts_people)
  person_id INTEGER NOT NULL, -- `id` in that copy's people
  UNIQUE (instance, person_id)
);
