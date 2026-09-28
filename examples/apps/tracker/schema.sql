-- One row per habit. Check-ins live in `checkins`, one per habit per day.
CREATE TABLE habits (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE checkins (
  id INTEGER PRIMARY KEY,
  habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
  day TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (habit_id, day)
);

CREATE INDEX checkins_habit_day ON checkins (habit_id, day);
