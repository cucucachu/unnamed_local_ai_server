# Notes

A shared notebook. People add notes with a title, tap one to edit its
body, and pin the ones they want at the top.

## Files

```text
app.json              manifest (name, slug, version, homeai block)
schema.sql            the database schema (plain SQLite DDL)
actions/addNote.sql   insert a note with :title
app/_layout.tsx       the Stack navigator and screen titles
app/index.tsx         the list: add, pin/unpin
app/note/[id].tsx     one note: edit title/body, pin, delete
```

## Data

`notes` — one row per note.

| column       | type    | meaning                                          |
|--------------|---------|--------------------------------------------------|
| `id`         | INTEGER | primary key                                      |
| `title`      | TEXT    | the heading; never empty                         |
| `body`       | TEXT    | the note; `''` if not given                      |
| `pinned`     | INTEGER | `0` = normal, `1` = stays at the top             |
| `created_at` | TEXT    | UTC, `YYYY-MM-DD HH:MM:SS`, set by SQLite        |
| `updated_at` | TEXT    | UTC, set on insert and whenever the note is saved |

The list shows pinned notes first, then newest:
`SELECT … FROM notes ORDER BY pinned DESC, updated_at DESC`.

## Actions

Actions are files in `actions/`; each runs all its statements in one
transaction. Call them with `runAction(name, params)`; params are `:named`.

- `addNote` — params `{ title }`. Inserts a new note. The body starts
  empty; edit it on the detail screen.

Single-row edits (save, pin, delete) are one statement each, so they use
`db.runAsync` directly.

## How the code talks to the database

- `useQuery(sql, params)` for anything shown on screen. It re-runs by
  itself when anyone changes the database. Don't copy query results into
  state.
- `useDatabase()` gives `db.getAllAsync`, `db.getFirstAsync` and
  `db.runAsync` (expo-sqlite's API). One statement per call; use `?`
  placeholders with an array of values. Never build SQL by string
  concatenation.
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
  `INTEGER` column plus `REFERENCES notes(id) ON DELETE CASCADE`.
- **New action**: add `actions/<camelCaseName>.sql` with `:named` params,
  and describe it in the Actions section above.
- **New screen**: add a file under `app/` (`app/note/[id].tsx` is
  `/note/<id>`), give it a title in `app/_layout.tsx`, and link to it with
  `<Link href={{ pathname: '/note/[id]', params: { id: String(id) } }}>`.
  Route groups `(x)`, nested `_layout` files and tabs aren't supported.
- Imports are limited to `react`, `react-native`, `expo-router`,
  `expo-sqlite` and `@homeai/sdk`, plus relative files in this folder.
  There is no network (`fetch`) and no DOM (`window`, `document`).
- Keep this file up to date when you change the schema, actions or
  screens.
