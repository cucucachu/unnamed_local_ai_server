-- One row per habit. Check-ins live in `checkins`, one per habit per day.
CREATE TABLE habits (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL, -- the habit, e.g. "Stretch"
  created_at TEXT NOT NULL DEFAULT (datetime('now')) -- when it was added (UTC)
);

-- One row per day a habit was done.
CREATE TABLE checkins (
  id INTEGER PRIMARY KEY,
  habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE, -- which habit
  day TEXT NOT NULL, -- the day it was done, YYYY-MM-DD (UTC)
  created_at TEXT NOT NULL DEFAULT (datetime('now')), -- when it was checked (UTC)
  UNIQUE (habit_id, day)
);

CREATE INDEX checkins_habit_day ON checkins (habit_id, day);
