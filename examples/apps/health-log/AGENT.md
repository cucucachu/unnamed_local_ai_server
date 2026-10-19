# Health log

Any measurement over time: weight, sleep, water, steps, blood pressure…
The main screen lists each measure with its latest day's value; "+" logs
a reading. A measure shows a chart over 30 days, 90 days, a year or
everything (bars for measures that add up, a line otherwise, with the goal
as a second line), the average and how often the goal was met, its
readings, and its settings.

## Files

```text
app.json                        manifest
schema.sql                      the database schema (plain SQLite DDL)
actions/addStarterMeasures.sql  weight, sleep and water
lib/health.ts                   types, DAILY (a day's value per measure), formatting
components/MeasureForm.tsx      the settings of a measure, used by new and edit
app/_layout.tsx                 Stack: the list (with "+"), a measure, two modals
app/index.tsx                   the measures with their latest values
app/measure/[id].tsx            one measure: chart, stats, readings, settings
app/log.tsx                     the Log a reading modal (?measure=<id> picks one)
app/new-measure.tsx             the New measure modal
```

## Data

`measures`

| column     | type    | meaning                                                 |
|------------|---------|---------------------------------------------------------|
| `id`       | INTEGER | primary key                                             |
| `name`     | TEXT    | e.g. `Weight`                                           |
| `unit`     | TEXT    | e.g. `kg`, `hours`, `glasses`; `''` for none            |
| `goal`     | REAL    | a daily target; NULL for none                           |
| `decimals` | INTEGER | 0 to 3, for display                                     |
| `summary`  | TEXT    | how a day's readings combine: `last`, `sum`, `average`  |

`entries`: `id`, `measure_id` (deleted with the measure), `day` (local
`YYYY-MM-DD`), `value` (in the measure's unit), `note`.

A day's value depends on `summary`: weight is the day's last reading,
water the sum, heart rate the average. `DAILY` in `lib/health.ts` is that
as SQL, one row per measure and day; use it as a CTE:
`WITH daily AS (${DAILY}) SELECT day, value FROM daily WHERE measure_id = ?`.

## Actions

- `addStarterMeasures`: `{}`. Weight (kg, last), Sleep (hours, sum, goal
  8) and Water (glasses, sum, goal 8), skipping names that exist.

Logging, editing and deleting are one `db.runAsync` each.

## Exports and reads

None. Exporting `entries` (with `measures`) would let another app, a
Workouts or a doctor's-visit app, show the same numbers.

## How the code talks to the database

- `useQuery(sql, params)` for anything on screen; it re-runs when anyone
  changes the database. Don't copy results into state.
- `useDatabase()` gives `db.runAsync` etc.: one statement per call, `?`
  placeholders. Never build SQL by string concatenation.
- `useSpace()?.role` is `'viewer'` for people who can only look; the
  screens hide the log, edit and delete controls from them.

## Extending it safely

- **A measure with two numbers** (blood pressure): two measures
  (Systolic, Diastolic) is simplest; or a nullable `value2 REAL` on
  `entries` and a second series in the chart.
- **New column**: add it to `schema.sql` with `NOT NULL DEFAULT <constant>`
  (or nullable) and a trailing `-- comment`. Adding is applied on the next
  build; renaming or removing is destructive and needs the owner's
  approval. A new `summary` kind needs the CHECK, `SUMMARIES` and `DAILY`.
- **New screen**: a file under `app/` with a `Stack.Screen` in
  `app/_layout.tsx` (`presentation: 'modal'` for a sheet). Tabs go in an
  `app/(tabs)/` group with its own `_layout.tsx`. Helpers live outside
  `app/`.
- Charts come from `@homeai/ui`; take colors from `useTheme()`.
- Imports are limited to `react`, `react-native`, `expo-router`,
  `expo-sqlite`, `@homeai/sdk`, `@homeai/ui`, `@expo/vector-icons` and
  relative files. No network, no DOM.
- Keep this file up to date when you change the schema, actions or
  screens.
