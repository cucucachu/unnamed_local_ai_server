-- Stop showing the birthday of person :person_id from Contacts copy :instance.
INSERT INTO hidden_birthdays (instance, person_id)
SELECT :instance, :person_id
WHERE NOT EXISTS (SELECT 1 FROM hidden_birthdays WHERE instance = :instance AND person_id = :person_id);
