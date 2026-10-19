-- Something measured over time, e.g. weight in kg or water in glasses.
CREATE TABLE measures (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL, -- e.g. "Weight"
  unit TEXT NOT NULL DEFAULT '', -- e.g. "kg", "hours", "glasses"
  goal REAL, -- a daily target, or NULL for none
  decimals INTEGER NOT NULL DEFAULT 1 CHECK (decimals BETWEEN 0 AND 3), -- how many decimals to show
  summary TEXT NOT NULL DEFAULT 'last' CHECK (summary IN ('last', 'sum', 'average')) -- how a day's entries combine: weight is the last one, water the sum
);

-- One reading of a measure.
CREATE TABLE entries (
  id INTEGER PRIMARY KEY,
  measure_id INTEGER NOT NULL REFERENCES measures(id) ON DELETE CASCADE, -- what was measured
  day TEXT NOT NULL, -- the local date, YYYY-MM-DD
  value REAL NOT NULL, -- the reading, in the measure's unit
  note TEXT NOT NULL DEFAULT '' -- e.g. "after a run"
);

CREATE INDEX entries_measure_day ON entries (measure_id, day);
