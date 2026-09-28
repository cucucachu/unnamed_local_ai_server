# List

A shared checklist. People add items, tick them off, and clear the done
ones when they're finished. Tapping an item opens a screen to rename or
delete it.

## Files

```text
app.json              manifest (name, slug, version, homeai block)
schema.sql            the database schema (plain SQLite DDL)
actions/addItem.sql   add an item, or untick it if it's already listed
actions/clearDone.sql delete every done item
app/_layout.tsx       the Stack navigator and screen titles
app/index.tsx         the list: add, tick/untick, clear done
app/item/[id].tsx     one item: rename, tick, delete
```

## Data

`items` — one row per thing on the list.

| column       | type    | meaning                                   |
|--------------|---------|-------------------------------------------|
| `id`         | INTEGER | primary key                               |
| `name`       | TEXT    | the item; never empty                     |
| `done`       | INTEGER | `0` = still open, `1` = ticked off        |
| `created_at` | TEXT    | UTC, `YYYY-MM-DD HH:MM:SS`, set by SQLite |

The list shows open items first, oldest first:
`SELECT … FROM items ORDER BY done, id`.

## Actions

Actions are files in `actions/`; each runs all its statements in one
transaction. Call them with `runAction(name, params)`; params are `:named`.

- `addItem` — params `{ name }`. If an item with that name exists (ignoring
  case) it is unticked; otherwise a new item is inserted. Use this instead
  of a plain INSERT so the list has no duplicates.
- `clearDone` — no params. Deletes every item with `done = 1`;
  `result.changes` is how many were deleted.

Single-row edits (tick/untick, rename, delete) are one statement each, so
they use `db.runAsync` directly.

## How the code talks to the database

- `useQuery(sql, params)` for anything shown on screen. It re-runs by
  itself when anyone changes the database. Don't copy query results into
  state.
- `useDatabase()` / `useSQLiteContext()` (the same hook; expo-sqlite's name
  is an alias) give `db.getAllAsync`, `db.getFirstAsync` and `db.runAsync`
  (expo-sqlite's API). One statement per call; use `?` placeholders with an
  array of values. Never build SQL by string concatenation.
- `runAction(name, params)` for anything that needs more than one
  statement.
- `useSpace()?.role` is `'owner'`, `'editor'` or `'viewer'`. Viewers can't
  write, so the screens hide the edit controls when the role is `'viewer'`.

## Extending it safely

- **New column**: add it to the `CREATE TABLE` in `schema.sql` with
  `NOT NULL DEFAULT <constant>` (or leave it nullable), then use it in the
  screens. Adding a column is applied automatically on the next build.
  Renaming or removing a column is destructive: prefer adding.
- **New table**: add a `CREATE TABLE` to `schema.sql`. Link rows with an
  `INTEGER` column plus `REFERENCES items(id) ON DELETE CASCADE`.
- **New action**: add `actions/<camelCaseName>.sql` with `:named` params,
  and describe it in the Actions section above.
- **New screen**: add a file under `app/` (`app/item/[id].tsx` is
  `/item/<id>`), give it a title in `app/_layout.tsx`, and link to it with
  `<Link href={{ pathname: '/item/[id]', params: { id: String(id) } }}>`.
  Route groups `(x)`, nested `_layout` files and tabs aren't supported.
- Imports are limited to `react`, `react-native`, `expo-router`,
  `expo-sqlite` and `@homeai/sdk`, plus relative files in this folder.
  There is no network (`fetch`) and no DOM (`window`, `document`).
- Keep this file up to date when you change the schema, actions or
  screens.
