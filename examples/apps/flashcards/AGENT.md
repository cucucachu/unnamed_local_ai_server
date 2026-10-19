# Flashcards

Decks of flashcards studied with spaced repetition. **Decks** lists them
with how many cards are due, has "Study all N due", a search over every
card, and a form to make a deck. A deck shows its cards and when each is
due, a form to add one, and "Study N due". The study session shows a
card's front, then its back, and four grades: Again, Hard, Good, Easy,
each showing when the card would come back. **Progress** has the totals,
how often you remembered in the last 30 days, reviews per day and the
cards coming due in the next two weeks.

## Files

```text
app.json                 manifest; `index` makes cards searchable
schema.sql               the database schema (plain SQLite DDL)
actions/grade.sql        grade a card and schedule its next review
actions/resetCard.sql    start a card over as new
lib/flashcards.ts        types, local dates, `nextInterval` (the button labels)
app/_layout.tsx          Stack: tabs, deck, edit deck, card, study (modal)
app/(tabs)/_layout.tsx   Tabs: Decks (badge: cards due), Progress
app/(tabs)/index.tsx     decks, search, new deck
app/(tabs)/stats.tsx     totals and charts
app/deck/[id]/index.tsx  one deck: study, add a card, its cards
app/deck/[id]/edit.tsx   rename or delete a deck
app/card/[id].tsx        one card: edit, move deck, schedule, start over, delete
app/study/[deck].tsx     the review session; `deck` is an id or `all`
```

## Data

`decks`: `id`, `name`, `description`.

`cards`: one row per card, with its schedule.

| column          | type    | meaning                                                  |
|-----------------|---------|----------------------------------------------------------|
| `id`            | INTEGER | primary key                                              |
| `deck_id`       | INTEGER | `decks.id`; deleted with the deck                        |
| `front`         | TEXT    | the prompt                                               |
| `back`          | TEXT    | the answer                                               |
| `due`           | TEXT    | local `YYYY-MM-DD` it's next due; a new card: the day added |
| `interval_days` | INTEGER | days from the last review to `due`; 0 if new or forgotten |
| `ease`          | REAL    | how much the interval grows on Good; 2.5 to start, at least 1.3 |
| `reps`          | INTEGER | reviews in a row not graded Again                        |
| `lapses`        | INTEGER | times forgotten after being learned                      |

`reviews`: `id`, `card_id` (deleted with the card), `day`, `grade` (0
Again, 1 Hard, 2 Good, 3 Easy).

A card is new when `reps = 0 AND interval_days = 0`, due when
`due <= today`, and counted as learned when `interval_days >= 21`.

## Actions

Call them with `runAction(name, params)`; params are `:named`.

- `grade`: `{ card_id, grade, today }`. Logs the review and schedules the
  card, SM-2 style. Again: due today, ease −0.2, `reps` back to 0. Hard:
  interval ×1.2 (at least 1 day), ease −0.15. Good: 1 day, then 3, then
  interval × ease. Easy: 4 days, then interval × ease × 1.3, ease +0.15.
  `nextInterval` in `lib/flashcards.ts` gives the same numbers for the
  buttons: change both together.
- `resetCard`: `{ card_id, today }`. Makes the card new again, due today.
  Its reviews stay.

Adding decks and cards, editing and deleting are one `db.runAsync` each.
New cards are inserted with `due` = today.

## Exports and reads

None. Exporting `cards` would let another app (a language app, say) add
cards to a deck.

## How the code talks to the database

- `useQuery(sql, params)` for anything on screen; it re-runs when anyone
  changes the database. Don't copy results into state.
- `useDatabase()` gives `db.runAsync` / `db.getAllAsync` etc.: one
  statement per call, `?` placeholders. Never build SQL by string
  concatenation.
- `search(query)` finds cards by meaning and by words (the `index` in
  `app.json`); it returns row ids, best first.
- `useSpace()?.role` is `'viewer'` for people who can only look; the
  screens hide the add, edit, grade and delete controls from them.

## Extending it safely

- **New column** (e.g. `hint` on `cards`, or `new_per_day` on `decks` to
  limit new cards): add it to `schema.sql` with `NOT NULL DEFAULT
  <constant>` (or nullable) and a trailing `-- comment`. Adding is applied
  on the next build; renaming or removing is destructive and needs the
  owner's approval.
- **Different scheduling**: change `actions/grade.sql` and `nextInterval`
  together. Past reviews are kept in `reviews`, so a new scheme can be
  replayed from them.
- **New table**: a comment line above the `CREATE TABLE`; link rows with
  `REFERENCES cards(id) ON DELETE CASCADE`.
- **New screen**: a file under `app/` with a `Stack.Screen` in
  `app/_layout.tsx` (`presentation: 'modal'` for a sheet); a new tab goes
  in `app/(tabs)/` with a `Tabs.Screen`. Helpers live outside `app/`.
- Charts come from `@homeai/ui` (`LineChart`, `BarChart`, `PieChart`);
  take colors from `useTheme()`.
- Imports are limited to `react`, `react-native`, `expo-router`,
  `expo-sqlite`, `@homeai/sdk`, `@homeai/ui`, `@expo/vector-icons` and
  relative files. No network, no DOM.
- Keep this file up to date when you change the schema, actions or
  screens.
