-- A recipe: what it is, what goes in it (one ingredient per line) and how to make it.
CREATE TABLE recipes (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL, -- e.g. "Dal tadka"
  cuisine TEXT NOT NULL DEFAULT '', -- e.g. "Indian"; '' if not given
  servings INTEGER NOT NULL DEFAULT 4 CHECK (servings >= 1), -- how many it feeds
  minutes INTEGER, -- total time in minutes, or NULL if unknown
  ingredients TEXT NOT NULL DEFAULT '', -- one per line, e.g. "200 g red lentils"
  steps TEXT NOT NULL DEFAULT '' -- the method, one step per line or paragraph
);

-- The meal plan: what's for which meal on which day.
CREATE TABLE meals (
  id INTEGER PRIMARY KEY,
  day TEXT NOT NULL, -- the local date, YYYY-MM-DD
  slot TEXT NOT NULL DEFAULT 'Dinner', -- Breakfast, Lunch or Dinner
  recipe_id INTEGER REFERENCES recipes(id) ON DELETE SET NULL, -- the recipe; NULL for a note-only meal or a deleted recipe
  note TEXT NOT NULL DEFAULT '' -- e.g. "leftovers", "eating out"
);

CREATE INDEX meals_day ON meals (day, slot);
