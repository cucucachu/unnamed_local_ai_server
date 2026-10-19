# Pantry

What's kept at home, in the cupboards, fridge and freezer: how much is
left, where it is and when it expires. The main screen has a search box,
a **Use soon** section (expired, or expiring in the next 7 days), chips to
filter by place, and the list. Tapping an item opens it: Use 1 (or Use it
up), edit, or delete.

## Files

```text
app.json                 manifest; homeai.index makes items searchable
schema.sql               the database schema (plain SQLite DDL)
actions/addItem.sql      add an item, or top up the same one
actions/useSome.sql      take some; delete the row when it's used up
lib/pantry.ts            dates (local YYYY-MM-DD), labels, the Item type
components/ItemForm.tsx  the fields of an item, used by new and edit
app/_layout.tsx          Stack: the list (with "+"), an item, the Add modal
app/index.tsx            search, Use soon, place chips, the list
app/item/[id].tsx        one item: use, edit, delete
app/new.tsx              the Add modal
```

## Data

`items`: one row per thing.

| column       | type    | meaning                                                 |
|--------------|---------|---------------------------------------------------------|
| `id`         | INTEGER | primary key                                             |
| `name`       | TEXT    | what it is, e.g. `Basmati rice`                         |
| `quantity`   | REAL    | how much is left, in `unit` (0 or more)                 |
| `unit`       | TEXT    | `kg`, `cans`, `L`…; `''` for a plain count              |
| `location`   | TEXT    | `Pantry`, `Fridge`, `Freezer`, or any other place       |
| `expires_on` | TEXT    | best-before, `YYYY-MM-DD`; NULL if it keeps             |
| `note`       | TEXT    | free text; `''` if none                                 |

Dates are local: the app computes them in JS (`today()`, `inDays(n)` in
`lib/pantry.ts`) rather than with SQLite's UTC `date('now')`.

## Search

`homeai.index` in `app.json` indexes `items` (title `name`, text `note`,
field `location`), so the agent's `search_files` finds pantry items, and the
search box calls `search(query)` from `@homeai/sdk`: this instance's rows
by meaning and by words, best first. A new item shows up in search a
moment after it's written. There's no FTS table.

## Actions

Call them with `runAction(name, params)`; params are `:named`.

- `addItem`: `{ name, quantity, unit, location, expires_on, note }`. If the
  same thing is already in that place (same name in any case, unit and
  expiry date), it adds to its quantity instead of making a second row.
  Use this for "we bought 2 more cans of tomatoes".
- `useSome`: `{ id, amount }`. Takes `amount` off; at 0 the row is deleted.
  `changes` is 2 when it was used up.

Single-row edits (save, delete) are one `db.runAsync` each.

## Exports and reads

None yet. A natural next step is exporting `items` so Recipes can show
what's already at home.

## How the code talks to the database

- `useQuery(sql, params)` for anything on screen; it re-runs when anyone
  changes the database. Don't copy results into state.
- `useDatabase()` gives `db.runAsync` etc.: one statement per call, `?`
  placeholders. Never build SQL by string concatenation.
- `useSpace()?.role` is `'viewer'` for people who can only look; the
  screens hide the add, use and edit controls from them.

## Extending it safely

- **New column**: add it to `schema.sql` with `NOT NULL DEFAULT <constant>`
  (or nullable) and a trailing `-- comment`. Adding is applied on the next
  build; renaming or removing is destructive and needs the owner's
  approval. A text column worth searching can join `homeai.index`'s `text`.
- **New table**: a comment line above the `CREATE TABLE`; link rows with
  `REFERENCES items(id) ON DELETE CASCADE`.
- **New screen**: a file under `app/` with a `Stack.Screen` in
  `app/_layout.tsx` (`presentation: 'modal'` for a sheet). Tabs go in an
  `app/(tabs)/` group with its own `_layout.tsx`. Helpers and components
  live outside `app/`.
- Build screens from `@homeai/ui` and take colors from `useTheme()`.
- Imports are limited to `react`, `react-native`, `expo-router`,
  `expo-sqlite`, `@homeai/sdk`, `@homeai/ui`, `@expo/vector-icons` and
  relative files. No network, no DOM.
- Keep this file up to date when you change the schema, actions or
  screens.
