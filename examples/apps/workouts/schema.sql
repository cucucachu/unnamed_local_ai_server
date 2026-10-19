-- An exercise, e.g. "Squat" or "Pull-up".
CREATE TABLE exercises (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL, -- e.g. "Bench press"
  unit TEXT NOT NULL DEFAULT 'kg', -- the weight unit: 'kg' or 'lb'; '' for bodyweight only
  note TEXT NOT NULL DEFAULT '' -- form cues, machine settings
);

-- A workout: one session on one day.
CREATE TABLE workouts (
  id INTEGER PRIMARY KEY,
  day TEXT NOT NULL, -- the local date, YYYY-MM-DD
  note TEXT NOT NULL DEFAULT '' -- how it went
);

-- One set: an exercise done for some reps at some weight, in a workout.
CREATE TABLE sets (
  id INTEGER PRIMARY KEY,
  workout_id INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE, -- the workout it was part of
  exercise_id INTEGER NOT NULL REFERENCES exercises(id) ON DELETE CASCADE, -- what was done
  reps INTEGER NOT NULL CHECK (reps >= 0), -- how many times
  weight REAL NOT NULL DEFAULT 0 CHECK (weight >= 0), -- the weight, in the exercise's unit; 0 for bodyweight
  position INTEGER NOT NULL DEFAULT 0 -- the order within the workout
);

CREATE INDEX workouts_day ON workouts (day);
CREATE INDEX sets_workout ON sets (workout_id, position);
CREATE INDEX sets_exercise ON sets (exercise_id);
