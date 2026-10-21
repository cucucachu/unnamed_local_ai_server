# Example apps

Hand-written apps in the package format of `docs/PLATFORM.md` §7. They are
the templates the platform ships: the catalog's "Start from a template"
and the agent's `create_app` copy them (M13, M22-05), so they stick to
plain standards: SQLite DDL in `schema.sql`, `:named`-parameter SQL in
`actions/`, expo-router files under `app/`, React Native components, and the
expo-sqlite-shaped API from `@homeai/sdk`.

| App | What it shows |
|---|---|
| [`grocery-list/`](grocery-list/) | Default `create_app` template. One `items` table with quantity/note, two actions (`addItem`, `clearChecked`), a list screen with live data (`useQuery`), a detail screen (`app/item/[id].tsx`), and read-only UI for viewers. Exports `items` v1 (with `addItem`) to other apps. Start with its `AGENT.md`. |
| [`chores/`](chores/) | Household chores: `chores` repeating every N days, a rota of members (`chore_assignees`, `useMembers()`) and `completions` stamped with who did it. Tabs (Due, with a badge; All; History), a modal to add one, `completeChore` moves the due date and the turn. |
| [`pantry/`](pantry/) | What's at home: `items` with quantity, unit, place and best-before. Search (`homeai.index` + `search()`), a "Use soon" section, place chips; `addItem` tops up a matching row, `useSome` deletes it when it's used up. |
| [`recipes/`](recipes/) | Recipes and a weekly meal plan: tabs, nested `recipe/[id]/index` + `edit`, two modals, search, `planMeal`. Reads the grocery list's `items` export and adds a recipe's ingredients with `runExportAction`. |
| [`contacts/`](contacts/) | People with a photo, phones, emails, structured addresses, websites, work, relationship, birthdays and other dates coming up (Feb 29 handled), all in one New/Edit form (`savePerson`); search, `mergePeople` for duplicates. Exports `people` v1 read-only. |
| [`group-chat/`](group-chat/) | A group chat for the space: one room, live (`useQuery` re-runs on every write), grouped by sender with day separators, older messages on scroll, edit or delete your own (soft-deleted, with "edited by" when someone else changed it), search. Sender and time are the platform's `_created_by` / `_created_at`. |
| [`calendar/`](calendar/) | A shared calendar with day, week (hour grids, overlapping events side by side) and month views: timed, all-day and multi-day events, repeats (daily, weekly on chosen days, monthly, yearly, with an end) expanded in the app, "this event or all events" edits and deletes (`event_skips`), who each event is for with a filter, and Contacts birthdays read from its `people` export. |
| [`connect-four/`](connect-four/) | Four in a row between two members, live: `games` and their `moves` (the board is derived), turns kept by the game screen, `dropDisc` accepts only the next move into a column with room and records a win or draw in the same action; an out-of-turn move (by `_created_by`) is pointed out. Several games at once, watching, resign, rematch. |
| [`daily-word/`](daily-word/) | A five-letter word game, one word a day for the whole space: each member's game saved as they go (`_created_by` is the player), the day's results for everyone (grids hidden until you've finished), and per-person stats and streaks. Word lists from SCOWL. |
| [`budget/`](budget/) | Money in and out: `categories` with monthly budgets, `transactions` (signed amounts) and `rules` that categorize by description (`applyRules`, `categorize`). Month view with a pie chart and budget bars, a six-month trend, and bank statements imported as CSV from App info. |
| [`workouts/`](workouts/) | `exercises`, `workouts` and their `sets` (reps and weight). `addSet`, `repeatWorkout` copies a workout to today, and each exercise has a line chart of its top weight. |
| [`health-log/`](health-log/) | Any measure over time (weight, sleep, water): `measures` say whether a day's readings add up, average or keep the last one, `entries` hold the readings. Line or bar charts by range with a goal line. |
| [`countdowns/`](countdowns/) | Days until or since a date, yearly ones counting the anniversary, plus contacts' birthdays read from the Contacts `people` export (hideable). |
| [`flashcards/`](flashcards/) | Decks of cards with spaced repetition scheduled in SQL (`grade`: Again, Hard, Good, Easy), a study session, search, and Progress charts (reviews per day, cards coming due). |
| [`kanban/`](kanban/) | Boards with columns and cards, swipe actions to move a card along or delete it, card limits per column, due dates and assignees (`useMembers()`); `moveCard` and friends keep positions dense. |
| [`books/`](books/) | Books and media: books, films, shows, games, albums and podcasts with a status (dates stamped by `setStatus`), 1–5 stars, notes, search, and a This year tab. |
| [`calculator/`](calculator/) | Calculator and units: a keypad calculator (a small parser, no `eval`), unit conversions and a tip splitter. No tables at all. |
| [`list/`](list/) | Generic checklist: `items` with `done`, `addItem` / `clearDone`, list + detail. |
| [`notes/`](notes/) | Notes: `title` / `body` / `pinned`, `addNote`, list + `app/note/[id].tsx`; `homeai.index` makes the notes searchable (chat and the search box, `search()`). |
| [`tracker/`](tracker/) | Habit tracker: `habits` + `checkins` (one row per habit per day), `addHabit` / `checkIn`, list + `app/habit/[id].tsx`. |

## Install one into a space

Apps live as files in a space's `Apps` folder; the platform registers the
folder, installs an instance in the space (with its own SQLite database),
and builds it. `scripts/e2e/app_fixture.mjs` does all of that as your own
user, through Caddy, with only Node (no npm install):

```bash
# Your Personal space
node scripts/e2e/app_fixture.mjs install --app examples/apps/grocery-list --user <you>

# A shared space you own or edit (its slug, as in Settings -> Spaces)
node scripts/e2e/app_fixture.mjs install --app examples/apps/grocery-list --user <you> --space family
```

It prompts for your password (or reads `$HOMEAI_PASSWORD`), uploads the
folder to `<space>/Apps/grocery-list`, registers it, installs it and builds
it, then prints the instance id. Open the **Apps** tab: the app is listed
under that space. Everyone in a shared space sees the same list, live;
viewers can look but not change it. `--base <url>` (default
`$HOMEAI_BASE_URL` or `http://homeai.local`) points it at another address.

Re-running `install` uploads the folder again and rebuilds, so it is also
how you try an edit. A schema change that would lose data (a dropped or
renamed column, say) isn't applied by the build; it waits for approval
(`docs/PLATFORM.md` §7 "Data").

To remove it:

```bash
node scripts/e2e/app_fixture.mjs uninstall --app examples/apps/grocery-list --user <you> [--space family]
```

Uninstalling keeps the instance's data in the space's `apps/.trash` and the
source files in `Apps/grocery-list`; delete that folder in the Files tab if
you don't want them.

The same steps with the recovery CLI, after putting the folder in the
space's `Apps` folder (e.g. by uploading it in the Files tab), are
`register-app` and `install-app` (README "Recovery CLI"); the CLI has no
build command, so build it with the script above or
`POST /api/platform/apps/<id>/build`.

## Checks

- `cd services/app-builder && npm test` builds every template here with
  the builder (zero diagnostics, every route renders), and fails if a
  folder here isn't in its `TEMPLATE_ROUTES`.
- `services/platform/tests/test_app_templates.py` validates every template
  and runs its actions against a scratch database.
- `scripts/e2e/app_template_smoke.sh` creates one from the catalog on the
  live stack and uses it.
- `scripts/e2e/grocery_app_smoke.sh` installs it in a personal and a
  shared space on the live stack and drives it in a browser (add, check,
  detail screen, clear checked, reload, live updates between members, a
  viewer can't write).
- `scripts/e2e/m22_household_templates_smoke.sh` installs chores, pantry,
  recipes, contacts and the grocery list from the catalog API into a shared
  space and drives each in a browser: add, edit and its main flow, with a
  second member taking a chore turn and a recipe's ingredients landing on
  the grocery list.
- `scripts/e2e/m22_money_health_templates_smoke.sh` installs budget,
  workouts, health-log and countdowns (and later contacts) in a personal
  space and drives each in a browser: a bank statement imported through
  App info with a bad line refused, rules, charts, sets and a repeated
  workout, readings that add up or keep the last, and a contact's birthday
  counting down.
- `scripts/e2e/m22_learning_boards_templates_smoke.sh` installs
  flashcards, kanban, books and calculator in a personal space and drives
  each in a browser: a study session whose buttons and SQL agree on the
  schedule, cards swiped across a board with the mouse, a shelf moved
  along by swipes and chips, and the calculator, conversions and tip.
