# Chores

Household chores that come round again (bins every week, plants every
three days). The space's members take turns at each one: the Due tab shows
what's due today or overdue, split into "Your turn" and everyone else's,
with a Done button. Done records who did it, moves the chore on by its
interval and passes the turn to the next person. History lists who did
what.

## Files

```text
app.json                     manifest (name, slug, version, homeai block)
schema.sql                   the database schema (plain SQLite DDL)
actions/addChore.sql         add a chore (assignees are added after)
actions/completeChore.sql    mark a chore done: history, next due date, next turn
lib/chores.ts                dates (local YYYY-MM-DD), labels, CHORES_SQL (whose turn)
components/ChoreForm.tsx     the fields of a chore, used by new and edit
app/_layout.tsx              Stack: the tabs, a chore, the New chore modal
app/(tabs)/_layout.tsx       Tabs: Due (with a badge), All chores, History; "+" in the header
app/(tabs)/index.tsx         Due: due today or overdue, Done buttons
app/(tabs)/all.tsx           every chore by next due date
app/(tabs)/history.tsx       the last 200 completions, newest first
app/chore/[id].tsx           one chore: Done, edit, who did it, delete
app/new.tsx                  the New chore modal
```

## Data

`chores`: one row per chore.

| column       | type    | meaning                                                   |
|--------------|---------|-----------------------------------------------------------|
| `id`         | INTEGER | primary key                                               |
| `name`       | TEXT    | what to do, e.g. `Take out the bins`                      |
| `every_days` | INTEGER | how often, in days (at least 1; 7 is weekly)              |
| `next_due`   | TEXT    | the local date it's next due, `YYYY-MM-DD`                |
| `turn`       | INTEGER | whose turn: an index into the rotation, taken modulo its size |
| `note`       | TEXT    | free text; `''` if none                                   |

`chore_assignees`: who takes turns at a chore, `position` 0, 1, 2… in
rotation order. `user_id` is a space member's id (`useMembers()`). No rows
means anyone can do it.

`completions`: one row per time a chore was done, `done_on` the doer's
local date. **Who did it is `_created_by`**, set by the platform; there's no
column for it.

Whose turn it is comes from SQL, not app state: `CHORES_SQL` in
`lib/chores.ts` numbers each chore's assignees with `ROW_NUMBER()` and
picks the one at `turn % count`, as `turn_user`. Use it (adding `WHERE`
and `ORDER BY`) wherever a screen shows a turn.

Dates are local, not UTC: the app passes `today()` from `lib/chores.ts`
rather than using SQLite's `date('now')`, which is UTC.

## Actions

Call them with `runAction(name, params)`; params are `:named`.

- `addChore`: `{ name, every_days, next_due }`. The result's
  `lastInsertRowId` is the new chore; insert its `chore_assignees` rows
  after (as `app/new.tsx` does).
- `completeChore`: `{ chore_id, today }`. Adds a completion, sets
  `next_due` to `today` plus `every_days`, and moves `turn` on by one.
  This is what Done does; for the agent, "I did the bins" is this action.

Single-row edits (rename, change the interval, delete) are one statement
each with `db.runAsync`. Changing the assignees replaces the chore's
`chore_assignees` rows.

## Exports and reads

None. Assignees are space members, not contacts: members are the people
who use the app. If chores ever need people outside the space, read
Contacts' `people` export (`homeai.reads`).

## How the code talks to the database

- `useQuery(sql, params)` for anything on screen. It re-runs when anyone
  changes the database, so every member sees Done at once. Don't copy
  query results into state.
- `useDatabase()` gives `db.runAsync` etc.: one statement per call, `?`
  placeholders. Never build SQL by string concatenation.
- `useMembers()` for names, `useUser()` for "Your turn".
- `useSpace()?.role` is `'viewer'` for people who can only look; the
  screens hide Done and edit controls from them.

## Extending it safely

- **New column**: add it to `schema.sql` with `NOT NULL DEFAULT <constant>`
  (or nullable) and a trailing `-- comment` saying what it holds. Adding is
  applied on the next build; renaming or removing a column is destructive
  and needs the owner's approval. Prefer adding.
- **New table**: a comment line above the `CREATE TABLE`, and link rows
  with `REFERENCES chores(id) ON DELETE CASCADE`.
- **New screen**: a file under `app/`. A new tab goes in `app/(tabs)/` and
  needs a `Tabs.Screen` in `app/(tabs)/_layout.tsx`; a pushed screen or a
  modal (`presentation: 'modal'`) goes in `app/` with a `Stack.Screen` in
  `app/_layout.tsx`. Helpers and components live outside `app/`.
- Build screens from `@homeai/ui` (Screen, List, ListRow, fields, Button)
  and take colors from `useTheme()`, so dark mode works.
- Imports are limited to `react`, `react-native`, `expo-router`,
  `expo-sqlite`, `@homeai/sdk`, `@homeai/ui`, `@expo/vector-icons` and
  relative files. No network, no DOM.
- Keep this file up to date when you change the schema, actions or
  screens.
