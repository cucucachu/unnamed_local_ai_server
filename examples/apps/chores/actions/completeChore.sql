-- :chore_id was done on :today (the doer's local date, YYYY-MM-DD) by whoever
-- runs this (the completion's _created_by). It's next due every_days after
-- :today, and the turn passes to the next person in the rotation.
INSERT INTO completions (chore_id, done_on)
SELECT id, :today FROM chores WHERE id = :chore_id;

UPDATE chores
SET next_due = date(:today, '+' || every_days || ' days'),
    turn = (turn + 1) % max(1, (SELECT count(*) FROM chore_assignees WHERE chore_id = :chore_id))
WHERE id = :chore_id;
