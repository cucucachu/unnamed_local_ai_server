# Countdowns

Days until the things you're waiting for: trips, deadlines, and yearly
anniversaries, plus the birthdays of everyone in the Contacts app. The
main screen shows the next one large, then everything coming up (soonest
first) and past one-off dates. Long-press a birthday to hide it.

## Files

```text
app.json                    manifest; homeai.index, homeai.reads (contacts' people)
schema.sql                  the database schema (plain SQLite DDL)
actions/hideBirthday.sql    stop showing a contact's birthday
lib/countdowns.ts           days until, yearly dates (Feb 29), labels
components/CountdownForm.tsx the fields of a countdown, used by new and edit
app/_layout.tsx             Stack: the list (with "+"), a countdown, the New modal
app/index.tsx               next up, coming up, past, birthdays
app/countdown/[id].tsx      one countdown: when, edit, delete
app/new.tsx                 the New countdown modal
```

## Data

`countdowns`

| column   | type    | meaning                                                       |
|----------|---------|---------------------------------------------------------------|
| `id`     | INTEGER | primary key                                                   |
| `title`  | TEXT    | e.g. `Lisbon trip`                                            |
| `day`    | TEXT    | `YYYY-MM-DD`; for a yearly one, the first time it happened    |
| `yearly` | INTEGER | 1 to count to the next anniversary each year                  |
| `note`   | TEXT    | free text; `''` if none                                       |

`hidden_birthdays`: `instance` (a Contacts copy's instance id) and
`person_id` (its `people.id`). This is a reference into another app's data,
so it may point at someone since deleted; that row is just never matched.

Days are worked out in JS (`lib/countdowns.ts`): `daysUntil` for a date,
`nextYearly` for an anniversary or birthday (a Feb 29 date falls on Feb 28
in other years).

## Actions

- `hideBirthday`: `{ instance, person_id }`. "Show N hidden birthdays"
  deletes every `hidden_birthdays` row.

Adding, editing and deleting countdowns are one `db.runAsync` each.

## Exports and reads

`homeai.reads` asks for Contacts' `people` export (v1). The platform
attaches every Contacts copy the user can see, read-only:

- `homeai_exports` has one row per copy (`_instance`, `_space`, `app`,
  `export`); it's empty when Contacts isn't installed.
- `contacts_people` is their people, merged: `id`, `name`, `birthday`,
  `notes`, `_space`, `_instance`. It **doesn't exist** until a Contacts
  copy is attached, so `app/index.tsx` counts `homeai_exports` first and
  only then queries it (otherwise a constant empty query).
- Contacts can't be written from here (its export has no actions).

## How the code talks to the database

- `useQuery(sql, params)` for anything on screen; it re-runs when anyone
  changes the database. Don't copy results into state.
- `useDatabase()` gives `db.runAsync` etc.: one statement per call, `?`
  placeholders. Never build SQL by string concatenation.
- `useSpace()?.role` is `'viewer'` for people who can only look; the
  screens hide the add, edit and hide controls from them.

## Extending it safely

- **New column** (an icon, a color, a time of day): add it to `schema.sql`
  with `NOT NULL DEFAULT <constant>` (or nullable) and a trailing
  `-- comment`. Adding is applied on the next build; renaming or removing
  is destructive and needs the owner's approval.
- **More from Contacts**: only `people`'s exported columns are there; ask
  for more by adding them to Contacts' export (a new version if their
  meaning changes).
- **New screen**: a file under `app/` with a `Stack.Screen` in
  `app/_layout.tsx` (`presentation: 'modal'` for a sheet). Tabs go in an
  `app/(tabs)/` group with its own `_layout.tsx`. Helpers live outside
  `app/`.
- Build screens from `@homeai/ui` and take colors from `useTheme()`.
- Imports are limited to `react`, `react-native`, `expo-router`,
  `expo-sqlite`, `@homeai/sdk`, `@homeai/ui`, `@expo/vector-icons` and
  relative files. No network, no DOM.
- Keep this file up to date when you change the schema, actions or
  screens.
