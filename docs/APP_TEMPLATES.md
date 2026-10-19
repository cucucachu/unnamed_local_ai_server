# App framework and templates (M22): design

A set of installable app templates covering the everyday phone apps that
aren't social media or a separate service, and the framework changes that
building them calls for. The templates are the exercise; the framework is
the product. A second milestone (M23, §10) takes on what reaches outside an
app's own database: the files bridge, tool calls from apps, camera and
microphone capture, and messages for shared spaces.

The apps runtime, SDK, data and registry are in `docs/PLATFORM.md` §7 and
`docs/ARCHITECTURE.md` §3 "Apps". This note only describes what changes.

Contents:

1. [Principles](#1-principles)
2. [Files or a database](#2-files-or-a-database)
3. [Templates you can install](#3-templates-you-can-install)
4. [Navigation and the UI kit](#4-navigation-and-the-ui-kit)
5. [The agent and app data](#5-the-agent-and-app-data)
6. [Memory over time](#6-memory-over-time)
7. [Shared entities](#7-shared-entities)
8. [Files: viewing and editing text](#8-files-viewing-and-editing-text)
9. [The templates](#9-the-templates)
10. [M23](#10-m23)

## 1. Principles

- **Anything a UI can do, the agent can do.** Every app's data stays
  reachable through the agent's tools, and now also findable: the agent
  knows which apps exist and what they hold, and search covers app rows as
  well as files.
- **Keep the SDK small and expo-shaped.** New surfaces follow expo-router
  and React Native names, so what the model already knows still applies.
  A UI kit sits on top as a convenience, never a requirement.
- **Templates are starting points the user owns.** Installing one copies
  its source into the space, the same as `create_app` does. The agent
  customizes it with the normal file tools and `build_app`.
- **Open formats outlive apps.** App data is SQLite plus CSV export; prose
  is markdown in files.

## 2. Files or a database

Two stores, each for what it does well:

- **Files** hold what a person reads or writes as a whole: prose and
  documents, anything with its own format (markdown, PDF, photos, audio),
  anything that should outlive the app that made it, and the memory notes.
  Text files are indexed for search.
- **An app's database** holds many small records of the same shape that
  are filtered, sorted, totalled, edited by several people at once, or
  toggled in a UI: budget entries, workouts, chores, inventory, review
  state.
- **Both:** records point at files by virtual path (a recipe's photo, an
  expense's receipt). Showing those files in an app needs the files bridge
  (M23), so M22's templates avoid them.

Prose-shaped apps (journal, notes) belong in markdown files with an app UI
over them. They move to M23 with the files bridge; the existing `notes`
template stays as it is until then.

## 3. Templates you can install

Before M22-05 templates lived only in agent-server (`/app/templates`, from
`examples/apps/`), and only the agent's `create_app` used them. Space
catalogs listed published apps only. Shipped in M22-05 (PLATFORM.md §7
"Templates"); the catalog has one Templates section with a space picker
rather than one per space.

- **The platform ships the templates** (copied into its image from
  `examples/apps/`) and lists them: `GET /api/platform/app-templates` with
  each template's `app.json` and `AGENT.md`. agent-server's `create_app`
  uses that list instead of its own copy, so there's one source.
- **Each space's catalog gets a Templates section** next to published
  apps. Installing a template, for an editor or owner of the space, does
  what `create_app` does: copy the source to `<space>/Apps/<slug>/`, patch
  `slug` and `name`, register, install tracking `working`, build. The app
  is then the user's own, and "Ask the agent" can change it.
- **Provenance:** `app.json`'s `homeai.template` records
  `{slug, version}`. No update path in M22: an installed template is a
  copy, like a fork. A later "the template changed" diff can use it.
- **Every template carries a smoke**: it builds with no diagnostics,
  renders its screens in the build's smoke step, and its actions run
  against a scratch database (a platform test over every shipped
  template).

## 4. Navigation and the UI kit

The router shim is a single `Stack` with a back button and a title. Real
phone apps need more.

**Router** (expo-router names and file layout):
- Route groups `(name)/` and nested `_layout.tsx` files.
- `Tabs` and `Tabs.Screen` (`title`, `icon` as an Ionicons name, `badge`)
  for a bottom tab bar, as in expo-router's `(tabs)/_layout.tsx`.
- `Stack.Screen options.presentation: 'modal'` for sheets that slide up
  over the current screen.
- `headerRight` / `headerLeft` in screen options, for "+" and "Edit".
- The builder's route checks and diagnostics cover each of these.

**`@homeai/ui`** (a new allowed module, built into the runtime):
- Layout and lists: `Screen`, `Section`, `List`, `ListRow` (title,
  subtitle, value, chevron, swipe actions), `Card`, `EmptyState`.
- Inputs: `TextField`, `NumberField`, `DateField`, `TimeField`, `Select`,
  `Toggle`, `Stepper`, `SearchField`, `Button`, `IconButton`, `Chip`.
- Display: `Icon` (Ionicons), `ProgressBar`, `Badge`, `Money`, `Duration`.
- Charts: `BarChart`, `LineChart`, `PieChart` over plain arrays.
- A theme that follows the host (light/dark), with its tokens available to
  apps (`useTheme()`).

The sandbox always renders with react-native-web, on the phone too (a
WebView), so the kit can use web implementations internally (SVG for
charts, the browser's date and time inputs) while app code keeps seeing
React Native components and never touches the DOM. The kit is part of
the runtime bundle (one cacheable file per SDK version), which is ≈146 KB
gzip today; the kit should add under ~100 KB.

The authoring guide (`APP_AUTHORING_GUIDE`) and templates show the kit
first. It's part of the static system prompt, so its size matters (#341).

## 5. The agent and app data

The agent can query any app it can see (`app_query`), but nothing tells it
which apps exist or what they hold, and search covers files only. After
years and dozens of apps, that's what breaks first.

### 5.1 The data map

A short list in every thread of the apps in the user's spaces: name,
space, one line on what it holds, and its tables with row counts. It's
built at the thread's start and kept for the thread, next to the memory
core (#341's per-user context), e.g.:

```text
Apps (query with app_query; details with list_apps):
- Budget (Personal): monthly spending by category.
  expenses (1,204), categories (14), budgets (12)
- Chores (Family): rotating household chores.
  chores (9), completions (311)
```

- The one-liner is `app.json`'s `homeai.description`.
- Table and column descriptions come from `schema.sql` comments: the
  comment above a `CREATE TABLE`, and a trailing `-- ...` on a column. The
  templates already write them; the platform parses them on build and
  `list_apps` shows them too.
- Capped at ~1,500 characters. Past that, the list shortens to app names
  and the agent calls `list_apps`.

Shipped in M22-06: `GET /api/platform/data-map` (PLATFORM.md §7 "The data
map"), agent-server's `DataMapMiddleware` (ARCHITECTURE.md), and comments
on every table and column of the shipped templates (checked by
`test_app_templates.py`).

### 5.2 App rows in search

An app declares which tables hold text worth finding:

```json
"homeai": {
  "index": [
    {"table": "recipes", "title": "name", "text": ["ingredients", "steps"], "fields": ["cuisine"]}
  ]
}
```

- The platform renders each row (title, fields as `name: value`, then the
  text columns) into chunks next to the files' (`app_row_chunks`, beside
  `file_chunks`), keyed by instance, table and row id. As built: PLATFORM.md
  §5 "File search", "App rows" (M22-07).
- `POST /files/search` and `search_files` return app rows next to file
  chunks, ranked together, with a reference the agent can query
  (`Recipes (Personal) recipes #12`). The same space permissions apply.
- Freshness: a `db_changed` queues the instance; the indexer reads rows
  whose `_updated_at` is newer than its last pass and drops rows that are
  gone. The periodic scan catches anything missed.
- Inside the app, `search(query)` from `@homeai/sdk` searches only that
  instance's indexed rows (by meaning and by words), which gives templates
  search without FTS tables in `schema.sql`.
- Numeric tables (workouts, measurements) usually don't need indexing; the
  data map and SQL cover them.
- The Memory routine reads indexed rows that changed, too (§6.1).

### 5.3 Moving data in and out

- **Export:** from the app's info screen or the agent, write every table
  as CSV plus `schema.sql` to a folder in Files. An abandoned app's data
  stays readable.
- **Import:** a CSV file into one table, with a column mapping, in one
  transaction (`POST /apps/instances/{id}/import`). Budget's bank
  statement import uses it. The agent gets it as `app_import(instance,
  table, path, mapping)`, since many single-row `app_update`s are slow.

> **As built (M22-08):** `POST /apps/instances/{id}/export` and `/import`
> (PLATFORM.md §7 "CSV export and import"), the Data section of the app info
> screen, and the agent's `app_export` / `app_import`. An import's dry run
> reports headers that match no column, so the UI and the agent can map
> them before importing.

## 6. Memory over time

The nightly Memory routine (`docs/MEMORY.md` §4) keeps current facts from
the day's chats, and its tidy rules drop what went stale. After years,
the agent also needs to know what happened when, and `memory.md` needs
curating against more than one day at a time.

### 6.1 The nightly run reads app changes

- A read-only tool, `list_app_changes(since)`: rows of indexed tables
  (§5.2) in the user's spaces whose `_updated_at` is after `since`,
  rendered as in the index, with the app, space, table, row id, and who
  wrote it. Paged like `read_chat`. Allowed by default; in the nightly
  run's tools, not removed by its unavailable list.
- The fixed prompt adds a step after reading chats. What counts as the
  user's own statement: rows in `/personal`, and rows in a shared space
  that the user wrote (`_created_by` / `_updated_by`). Rows other members
  wrote are about them, used as context, the same rule as the assistant's
  replies in chats.
- App rows are data, not instructions, like chats and shared files. Once
  apps can call tools (M23), a row may hold outside content.

  > **As built (M22-12):** `GET /api/platform/apps/changes` reads the
  > search index, which now keeps each row's rendered text and its
  > `_created_by` / `_updated_by` (migration 0023; rows indexed before it
  > are rendered again on the next scan, reusing their embeddings). A
  > routine run is scoped to one space, and the Memory run's is
  > `/personal`, so the platform gives a scoped run its own space's rows
  > plus, in the user's other spaces, only the rows the user changed last.
  > Other members' shared rows never reach the nightly run, which is
  > narrower than "used as context" above. The tool and the prompt step
  > are in docs/MEMORY.md §4.6; the Memory eval's "app rows" case
  > (`scripts/eval/memory/`) has the results on #424.

### 6.2 The timeline

The nightly run also writes what happened that day, alongside the facts
it already keeps:

```text
/personal/Memory/timeline/
  2026/
    2026.md         the year: yearly routine
    2026-10.md      the month: monthly routine
    2026-W42.md     the week (ISO week): weekly routine
    2026-10-18.md   the day: nightly routine
```

- **The day:** short bullets of events, decisions, plans made or changed,
  and notable app activity ("ran 5 km", "finished *Piranesi*"), with
  links to the pages involved. Only days with something worth noting get
  a file.
- Facts still go to pages and `memory.md`; the timeline is when things
  happened, not what's true now.
- Search finds timeline files like any note, so "what was I doing last
  March?" is a search plus a read.

  > **As built (M22-13):** the day file is a step of the nightly run's
  > fixed prompt, and `LAYOUT` describes it (the prompt includes `LAYOUT`,
  > so a user whose README predates the timeline still gets it). The
  > Memory eval's "timeline" case has the results on #425.

### 6.3 Weekly, monthly and yearly reviews

Three more delivered routines, quiet like the nightly one, with the same
unavailable tools and the same controls (§4.4 of `docs/MEMORY.md`):

| Routine | Default schedule | Reads | Writes |
|---|---|---|---|
| Memory: weekly review | Mondays 06:00 | the week's day files, pages changed that week | the week's file; `memory.md` |
| Memory: monthly review | the 1st, 07:00 | the month's week and day files, pages changed that month | the month's file; `memory.md`; pages |
| Memory: yearly review | January 1, 08:00 | the year's month files | the year's file; `memory.md`; pages |

The per-user offset applies, as for the nightly run. Each covers the
period that just ended; a run that failed doesn't move `{since}`, so the
next one catches up.

What each writes:

- **The period's file:** a summary of the period from the files below it
  (days for a week, weeks for a month, months for a year), with links
  down to them. A period with nothing worth keeping gets no file, like a
  day.
- **`memory.md`, curated** (all three): it gets a short **Lately**
  section (the past week or two and the month, a few lines each) kept by
  the weekly and monthly runs. Past plans leave Current plans (into the
  timeline if they happened); things that came up again and again move
  up; things that haven't mattered for a while move down into their pages.
  It stays under the 6,000-character target.
- **Pages** (monthly and yearly): merge duplicates, fix links, and mark
  people, places and interests that haven't come up in a long time
  ("- Not mentioned since Mar 2027") rather than deleting them.

The review routines read notes only, not chats or app rows: the nightly
run is the one place facts enter memory, so the injection rules stay in
one place.

The scheduler gets a `yearly` kind (`{"kind": "yearly", "month": 1,
"day": 1, "time": "08:00"}`), and a review routine's schedule can't be
made more frequent than its period (a weekly review that runs daily
would rewrite the same week).

These replace M21's "memory tidy-up routine" (#338, closed unshipped).

  > **As built (M22-14):** the three are `memory_weekly`,
  > `memory_monthly` and `memory_yearly` in
  > `app/routines/delivered.py`, with prompts in
  > `app/routines/review_prompts.py`. The run works out which periods it
  > covers in code (every period that ended since the last successful
  > run; for a first run, or a Run now mid-period, the last one that
  > ended) and puts
  > them in the fixed prompt as file paths, so the model does no calendar
  > arithmetic. "Pages changed that period" became **the pages the
  > period's files link to**: `ls` shows no modification times, and the
  > day files already link every page the nightly run touched. Besides
  > the nightly run's unavailable tools, the reviews can't use
  > `list_chats`, `read_chat` or `list_app_changes`. `min_period` (7, 28
  > or 365 days) is on the routine: the API refuses a more frequent
  > schedule with a 422, the routine tools say why, and the edit screen
  > only offers the kinds that fit. The admin switch is per routine (§4.4
  > of `docs/MEMORY.md`), like the nightly run's.

## 7. Shared entities

People, places and dates show up in many apps. There's no platform-wide
schema for them; templates share through cross-app exports (`docs/PLATFORM.md`
§7 "cross-app exports"):

- **Contacts** owns people and exports `people` (v1: id, name, birthday,
  notes). Templates that refer to people outside the space's members read
  it.
- **Space members** (`useMembers()`) are for people who use the app
  (chore assignees), not contacts.
- **Recipes → Grocery list:** "add ingredients to the list" runs the
  grocery list's `addItem` through an export action.
- **A cross-app reference** stores the export's id and shows a missing row
  plainly instead of failing.
- `AGENT.md` in each template names what it exports and reads, so the
  agent extends the same conventions.

## 8. Files: viewing and editing text

Files opens video, audio and images; everything else opens the action
sheet. Markdown is the most common text in the system (memory notes,
READMEs, AGENT.md), so text comes first. Files is a native host screen, so
this is host work.

- **A version check on writes.** `GET /files/stat` returns a `version`
  (mtime in nanoseconds plus size). `/write`, `/content` and `/edit` take an
  optional `if_version`; a mismatch is `409 changed`, nothing written. The
  agent and the memory routine write these files too.

  > **As built (M22-09):** `version` is `"<mtime ns>-<size>"` on file
  > entries (stat and listings), read responses, and the responses of
  > `/write`, `/content` and `/edit`, so an editor can save again without
  > another stat. A missing file also counts as changed. ARCHITECTURE.md §3
  > "Files" has the routes.
- **Text and markdown:** tapping a text file opens a viewer (markdown
  rendered with the chat's `Markdown` component; other text monospace).
  Edit switches to a plain editor; Save writes with `if_version`. On `409`:
  reload, keep mine (overwrite), or save as a copy. Up to 1 MiB, the same
  limit as the search index.

  > **As built (M22-10):** `src/app/file.tsx` (route `/file?path=`, a stack
  > screen over the tabs). Files opens it for a known text or code
  > extension, or a name with no extension, up to 1 MiB; the content must
  > decode as UTF-8 without NULs or the screen says it isn't text. It stats
  > first (version, writable), then reads the whole file from
  > `/files/stream`. Markdown links resolve relative to the file's folder,
  > never above its space (`linkPathFrom` in `lib/fileLink.ts`), and open
  > in Files with `router.dismissTo`. Save is `/files/write` with
  > `if_version`. On `409 changed` it offers Reload theirs, Keep mine
  > (written without `if_version`) and Save as copy (`name (copy).md`, then
  > `(copy 2)` and so on, opened after saving). Leaving with unsaved changes
  > asks first (`beforeRemove`, and `beforeunload` on web). Smoke:
  > `scripts/e2e/m22_text_editor_smoke.sh`.
- **Then:** CSV as a table, JSON and code in monospace with line numbers,
  and PDF opened in the browser's or phone's own viewer.

  > **As built (M22-11):** the same `/file` screen picks the view from the
  > extension (`textViewOf` in `lib/textFiles.ts`). `.csv`/`.tsv` render
  > as a table (`components/CsvTable.tsx`): RFC 4180 quoting, tab for
  > `.tsv`, `;` when the header has more semicolons than commas, the first
  > 5,000 rows with "Showing 5,000 of N rows". `.md` is rendered markdown;
  > `.txt`, `.log` and names without an extension are plain monospace;
  > everything else (JSON, YAML, source) is `components/CodeView.tsx`,
  > monospace with a line-number gutter, no wrapping. Edit works the same
  > in every view. PDFs (`openPdf` in `lib/files.ts`) open the stream URL
  > in a new tab on web; on the phone they're downloaded to the cache and
  > handed to the system viewer: an `ACTION_VIEW` intent on Android
  > (`expo-intent-launcher`, a new native module, so the phone app needs a
  > rebuild) and the share sheet on iOS.
- "Open with" an app is M23 (it needs the files bridge).

## 9. The templates

Existing: `grocery-list`, `list`, `notes`, `tracker` (habits). New in M22:

| Template | What it exercises |
|---|---|
| Chores | tabs, members as assignees, rotation in SQL |
| Pantry | quantities, expiry dates, date fields, search |
| Recipes + meal plan | nested routes, modal, index, export action to the grocery list |
| Contacts | the `people` export, birthdays, search |
| Budget | charts, CSV import, money, monthly views |
| Workouts | sets and reps, line chart of progress |
| Health log | any measurement over time (weight, sleep, water) |
| Countdowns | dates, "days until", contacts' birthdays via `people` |
| Flashcards | spaced repetition scheduling in SQL |
| Kanban | columns, moving cards, swipe actions |
| Books and media | status, rating, search |
| Calculator and units | no data at all |

Each has an `AGENT.md`, `schema.sql` comments, an `index` declaration
where it holds text, and its smoke. Gaps a template finds become framework
issues in this milestone, or are written down for M23.

> **As built (M22-16):** `examples/apps/chores`, `pantry`, `recipes` and
> `contacts`. Chores has three tabs (Due, with a badge of what's due; All;
> History) and a rota in `chore_assignees`. The rota is ordered by
> position, `turn` indexes it with `ROW_NUMBER()`, and `completeChore`
> moves `next_due` and the turn; who did it is the completion's
> `_created_by`. Pantry's `addItem` tops up the same thing in the same
> place (name in any case, unit, expiry date) instead of adding a row, and
> `useSome` deletes it at zero. Recipes has `(tabs)/index` and `plan`,
> nested `recipe/[id]/index` and `edit`, and modals `new` and
> `plan/[day]`. Contacts keeps phone numbers and addresses in
> `contact_details`, which isn't exported, and `mergePeople` folds a
> duplicate in. The grocery list now exports `items` v1 with `addItem`.
> The templates found three framework gaps, fixed here. An export action
> needs the target copy (#448: `runExportAction` and `_instance` on merged
> views). A copy with no rows yet is invisible in the merged view (#450:
> `homeai_exports`). And `homeai_exports` only existed once something was
> attached, so a reader with nothing installed got a SQL error at runtime
> and failed its smoke render at build: it now always exists, empty, for
> an app with reads, both in the build's `reads.sql` and in the runtime
> connection. Smoke: `scripts/e2e/m22_household_templates_smoke.sh`.

> **As built (M22-17):** `examples/apps/budget`, `workouts`, `health-log`
> and `countdowns`. Budget stores signed amounts (spending negative) and
> categorizes through `rules`: a description containing a rule's pattern,
> in any case, goes in its category, the longest pattern winning.
> "Always use this category" suggests the description without trailing
> store numbers ("TRADER JOE'S #552" gives "TRADER JOE'S"). Bank
> statements come in through the existing CSV import in App info, mapping
> their headers; a row the table refuses (a US-style date, say) fails at
> its line and nothing is imported. Workouts keeps `sets` per workout and
> exercise, and `repeatWorkout` copies one to a new day. Health log has
> one table of readings for any number of `measures`, each saying how a
> day's readings combine (`last`, `sum`, `average`); the screens group by
> day in SQL from that. Countdowns reads Contacts' `people` v1 and checks
> `homeai_exports` before touching `contacts_people`, which only exists
> once a copy is attached; hidden birthdays are kept per copy and person.
> No framework gaps this time. Smoke:
> `scripts/e2e/m22_money_health_templates_smoke.sh`.

> **As built (M22-18):** `examples/apps/flashcards`, `kanban`, `books`
> (named "Books and media") and `calculator` ("Calculator and units").
> Flashcards schedules in `actions/grade.sql`, SM-2 style: one `UPDATE`
> with `CASE` on the grade for interval, ease, reps and lapses, then
> `due = date(:today, '+N days')`. The buttons show the same intervals
> from `nextInterval` in `lib/flashcards.ts`, and the smoke checks the two
> agree. The study queue puts cards already seen today last, so one graded
> Again comes back after the rest. Kanban keeps card and column positions
> dense (0, 1, 2, …) through `moveCard`, `deleteCard`, `moveColumn` and
> `deleteColumn`, and refuses a move to another board. Its swipe actions
> are `ListRow`'s `actions`, driven in the smoke by a real mouse drag as
> well as a long press. Books stamps `started_on` and `finished_on` in
> `setStatus`; the edit form does the same when the status changes. The
> calculator has a `schema.sql` holding only a comment, since the file is
> required but may be empty. No framework gaps. One rough edge: a typed
> icon field (an array of row actions built with a spread, a table of
> kinds) needs `IconName` from `@expo/vector-icons`. Smoke:
> `scripts/e2e/m22_learning_boards_templates_smoke.sh`.

## 10. M23

Reaching outside an app's own database:

- **Files bridge:** read, write and pick files in the space, show images,
  play media. Then journal and notes over markdown files, a photo gallery,
  a music player, and "open with" for file types.
- **Tool calls from apps:** the manifest declares tools
  (`homeai.permissions.tools`), the user grants them at install, and
  `callTool(name, args)` runs as the person using the app under their
  permissions, with the usual approval card and the gateway's approval
  marker. Builds on #410. Settles #320/#321.
- **Camera and microphone,** captured by the host: `capturePhoto()`,
  `recordAudio()`, `scanCode()` open the host's capture screen, save a file
  in the space and return its path. The app never gets a live stream.
- **Messages** for shared spaces.
- With M20's notifications: to-do, medications, clock.
