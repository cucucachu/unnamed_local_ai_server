-- A game between two members. Red moves first. Who started it is the platform's _created_by.
CREATE TABLE games (
  id INTEGER PRIMARY KEY,
  red TEXT NOT NULL, -- user id of the red player, who moves first
  yellow TEXT NOT NULL, -- user id of the yellow player
  status TEXT NOT NULL DEFAULT 'playing' CHECK (status IN ('playing', 'won', 'draw', 'resigned')), -- 'won' by four in a row, 'resigned' when a player gave up
  winner TEXT, -- user id of the winner; NULL while playing or after a draw
  CHECK (red <> yellow)
);

-- One disc dropped. The board is all of a game's moves; the player is whoever's turn it was (and _created_by is who actually made it).
CREATE TABLE moves (
  id INTEGER PRIMARY KEY,
  game_id INTEGER NOT NULL, -- games.id
  turn INTEGER NOT NULL, -- 0 for the first move (red), 1 for yellow's first, and so on
  col INTEGER NOT NULL CHECK (col BETWEEN 0 AND 6), -- the column, 0 at the left
  row INTEGER NOT NULL CHECK (row BETWEEN 0 AND 5), -- where it landed, 0 at the bottom
  UNIQUE (game_id, turn),
  UNIQUE (game_id, col, row)
);
