# Workouts

Workouts as sets of reps and weight per exercise. **Workouts** lists the
sessions, newest first; "+" starts one today. A workout shows its sets
grouped by exercise and a form to add the next set (the exercise, reps and
weight stay filled in for the next one); "Repeat this workout today" copies
it. **Exercises** lists them with their best set; an exercise shows its
progress: the heaviest set per workout (a line chart), the volume per
workout (a bar chart), and its history.

## Files

```text
app.json                   manifest
schema.sql                 the database schema (plain SQLite DDL)
actions/addSet.sql         add a set to the end of a workout
actions/repeatWorkout.sql  a new workout with the same sets as another
lib/workouts.ts            types, local dates, "5 × 60 kg"
app/_layout.tsx            Stack: the tabs, a workout, an exercise
app/(tabs)/_layout.tsx     Tabs: Workouts (with "+"), Exercises
app/(tabs)/index.tsx       the workouts
app/(tabs)/exercises.tsx   the exercises, and a form to add one
app/workout/[id].tsx       one workout: sets, add a set, note, repeat, delete
app/exercise/[id].tsx      one exercise: charts, history, rename, delete
```

## Data

`exercises`: `id`, `name`, `unit` (`kg`, `lb`, or `''` for bodyweight
only), `note`.

`workouts`: `id`, `day` (local `YYYY-MM-DD`), `note`.

`sets`: one row per set.

| column        | type    | meaning                                          |
|---------------|---------|--------------------------------------------------|
| `id`          | INTEGER | primary key                                      |
| `workout_id`  | INTEGER | `workouts.id`; deleted with the workout          |
| `exercise_id` | INTEGER | `exercises.id`; deleted with the exercise        |
| `reps`        | INTEGER | how many, 0 or more                              |
| `weight`      | REAL    | in the exercise's unit; 0 for bodyweight         |
| `position`    | INTEGER | the order within the workout                     |

Progress is plain SQL over `sets`: `max(weight)` per workout for the
heaviest set, `sum(reps * weight)` for volume.

## Actions

Call them with `runAction(name, params)`; params are `:named`.

- `addSet`: `{ workout_id, exercise_id, reps, weight }`. Appends the set
  (position = last + 1); nothing happens if either id doesn't exist.
- `repeatWorkout`: `{ workout_id, day }`. Starts a workout on `day` with
  the same sets. The new workout is `SELECT max(id) FROM workouts`.

Starting an empty workout, editing a note and deleting are one
`db.runAsync` each.

## Exports and reads

None.

## How the code talks to the database

- `useQuery(sql, params)` for anything on screen; it re-runs when anyone
  changes the database. Don't copy results into state.
- `useDatabase()` gives `db.runAsync` / `db.getAllAsync` etc.: one
  statement per call, `?` placeholders. Never build SQL by string
  concatenation.
- `useSpace()?.role` is `'viewer'` for people who can only look; the
  screens hide the add, edit and delete controls from them.

## Extending it safely

- **New column** (e.g. `rpe` or `duration_seconds` on `sets` for cardio):
  add it to `schema.sql` with `NOT NULL DEFAULT <constant>` (or nullable)
  and a trailing `-- comment`. Adding is applied on the next build;
  renaming or removing is destructive and needs the owner's approval.
- **New table**: a comment line above the `CREATE TABLE`; link rows with
  `REFERENCES workouts(id) ON DELETE CASCADE`.
- **New screen**: a file under `app/` with a `Stack.Screen` in
  `app/_layout.tsx` (`presentation: 'modal'` for a sheet); a new tab goes
  in `app/(tabs)/` with a `Tabs.Screen`. Helpers live outside `app/`.
- Charts come from `@homeai/ui` (`LineChart`, `BarChart`: `data`, `x`,
  `y`); take colors from `useTheme()`.
- Imports are limited to `react`, `react-native`, `expo-router`,
  `expo-sqlite`, `@homeai/sdk`, `@homeai/ui`, `@expo/vector-icons` and
  relative files. No network, no DOM.
- Keep this file up to date when you change the schema, actions or
  screens.
