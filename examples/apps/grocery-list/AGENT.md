# Grocery list

A shopping list shared by everyone in the space. People add items, check
them off in the store, and clear the checked ones when they're done.
Tapping an item opens a screen to edit its quantity and note, or delete it.

## Files

```text
app.json                 manifest (name, slug, version, homeai block)
schema.sql               the database schema (plain SQLite DDL)
actions/addItem.sql      add an item, or uncheck it if it's already listed
actions/clearChecked.sql delete every checked item
app/_layout.tsx          the Stack navigator and screen titles
app/index.tsx            the list: add, check/uncheck, clear checked
app/item/[id].tsx        one item: edit name/quantity/note, check, delete
```

## Data

`items` — one row per thing to buy.

| column       | type    | meaning                                          |
|--------------|---------|--------------------------------------------------|
| `id`         | INTEGER | primary key                                      |
| `name`       | TEXT    | what to buy, e.g. `Milk`; never empty            |
| `quantity`   | TEXT    | free text, e.g. `2` or `1 lb`; `''` if not given |
| `note`       | TEXT    | free text; `''` if not given                     |
| `checked`    | INTEGER | `0` = still to buy, `1` = in the cart            |
| `created_at` | TEXT    | UTC, `YYYY-MM-DD HH:MM:SS`, set by SQLite        |

The list shows unchecked items first, oldest first:
`SELECT … FROM items ORDER BY checked, id`.

## Actions

Actions are files in `actions/`; each runs all its statements in one
transaction. Call them with `runAction(name, params)`; params are `:named`.

- `addItem` — params `{ name }`. If an item with that name exists (ignoring
  case) it is unchecked; otherwise a new item is inserted. Use this instead
  of a plain INSERT so the list has no duplicates.
- `clearChecked` — no params. Deletes every item with `checked = 1`;
  `result.changes` is how many were deleted.

Single-row edits (check/uncheck, save, delete) are one statement each, so
they use `db.runAsync` directly.

## How the code talks to the database

- `useQuery(sql, params)` for anything shown on screen. It re-runs by
  itself when anyone changes the database, so the list updates live for
  every member of the space. Don't copy query results into state.
- `useDatabase()` gives `db.getAllAsync`, `db.getFirstAsync` and
  `db.runAsync` (expo-sqlite's API). One statement per call; use `?`
  placeholders with an array of values. Never build SQL by string
  concatenation.
- `runAction(name, params)` for anything that needs more than one
  statement.
- `useSpace()?.role` is `'owner'`, `'editor'` or `'viewer'`. Viewers can't
  write (the platform refuses it), so the screens hide the edit controls
  when the role is `'viewer'`.

## Extending it safely

- **New column**: add it to the `CREATE TABLE` in `schema.sql` with
  `NOT NULL DEFAULT <constant>` (or leave it nullable), then use it in the
  screens. Adding a column is applied automatically on the next build.
  Renaming or removing a column, or making one stricter, is destructive:
  the owner has to approve it and existing data may be lost. Prefer adding.
- **New table**: add a `CREATE TABLE` to `schema.sql`. Link rows with an
  `INTEGER` column plus `REFERENCES items(id) ON DELETE CASCADE`.
- **New action**: add `actions/<camelCaseName>.sql` with `:named` params,
  and describe it in the Actions section above.
- **New screen**: add a file under `app/` (`app/stores.tsx` is `/stores`,
  `app/store/[id].tsx` is `/store/<id>`), give it a title in
  `app/_layout.tsx`, and link to it with
  `<Link href={{ pathname: '/store/[id]', params: { id: String(id) } }}>`.
  Route groups `(x)`, nested `_layout` files and tabs aren't supported.
- Imports are limited to `react`, `react-native`, `expo-router`,
  `expo-sqlite` and `@homeai/sdk`, plus relative files in this folder.
  There is no network (`fetch`) and no DOM (`window`, `document`).
- Keep this file up to date when you change the schema, actions or
  screens.
