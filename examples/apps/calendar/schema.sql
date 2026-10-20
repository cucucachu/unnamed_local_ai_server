-- An event on the space's calendar. A repeating event is one row (the series); its occurrences are worked out for the dates on screen.
CREATE TABLE events (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL, -- e.g. "Dentist"
  location TEXT NOT NULL DEFAULT '', -- where, free text
  notes TEXT NOT NULL DEFAULT '', -- anything else
  all_day INTEGER NOT NULL DEFAULT 0 CHECK (all_day IN (0, 1)), -- 1 for an all-day event, which has no times
  start_date TEXT NOT NULL, -- the local date it starts, YYYY-MM-DD; for a repeating event, the first occurrence
  start_time TEXT, -- the local time it starts, HH:MM; NULL when all-day
  end_date TEXT NOT NULL, -- the local date it ends, YYYY-MM-DD (an all-day event's last day)
  end_time TEXT, -- the local time it ends, HH:MM; NULL when all-day
  repeat TEXT NOT NULL DEFAULT 'none' CHECK (repeat IN ('none', 'daily', 'weekly', 'monthly', 'yearly')), -- how it repeats
  repeat_every INTEGER NOT NULL DEFAULT 1 CHECK (repeat_every >= 1), -- every N days/weeks/months/years
  repeat_days TEXT NOT NULL DEFAULT '', -- weekly: the weekdays, comma-separated, 0 = Sunday (e.g. "1,3"); '' for the start's weekday
  repeat_until TEXT, -- the last date an occurrence may start, YYYY-MM-DD; NULL to repeat forever
  CHECK ((all_day = 1 AND start_time IS NULL AND end_time IS NULL) OR (all_day = 0 AND start_time IS NOT NULL AND end_time IS NOT NULL)),
  CHECK (end_date || coalesce(end_time, '') >= start_date || coalesce(start_time, ''))
);
CREATE INDEX events_start ON events (start_date);

-- Who an event is for: space members, by user id. None means it's for nobody in particular.
CREATE TABLE event_people (
  id INTEGER PRIMARY KEY,
  event_id INTEGER NOT NULL, -- events.id
  user_id TEXT NOT NULL, -- a space member's user id (useMembers())
  UNIQUE (event_id, user_id)
);

-- One occurrence of a repeating event that was deleted, or edited into its own event.
CREATE TABLE event_skips (
  id INTEGER PRIMARY KEY,
  event_id INTEGER NOT NULL, -- events.id of the series
  date TEXT NOT NULL, -- the occurrence's start date, YYYY-MM-DD
  UNIQUE (event_id, date)
);
