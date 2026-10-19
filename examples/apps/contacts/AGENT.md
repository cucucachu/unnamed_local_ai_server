# Contacts

The people you know. The main screen has a search box (by meaning and by
words, over names and notes), **Birthdays coming up** (the next 30 days)
and everyone, A–Z. A contact shows their phone numbers, emails and
addresses, their name, birthday and notes, and lets you merge in a
duplicate or delete them.

## Files

```text
app.json                   manifest; homeai.index, homeai.exports (people v1)
schema.sql                 the database schema (plain SQLite DDL)
actions/mergePeople.sql    merge two entries for the same person
lib/contacts.ts            types, birthdays (days until, age), local dates
components/PersonForm.tsx  name, birthday, notes; used by new and edit
app/_layout.tsx            Stack: the list (with "+"), a contact, the New modal
app/index.tsx              search, birthdays coming up, everyone
app/person/[id].tsx        one contact: details, edit, merge, delete
app/new.tsx                the New contact modal
```

## Data

`people`: one row per person. **Exported**, so other apps depend on it.

| column     | type    | meaning                                    |
|------------|---------|--------------------------------------------|
| `id`       | INTEGER | primary key                                |
| `name`     | TEXT    | how they're known, e.g. `Priya Shah`       |
| `birthday` | TEXT    | `YYYY-MM-DD`; NULL if not known            |
| `notes`    | TEXT    | anything worth remembering; `''` if none   |

`contact_details`: ways to reach someone. Not exported.

| column      | type    | meaning                                       |
|-------------|---------|-----------------------------------------------|
| `id`        | INTEGER | primary key                                   |
| `person_id` | INTEGER | `people.id`; deleted with the person          |
| `kind`      | TEXT    | `phone`, `email` or `address`                 |
| `label`     | TEXT    | e.g. `mobile`, `work`; `''` if none           |
| `value`     | TEXT    | the number, email or address                  |

Birthdays are worked out in JS (`daysUntilBirthday` in `lib/contacts.ts`):
a Feb 29 birthday falls on Feb 28 in other years.

## Actions

- `mergePeople`: `{ keep_id, drop_id }`. Moves `drop_id`'s details to
  `keep_id`, fills in a missing birthday, appends the notes, and deletes
  `drop_id`. Does nothing when they're the same id or either is missing.

Other edits are one `db.runAsync` each.

## Exports and reads

`homeai.exports` offers `people` v1: the `people` table, read-only, with
no actions. Another app that declares
`"reads": [{ "app": "contacts", "export": "people", "version": "1" }]`
sees every Contacts copy it can reach as one view, `contacts_people`
(`id`, `name`, `birthday`, `notes`, plus `_space` and `_instance`).

So **don't rename or remove** `people`'s columns, or change what they
mean, without bumping the export to `version: "2"` (readers asking for v1
then stop seeing it). Adding a column is fine. Phone numbers and
addresses stay private to this app.

## How the code talks to the database

- `useQuery(sql, params)` for anything on screen; it re-runs when anyone
  changes the database. Don't copy results into state.
- `useDatabase()` gives `db.runAsync` etc.: one statement per call, `?`
  placeholders. Never build SQL by string concatenation.
- `useSpace()?.role` is `'viewer'` for people who can only look; the
  screens hide the add, edit, merge and delete controls from them.

## Extending it safely

- **New column**: add it to `schema.sql` with `NOT NULL DEFAULT <constant>`
  (or nullable) and a trailing `-- comment`. Adding is applied on the next
  build; renaming or removing is destructive and needs the owner's
  approval. A text column worth searching can join `homeai.index`'s `text`.
- **New kind of detail**: add it to the `CHECK` on `contact_details.kind`
  and to `KINDS` in `lib/contacts.ts`.
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
