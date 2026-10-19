-- Start card :card_id over as new, due :today. Its past reviews stay.
UPDATE cards SET due = :today, interval_days = 0, ease = 2.5, reps = 0, lapses = 0
WHERE id = :card_id;
