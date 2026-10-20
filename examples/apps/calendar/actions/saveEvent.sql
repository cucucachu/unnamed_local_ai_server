-- Add (:id NULL) or update an event and replace who it's for (:people, a
-- JSON list of user ids), in one go. Returns the event's id. With
-- :skip_event_id and :skip_date it also skips that occurrence of a repeating
-- event: "edit this event only" saves the edited copy as a new event. A new
-- event is the highest id until this action commits: writes to one database
-- run one at a time. Saving an event deleted meanwhile changes nothing.
INSERT INTO events (title, location, notes, all_day, start_date, start_time, end_date, end_time, repeat, repeat_every, repeat_days, repeat_until)
SELECT :title, :location, :notes, :all_day, :start_date, :start_time, :end_date, :end_time, :repeat, :repeat_every, :repeat_days, :repeat_until
WHERE :id IS NULL;

UPDATE events SET
  title = :title, location = :location, notes = :notes, all_day = :all_day,
  start_date = :start_date, start_time = :start_time, end_date = :end_date, end_time = :end_time,
  repeat = :repeat, repeat_every = :repeat_every, repeat_days = :repeat_days, repeat_until = :repeat_until
WHERE id = :id;

DELETE FROM event_people WHERE event_id = coalesce(:id, (SELECT max(id) FROM events));
INSERT OR IGNORE INTO event_people (event_id, user_id)
SELECT coalesce(:id, (SELECT max(id) FROM events)), j.value
FROM json_each(:people) AS j
WHERE j.type = 'text' AND EXISTS (SELECT 1 FROM events WHERE id = coalesce(:id, (SELECT max(id) FROM events)));

INSERT OR IGNORE INTO event_skips (event_id, date)
SELECT :skip_event_id, :skip_date WHERE :skip_event_id IS NOT NULL AND EXISTS (SELECT 1 FROM events WHERE id = :skip_event_id);

SELECT coalesce(:id, (SELECT max(id) FROM events)) AS id;
