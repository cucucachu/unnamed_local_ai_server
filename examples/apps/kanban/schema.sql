-- A board, e.g. "House projects".
CREATE TABLE boards (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL -- e.g. "House projects"
);

-- A column on a board, e.g. "To do", "Doing", "Done".
CREATE TABLE columns (
  id INTEGER PRIMARY KEY,
  board_id INTEGER NOT NULL REFERENCES boards(id) ON DELETE CASCADE, -- the board it's on
  name TEXT NOT NULL, -- e.g. "Doing"
  position INTEGER NOT NULL DEFAULT 0, -- left to right, 0 first; kept 0, 1, 2, … by the actions
  wip_limit INTEGER NULL CHECK (wip_limit IS NULL OR wip_limit > 0) -- the most cards it should hold; null for no limit
);

-- A card in a column.
CREATE TABLE cards (
  id INTEGER PRIMARY KEY,
  column_id INTEGER NOT NULL REFERENCES columns(id) ON DELETE CASCADE, -- the column it's in
  title TEXT NOT NULL, -- what it is, e.g. "Fix the gate"
  note TEXT NOT NULL DEFAULT '', -- details
  position INTEGER NOT NULL DEFAULT 0, -- top to bottom in its column, 0 first; kept 0, 1, 2, … by the actions
  due TEXT NULL, -- the local date it's due, YYYY-MM-DD; null for none
  assignee TEXT NULL -- the member doing it (their user id, as in useMembers()); null for nobody
);

CREATE INDEX columns_board ON columns (board_id, position);
CREATE INDEX cards_column ON cards (column_id, position);
