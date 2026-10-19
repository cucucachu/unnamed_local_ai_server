-- Set item :id's status to :status on :today. Starting stamps
-- `started_on` (if it isn't set), finishing or dropping stamps
-- `finished_on`, and going back to "want" clears both.
UPDATE items SET
  status = :status,
  started_on = CASE
    WHEN :status = 'want' THEN NULL
    WHEN :status IN ('started', 'finished') THEN coalesce(started_on, :today)
    ELSE started_on
  END,
  finished_on = CASE WHEN :status IN ('finished', 'dropped') THEN coalesce(finished_on, :today) ELSE NULL END
WHERE id = :id AND :status IN ('want', 'started', 'finished', 'dropped');
