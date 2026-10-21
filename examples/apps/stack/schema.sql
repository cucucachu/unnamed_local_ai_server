-- One finished game. Who played is the platform's _created_by; when is _created_at.
CREATE TABLE games (
  id INTEGER PRIMARY KEY,
  score INTEGER NOT NULL CHECK (score >= 0), -- blocks stacked before the miss
  perfects INTEGER NOT NULL DEFAULT 0 CHECK (perfects >= 0 AND perfects <= score) -- how many of them were perfect drops
);
