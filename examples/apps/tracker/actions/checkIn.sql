-- Mark :habit_id as done for :day (YYYY-MM-DD). A second check-in the same
-- day is a no-op because of UNIQUE (habit_id, day).
INSERT OR IGNORE INTO checkins (habit_id, day)
VALUES (:habit_id, :day);
