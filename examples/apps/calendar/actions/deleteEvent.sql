-- Delete an event (every occurrence, if it repeats) with who it's for and its skipped dates.
DELETE FROM event_people WHERE event_id = :id;
DELETE FROM event_skips WHERE event_id = :id;
DELETE FROM events WHERE id = :id;
