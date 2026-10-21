# Daily Word

A five-letter word game, one word a day. Guess it in six tries: each guess
colors its letters green (right place), yellow (in the word, elsewhere) or
gray (not in it), and the on-screen keyboard keeps each letter's best
color. Everyone in the space gets the same word on the same day; each
person plays their own game, saved after every guess (leave and come
back). The Today tab lists how everyone is doing; their colored grids (no
letters) show once you've finished yours. Stats has your played, win %,
current and best streak, and how many guesses your wins took, plus
everyone's in a list. Viewers can watch but not play.

## Files

```text
app.json                 manifest
schema.sql               the database schema (plain SQLite DDL)
lib/words.ts             the word lists (SCOWL; keep its notice): ANSWERS in daily order, GUESSES accepted
lib/game.ts              the day's word, marking a guess, keyboard colors, stats and streaks
components/Board.tsx     the 6×5 board and the keyboard
app/_layout.tsx          Stack around the tabs
app/(tabs)/_layout.tsx   Tabs: Play, Today, Stats
app/(tabs)/index.tsx     Play: today's game
app/(tabs)/today.tsx     everyone's game today
app/(tabs)/stats.tsx     your stats and everyone's
```

## Data

`games`: one row per member per day, made at their first guess.

| column    | type    | meaning                                                  |
|-----------|---------|----------------------------------------------------------|
| `id`      | INTEGER | primary key                                              |
| `day`     | TEXT    | the local date played, YYYY-MM-DD                        |
| `word`    | TEXT    | that day's word, lowercase (kept, so the lists can change) |
| `guesses` | TEXT    | the guesses so far, lowercase, comma-separated, in order |
| `status`  | TEXT    | `playing`, then `won` or `lost`                          |

Who played is the platform's `_created_by` (a user id); never add a
player column or write it yourself. "My game today" is
`WHERE day = ? AND _created_by = ?` with `useUser()?.id`; names come from
`useMembers()`.

The app keeps to one game per member per day and only ever changes your
own, but that's the app's choice: every editor of the space can write
this database (the agent too).

## The word

`wordFor(day)` is `ANSWERS[days since 2026-01-01 % ANSWERS.length]`, so
everyone gets the same word on the same local date. ANSWERS is already
shuffled; reordering or editing it changes future days' words (saved
games keep theirs in `word`). A guess must be in `GUESSES`.

`mark(guess, word)`: right-place letters first, then a letter elsewhere is
yellow only as many times as the word still has it (guess "speed" for
"abide": one yellow e, one gray).

## Stats

`statsOf(games)` over a person's finished games: played, won, the guess
counts of wins, the best run of consecutive won days, and the current run
back from today (or from yesterday while today's isn't finished). A lost
or missed day ends a run.

## Extending it safely

- **New column**: add it to `schema.sql` with `NOT NULL DEFAULT <constant>`
  (or nullable) and a trailing `-- comment`. Adding is applied on the next
  build; renaming or removing, or changing a CHECK, is destructive and
  needs the owner's approval.
- **Hard mode** (revealed hints must be used): check the new guess against
  `mark` of the earlier ones in `submit`; store the choice per person in a
  `settings` table keyed by `_created_by`.
- **Other lengths**: `LENGTH` plus a word list per length, and a `length`
  column on `games`.
- Build screens from `@homeai/ui` and take colors from `useTheme()`.
- Imports are limited to `react`, `react-native`, `expo-router`,
  `expo-sqlite`, `@homeai/sdk`, `@homeai/ui`, `@expo/vector-icons` and
  relative files. No network, no DOM.
- Keep this file up to date when you change the schema or screens.
