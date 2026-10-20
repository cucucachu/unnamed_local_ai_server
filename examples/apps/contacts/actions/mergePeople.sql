-- Merge person :drop_id into :keep_id (two entries for the same person):
-- phones, emails, addresses, websites and dates move over, missing fields
-- (birthday, nickname, company, job title, relationship, photo) are filled
-- in, the notes are joined, and :drop_id is deleted. Does nothing if they're
-- the same person or either doesn't exist.
UPDATE contact_details SET person_id = :keep_id
WHERE person_id = :drop_id AND :keep_id <> :drop_id
  AND EXISTS (SELECT 1 FROM people WHERE id = :keep_id);

UPDATE addresses SET person_id = :keep_id
WHERE person_id = :drop_id AND :keep_id <> :drop_id
  AND EXISTS (SELECT 1 FROM people WHERE id = :keep_id);

UPDATE websites SET person_id = :keep_id
WHERE person_id = :drop_id AND :keep_id <> :drop_id
  AND EXISTS (SELECT 1 FROM people WHERE id = :keep_id);

UPDATE dates SET person_id = :keep_id
WHERE person_id = :drop_id AND :keep_id <> :drop_id
  AND EXISTS (SELECT 1 FROM people WHERE id = :keep_id);

UPDATE people SET
  birthday = coalesce(birthday, (SELECT birthday FROM people WHERE id = :drop_id)),
  nickname = CASE WHEN nickname = '' THEN (SELECT nickname FROM people WHERE id = :drop_id) ELSE nickname END,
  company = CASE WHEN company = '' THEN (SELECT company FROM people WHERE id = :drop_id) ELSE company END,
  job_title = CASE WHEN job_title = '' THEN (SELECT job_title FROM people WHERE id = :drop_id) ELSE job_title END,
  relationship = CASE WHEN relationship = '' THEN (SELECT relationship FROM people WHERE id = :drop_id) ELSE relationship END,
  photo = coalesce(photo, (SELECT photo FROM people WHERE id = :drop_id)),
  notes = trim(notes || char(10) || (SELECT notes FROM people WHERE id = :drop_id))
WHERE id = :keep_id AND :keep_id <> :drop_id
  AND EXISTS (SELECT 1 FROM people WHERE id = :drop_id);

DELETE FROM people
WHERE id = :drop_id AND :keep_id <> :drop_id
  AND EXISTS (SELECT 1 FROM people WHERE id = :keep_id);
