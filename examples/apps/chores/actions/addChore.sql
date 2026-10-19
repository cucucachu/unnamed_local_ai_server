-- Add a chore named :name, every :every_days days (at least 1), first due on
-- :next_due (YYYY-MM-DD). The result's lastInsertRowId is the new chore's id:
-- add its chore_assignees rows after.
INSERT INTO chores (name, every_days, next_due)
VALUES (trim(:name), max(1, CAST(:every_days AS INTEGER)), :next_due);
