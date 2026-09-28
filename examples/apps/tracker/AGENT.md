# Tracker

A shared habit tracker. People add habits and check them off each day.
Tapping a habit opens its history.

## Files

```text
app.json               manifest (name, slug, version, homeai block)
schema.sql             the database schema (plain SQLite DDL)
actions/addHabit.sql   insert a habit named :name
actions/checkIn.sql    mark :habit_id done on :day (YYYY-MM-DD)
app/_layout.tsx        the Stack navigator and screen titles
app/index.tsx          the list of habits and today's check-ins
app/habit/[id].tsx     one habit: history, check in, delete
```

## Data

`habits` — one row per habit.

| column       | type    | meaning                                   |
|--------------|---------|-------------------------------------------|
| `id`         | INTEGER | primary key                               |
| `name`       | TEXT    | the habit, e.g. `Walk`; never empty       |
| `created_at` | TEXT    | UTC, `YYYY-MM-DD HH:MM:SS`, set by SQLite |

`checkins` — one row per habit per calendar day it was done.

| column       | type    | meaning                                          |
|--------------|---------|--------------------------------------------------|
| `id`         | INTEGER | primary key                                      |
| `habit_id`   | INTEGER | `REFERENCES habits(id) ON DELETE CASCADE`        |
| `day`        | TEXT    | calendar day, `YYYY-MM-DD`                       |
| `created_at` | TEXT    | UTC, when it was checked in                      |

Unique on `(habit_id, day)` so a habit can be checked in at most once a day.

The home screen lists habits with whether today is checked:
`SELECT h.id, h.name, CASE WHEN c.id IS NULL THEN 0 ELSE 1 END AS done_today FROM habits h LEFT JOIN checkins c ON c.habit_id = h.id AND c.day = date('now') ORDER BY h.id`.

## Actions

Actions are files in `actions/`; each runs all its statements in one
transaction. Call them with `runAction(name, params)`; params are `:named`.

- `addHabit` — params `{ name }`. Inserts a habit unless the name is empty
  or already exists (ignoring case).
- `checkIn` — params `{ habit_id, day }`. `day` is `YYYY-MM-DD`. A second
  check-in on the same day is ignored (`INSERT OR IGNORE`).

Unticking today and deleting a habit are one statement each, so they use
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
  `INTEGER` column plus `REFERENCES habits(id) ON DELETE CASCADE`.
- **New action**: add `actions/<camelCaseName>.sql` with `:named` params,
  and describe it in the Actions section above.
- **New screen**: add a file under `app/` (`app/habit/[id].tsx` is
  `/habit/<id>`), give it a title in `app/_layout.tsx`, and link to it with
  `<Link href={{ pathname: '/habit/[id]', params: { id: String(id) } }}>`.
  Route groups `(x)`, nested `_layout` files and tabs aren't supported.
- Imports are limited to `react`, `react-native`, `expo-router`,
  `expo-sqlite` and `@homeai/sdk`, plus relative files in this folder.
  There is no network (`fetch`) and no DOM (`window`, `document`).
- Keep this file up to date when you change the schema, actions or
  screens.
