# Connect Four

Four in a row between two members of the space. Anyone who can edit
starts a game against another member and picks who goes first (red).
Players take turns tapping a column to drop a disc; four in a row across,
down or diagonally wins, a full board is a draw, and either player can
resign. The other player's move appears as it's made: every screen re-reads
whenever the database changes. The home screen lists your games (your
turn first), games between other members (anyone can watch), and finished
ones; Rematch starts a new game with the colors swapped. Viewers can watch
but not play.

## Files

```text
app.json                  manifest
schema.sql                the database schema (plain SQLite DDL)
actions/dropDisc.sql      make a move (only the expected next move, into a column with room) and record the result
lib/connect4.ts           types, the board from the moves, landing row, four-in-a-row
components/BoardView.tsx  the board: tap a column to drop
app/_layout.tsx           Stack: games, game, new (modal)
app/index.tsx             the games, grouped
app/new.tsx               pick the opponent and who goes first
app/game/[id].tsx         the game: players, whose turn, board, resign, rematch
```

## Data

`games`: one row per game.

| column   | type    | meaning                                                  |
|----------|---------|----------------------------------------------------------|
| `id`     | INTEGER | primary key                                              |
| `red`    | TEXT    | user id of the red player, who moves first               |
| `yellow` | TEXT    | user id of the yellow player                             |
| `status` | TEXT    | `playing`, `won`, `draw` or `resigned`                   |
| `winner` | TEXT    | user id of the winner (also after a resignation); NULL otherwise |

`moves`: one row per disc.

| column    | type    | meaning                                             |
|-----------|---------|-----------------------------------------------------|
| `game_id` | INTEGER | `games.id`                                          |
| `turn`    | INTEGER | 0, 1, 2…: even turns are red's, odd are yellow's    |
| `col`     | INTEGER | 0–6 from the left                                   |
| `row`     | INTEGER | 0–5 from the bottom, where it landed                |

The board is all of a game's moves (`boardOf`); whose turn it is follows
from how many there are. User ids are members' (`useMembers()`); the game's
creator and each move's maker are the platform's `_created_by`.

## Turns

Turns are kept by the game screen only: it offers the board only to the
player whose turn it is. That is on purpose: anyone who can edit the space
(and the agent) can read and write this database. `dropDisc` refuses
anything but the next move (`:turn` must equal the number of moves so far)
into a column with room, in a game still on, so two taps or two players at
once can't both land. If a move's `_created_by` isn't the player whose turn
it was, the game screen says so under the board.

Win and draw are worked out in the app (`resultAfter`) before the move is
sent, and `dropDisc` stores them in the same action, only if the move
landed.

## Extending it safely

- **New column**: add it to `schema.sql` with `NOT NULL DEFAULT <constant>`
  (or nullable) and a trailing `-- comment`. Adding is applied on the next
  build; renaming or removing, or changing a CHECK, is destructive and
  needs the owner's approval.
- **Scores between people**: count `games` by `winner` per pair of players;
  no new table needed.
- **Other board sizes**: `COLS` / `ROWS` in `lib/connect4.ts`, and the
  `CHECK`s on `moves` (destructive to change: needs approval).
- **Undo**: delete a game's highest-`turn` move while `status = 'playing'`,
  with the other player's agreement (a `requests` table).
- Build screens from `@homeai/ui` and take colors from `useTheme()`.
- Imports are limited to `react`, `react-native`, `expo-router`,
  `expo-sqlite`, `@homeai/sdk`, `@homeai/ui`, `@expo/vector-icons` and
  relative files. No network, no DOM.
- Keep this file up to date when you change the schema or screens.
