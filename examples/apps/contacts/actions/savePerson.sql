-- Add (:id NULL) or update a person, and replace their phones and emails
-- (:details, a JSON list of {kind, label, value}), addresses (:addresses,
-- {label, street1, street2, city, region, postcode, country}), websites
-- (:websites, {label, url}) and other dates (:dates, {label, date}), all in
-- one go. Returns the person's id. A new person is the highest id until this
-- action commits: writes to one database run one at a time.
INSERT INTO people (name) SELECT :name WHERE :id IS NULL;

UPDATE people SET
  name = :name, nickname = :nickname, company = :company, job_title = :job_title,
  relationship = :relationship, birthday = :birthday, notes = :notes, photo = :photo
WHERE id = coalesce(:id, (SELECT max(id) FROM people));

DELETE FROM contact_details WHERE person_id = coalesce(:id, (SELECT max(id) FROM people));
INSERT INTO contact_details (person_id, kind, label, value)
SELECT coalesce(:id, (SELECT max(id) FROM people)), j.value ->> '$.kind', j.value ->> '$.label', j.value ->> '$.value'
FROM json_each(:details) AS j
WHERE j.value ->> '$.kind' IN ('phone', 'email') ORDER BY j.key;

DELETE FROM addresses WHERE person_id = coalesce(:id, (SELECT max(id) FROM people));
INSERT INTO addresses (person_id, label, street1, street2, city, region, postcode, country)
SELECT coalesce(:id, (SELECT max(id) FROM people)), j.value ->> '$.label', j.value ->> '$.street1', j.value ->> '$.street2',
  j.value ->> '$.city', j.value ->> '$.region', j.value ->> '$.postcode', j.value ->> '$.country'
FROM json_each(:addresses) AS j ORDER BY j.key;

DELETE FROM websites WHERE person_id = coalesce(:id, (SELECT max(id) FROM people));
INSERT INTO websites (person_id, label, url)
SELECT coalesce(:id, (SELECT max(id) FROM people)), j.value ->> '$.label', j.value ->> '$.url'
FROM json_each(:websites) AS j ORDER BY j.key;

DELETE FROM dates WHERE person_id = coalesce(:id, (SELECT max(id) FROM people));
INSERT INTO dates (person_id, label, date)
SELECT coalesce(:id, (SELECT max(id) FROM people)), j.value ->> '$.label', j.value ->> '$.date'
FROM json_each(:dates) AS j ORDER BY j.key;

SELECT coalesce(:id, (SELECT max(id) FROM people)) AS id;
