# Books and media

Books, films, shows, games, albums and podcasts, and where you are with
each. **Shelf** groups them (On it now, Want to, Finished, Gave up), with
chips to filter by status and kind and a search over titles, people and
notes. Swipe a row (or long-press it) to start something you wanted to, or
to finish or give up on what you're on. An item has its title, creator,
kind, status with dates, a 1–5 star rating and notes. **This year** counts
what you finished by kind, charts it month by month, and lists your
favorites.

## Files

```text
app.json                 manifest; `index` makes items searchable
schema.sql               the database schema (plain SQLite DDL)
actions/setStatus.sql    change the status, stamping the dates
lib/books.ts             types, kinds, statuses and how to word them
components/Stars.tsx     the tap-to-rate stars
components/ItemForm.tsx  the fields, for add and edit
app/_layout.tsx          Stack: the tabs, an item, Add (modal)
app/(tabs)/_layout.tsx   Tabs: Shelf (with "+"), This year
app/(tabs)/index.tsx     the shelf, filters, search, swipe actions
app/(tabs)/year.tsx      this year's totals, chart, favorites
app/item/[id].tsx        one item: start/finish buttons, edit, delete
app/new.tsx              add one
```

## Data

One table, `items`:

| column        | type    | meaning                                                        |
|---------------|---------|----------------------------------------------------------------|
| `id`          | INTEGER | primary key                                                    |
| `title`       | TEXT    | the title                                                      |
| `creator`     | TEXT    | author, director, studio, artist or host; `''` if not known    |
| `kind`        | TEXT    | `book`, `film`, `show`, `game`, `album` or `podcast`           |
| `status`      | TEXT    | `want`, `started`, `finished` or `dropped`                     |
| `rating`      | INTEGER | 1–5, or null for not rated                                     |
| `started_on`  | TEXT    | local `YYYY-MM-DD`, or null                                    |
| `finished_on` | TEXT    | local `YYYY-MM-DD` it was finished or dropped, or null         |
| `notes`       | TEXT    | thoughts, quotes, who recommended it                           |

## Actions

Call them with `runAction(name, params)`; params are `:named`.

- `setStatus`: `{ id, status, today }`. Starting or finishing sets
  `started_on` if it's empty; finishing or dropping sets `finished_on` if
  it's empty; going back to `want` clears both. The edit form does the
  same when you change the status, and the dates can be edited after.

Adding, editing and deleting are one `db.runAsync` each.

## Exports and reads

None. Exporting `items` would let another app (a book club, say) read
what everyone has finished.

## How the code talks to the database

- `useQuery(sql, params)` for anything on screen; it re-runs when anyone
  changes the database. Don't copy results into state.
- `useDatabase()` gives `db.runAsync` / `db.getAllAsync` etc.: one
  statement per call, `?` placeholders. Never build SQL by string
  concatenation.
- `search(query)` finds items by meaning and by words (the `index` in
  `app.json`: title, creator and notes); it returns row ids, best first.
- `useSpace()?.role` is `'viewer'` for people who can only look; the
  screens hide the add, edit, rate and delete controls from them.

## Extending it safely

- **New column** (e.g. `pages`, `progress`, or `recommended_by`): add it
  to `schema.sql` with `NOT NULL DEFAULT <constant>` (or nullable) and a
  trailing `-- comment`. Adding is applied on the next build; renaming or
  removing is destructive and needs the owner's approval.
- **New kind**: the `CHECK` on `kind` lists them, so a new one changes the
  table (the owner approves it), plus an entry in `KINDS` and `VERB` in
  `lib/books.ts`.
- **New table** (e.g. quotes per item): a comment line above the `CREATE
  TABLE`; link rows with `REFERENCES items(id) ON DELETE CASCADE`.
- **New screen**: a file under `app/` with a `Stack.Screen` in
  `app/_layout.tsx` (`presentation: 'modal'` for a sheet); a new tab goes
  in `app/(tabs)/` with a `Tabs.Screen`. Helpers live outside `app/`.
- Imports are limited to `react`, `react-native`, `expo-router`,
  `expo-sqlite`, `@homeai/sdk`, `@homeai/ui`, `@expo/vector-icons` and
  relative files. No network, no DOM.
- Keep this file up to date when you change the schema, actions or
  screens.
