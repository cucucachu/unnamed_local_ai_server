-- One member's game of one day's word. Who played is the platform's _created_by; one game per member per day.
CREATE TABLE games (
  id INTEGER PRIMARY KEY,
  day TEXT NOT NULL, -- the local date played, YYYY-MM-DD; that day's word is the same for everyone
  word TEXT NOT NULL, -- that day's word, lowercase
  guesses TEXT NOT NULL DEFAULT '', -- the guesses so far, lowercase, comma-separated, in order
  status TEXT NOT NULL DEFAULT 'playing' CHECK (status IN ('playing', 'won', 'lost')) -- 'won' or 'lost' once it's over
);
CREATE INDEX games_day ON games (day);
