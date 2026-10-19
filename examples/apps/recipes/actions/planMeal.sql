-- Plan recipe :recipe_id (or null, for a note-only meal) for :slot on :day
-- (YYYY-MM-DD), with :note. It replaces whatever was planned for that meal.
-- A recipe that doesn't exist is planned as a note-only meal.
DELETE FROM meals WHERE day = :day AND slot = :slot;

INSERT INTO meals (day, slot, recipe_id, note)
VALUES (:day, :slot, (SELECT id FROM recipes WHERE id = :recipe_id), trim(:note));
