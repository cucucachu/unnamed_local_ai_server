-- Merge person :drop_id into :keep_id (two entries for the same person):
-- the details move over, a missing birthday is filled in, the notes are
-- joined, and :drop_id is deleted. Does nothing if they're the same person
-- or either doesn't exist.
UPDATE contact_details SET person_id = :keep_id
WHERE person_id = :drop_id AND :keep_id <> :drop_id
  AND EXISTS (SELECT 1 FROM people WHERE id = :keep_id);

UPDATE people SET
  birthday = coalesce(birthday, (SELECT birthday FROM people WHERE id = :drop_id)),
  notes = trim(notes || char(10) || (SELECT notes FROM people WHERE id = :drop_id))
WHERE id = :keep_id AND :keep_id <> :drop_id
  AND EXISTS (SELECT 1 FROM people WHERE id = :drop_id);

DELETE FROM people
WHERE id = :drop_id AND :keep_id <> :drop_id
  AND EXISTS (SELECT 1 FROM people WHERE id = :keep_id);
