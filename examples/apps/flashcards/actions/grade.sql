-- Grade card :card_id on :today with :grade (0 Again, 1 Hard, 2 Good,
-- 3 Easy) and schedule its next review, SM-2 style:
--   Again: forgotten. Due again today, ease down 0.2, reps back to 0.
--   Hard:  the interval grows by 1.2 (at least 1 day), ease down 0.15.
--   Good:  1 day, then 3, then the interval times the ease.
--   Easy:  4 days, then the interval times the ease times 1.3, ease up 0.15.
-- The ease never goes below 1.3. lib/flashcards.ts `nextInterval` shows
-- the same numbers on the buttons; keep the two in step.
INSERT INTO reviews (card_id, day, grade)
SELECT id, :today, :grade FROM cards WHERE id = :card_id AND :grade IN (0, 1, 2, 3);

UPDATE cards SET
  interval_days = CASE :grade
    WHEN 0 THEN 0
    WHEN 1 THEN max(1, CAST(round(interval_days * 1.2) AS INTEGER))
    WHEN 2 THEN CASE reps WHEN 0 THEN 1 WHEN 1 THEN 3 ELSE max(interval_days + 1, CAST(round(interval_days * ease) AS INTEGER)) END
    ELSE CASE reps WHEN 0 THEN 4 ELSE max(interval_days + 1, CAST(round(interval_days * ease * 1.3) AS INTEGER)) END
  END,
  ease = CASE :grade
    WHEN 0 THEN max(1.3, ease - 0.2)
    WHEN 1 THEN max(1.3, ease - 0.15)
    WHEN 2 THEN ease
    ELSE ease + 0.15
  END,
  reps = CASE :grade WHEN 0 THEN 0 ELSE reps + 1 END,
  lapses = CASE WHEN :grade = 0 AND reps > 0 THEN lapses + 1 ELSE lapses END
WHERE id = :card_id AND :grade IN (0, 1, 2, 3);

UPDATE cards SET due = date(:today, '+' || interval_days || ' days')
WHERE id = :card_id AND :grade IN (0, 1, 2, 3);
