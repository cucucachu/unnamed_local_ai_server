# Kanban

Boards of cards in columns. **Boards** lists them with how many cards are
open (not in the last column); a new board starts with To do, Doing (a
limit of 3) and Done. A board shows its columns side by side, scrolling
sideways; each has its cards and an "Add a card" field. Swipe a card left
(or long-press it) to move it a column back or on, or delete it; tap it to
edit its title, note, due date and who's doing it, move it up or down, or
pick another column. The options button in the header edits the columns: rename,
reorder, a card limit (the column title turns red when it's over), add
and delete; and renames or deletes the board.

## Files

```text
app.json                    manifest; `index` makes cards searchable
schema.sql                  the database schema (plain SQLite DDL)
actions/newBoard.sql        a board with To do, Doing, Done
actions/addCard.sql         a card at the bottom of a column
actions/moveCard.sql        move a card to a column and position
actions/deleteCard.sql      delete a card, closing the gap
actions/moveColumn.sql      move a column left or right
actions/deleteColumn.sql    delete a column and its cards, closing the gap
lib/kanban.ts               types, dates, column titles
app/_layout.tsx             Stack: boards, a board, its columns, a card
app/index.tsx               the boards, and a form for a new one
app/board/[id]/index.tsx    one board: columns, cards, swipe actions
app/board/[id]/columns.tsx  edit the columns; rename or delete the board
app/card/[id].tsx           one card
```

## Data

`boards`: `id`, `name`.

`columns`: `id`, `board_id` (deleted with the board), `name`, `position`
(0 is the left), `wip_limit` (null for none).

`cards`:

| column      | type    | meaning                                                   |
|-------------|---------|-----------------------------------------------------------|
| `id`        | INTEGER | primary key                                               |
| `column_id` | INTEGER | `columns.id`; deleted with the column                     |
| `title`     | TEXT    | what it is                                                |
| `note`      | TEXT    | details                                                   |
| `position`  | INTEGER | 0 is the top of its column                                |
| `due`       | TEXT    | local `YYYY-MM-DD`, or null                               |
| `assignee`  | TEXT    | a member's user id (`useMembers()`), or null              |

Positions are always 0, 1, 2, … within a column (cards) or a board
(columns). The actions keep them that way, so moving, adding and deleting
should go through them rather than plain `UPDATE`s. The board's last
column counts as done.

## Actions

Call them with `runAction(name, params)`; params are `:named`.

- `newBoard`: `{ name }`. The board and its three columns. The new board
  is `SELECT max(id) FROM boards`.
- `addCard`: `{ column_id, title }`. At the bottom of the column.
- `moveCard`: `{ card_id, column_id, position }`. 0 is the top; anything
  past the end (the swipe actions pass 1000000) is the bottom. A column on
  another board is refused (nothing changes).
- `deleteCard`: `{ card_id }`.
- `moveColumn`: `{ column_id, position }`. 0 is the left.
- `deleteColumn`: `{ column_id }`. Its cards go with it.

Adding a column, renaming and editing a card's fields are one
`db.runAsync` each.

## Exports and reads

None. Exporting `cards` (with `addCard`) would let another app put work
on a board, e.g. a home-maintenance app filing "Replace the filter".

## How the code talks to the database

- `useQuery(sql, params)` for anything on screen; it re-runs when anyone
  changes the database, so everyone on the board sees moves live. Don't
  copy results into state.
- `useDatabase()` gives `db.runAsync` / `db.getAllAsync` etc.: one
  statement per call, `?` placeholders. Never build SQL by string
  concatenation.
- `useMembers()` lists the space's people (`id`, `name`) for "Who's doing
  it".
- `useSpace()?.role` is `'viewer'` for people who can only look; the
  screens hide the add, move, edit and delete controls from them.
- `ListRow`'s `actions` are the swipe actions: revealed by swiping left or
  a long press. The last one is red unless it sets `color`.

## Extending it safely

- **New column** (e.g. `color` or `label` on `cards`): add it to
  `schema.sql` with `NOT NULL DEFAULT <constant>` (or nullable) and a
  trailing `-- comment`. Adding is applied on the next build; renaming or
  removing is destructive and needs the owner's approval.
- **New table** (e.g. a checklist per card): a comment line above the
  `CREATE TABLE`; link rows with `REFERENCES cards(id) ON DELETE CASCADE`.
- **New screen**: a file under `app/` with a `Stack.Screen` in
  `app/_layout.tsx` (`presentation: 'modal'` for a sheet). Helpers live
  outside `app/`.
- Imports are limited to `react`, `react-native`, `expo-router`,
  `expo-sqlite`, `@homeai/sdk`, `@homeai/ui`, `@expo/vector-icons` and
  relative files. No network, no DOM.
- Keep this file up to date when you change the schema, actions or
  screens.
