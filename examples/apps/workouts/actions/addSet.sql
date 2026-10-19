-- Add a set of :exercise_id (:reps at :weight) to the end of workout :workout_id.
INSERT INTO sets (workout_id, exercise_id, reps, weight, position)
SELECT w.id, e.id, max(0, CAST(:reps AS INTEGER)), max(0, coalesce(:weight, 0)),
       coalesce((SELECT max(position) + 1 FROM sets WHERE workout_id = w.id), 0)
FROM workouts w, exercises e
WHERE w.id = :workout_id AND e.id = :exercise_id;
