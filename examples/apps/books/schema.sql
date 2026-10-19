-- A book, film, show, game, album or podcast, and where you are with it.
CREATE TABLE items (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL, -- e.g. "Piranesi"
  creator TEXT NOT NULL DEFAULT '', -- the author, director, studio or artist
  kind TEXT NOT NULL DEFAULT 'book' CHECK (kind IN ('book', 'film', 'show', 'game', 'album', 'podcast')), -- what it is
  status TEXT NOT NULL DEFAULT 'want' CHECK (status IN ('want', 'started', 'finished', 'dropped')), -- want to, on it now, finished, or gave up
  rating INTEGER NULL CHECK (rating IS NULL OR rating BETWEEN 1 AND 5), -- 1 to 5 stars; null for not rated
  started_on TEXT NULL, -- the local date it was started, YYYY-MM-DD
  finished_on TEXT NULL, -- the local date it was finished (or dropped), YYYY-MM-DD
  notes TEXT NOT NULL DEFAULT '' -- thoughts, quotes, who recommended it
);

CREATE INDEX items_status ON items (status);
CREATE INDEX items_finished ON items (finished_on);
