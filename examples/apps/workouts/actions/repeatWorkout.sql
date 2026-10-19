-- Start a new workout on :day with the same sets (exercise, reps, weight)
-- as workout :workout_id. The new workout is the one with the highest id.
INSERT INTO workouts (day) SELECT :day WHERE EXISTS (SELECT 1 FROM workouts WHERE id = :workout_id);

INSERT INTO sets (workout_id, exercise_id, reps, weight, position)
SELECT (SELECT max(id) FROM workouts), exercise_id, reps, weight, position
FROM sets
WHERE workout_id = :workout_id AND :workout_id <> (SELECT max(id) FROM workouts)
ORDER BY position;
