-- A deck of cards on one subject, e.g. "Spanish verbs".
CREATE TABLE decks (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL, -- e.g. "Spanish verbs"
  description TEXT NOT NULL DEFAULT '' -- what it's for
);

-- A card: a prompt and its answer, and when to see it next. The schedule
-- columns are written by the `grade` action.
CREATE TABLE cards (
  id INTEGER PRIMARY KEY,
  deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE, -- the deck it's in
  front TEXT NOT NULL, -- the prompt, e.g. "to eat"
  back TEXT NOT NULL DEFAULT '', -- the answer, e.g. "comer"
  due TEXT NOT NULL, -- the local date it's next due, YYYY-MM-DD; new cards are due the day they're added
  interval_days INTEGER NOT NULL DEFAULT 0 CHECK (interval_days >= 0), -- days between the last review and `due`; 0 for new or forgotten
  ease REAL NOT NULL DEFAULT 2.5 CHECK (ease >= 1.3), -- how much the interval grows on "Good"
  reps INTEGER NOT NULL DEFAULT 0, -- reviews in a row not graded "Again"
  lapses INTEGER NOT NULL DEFAULT 0 -- times it was forgotten after being learned
);

-- One review of a card: what it was graded on which day.
CREATE TABLE reviews (
  id INTEGER PRIMARY KEY,
  card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE, -- the card
  day TEXT NOT NULL, -- the local date, YYYY-MM-DD
  grade INTEGER NOT NULL CHECK (grade BETWEEN 0 AND 3) -- 0 Again, 1 Hard, 2 Good, 3 Easy
);

CREATE INDEX cards_due ON cards (deck_id, due);
CREATE INDEX reviews_card ON reviews (card_id);
CREATE INDEX reviews_day ON reviews (day);
