# Contacts

The people you know. The main screen has a search box (by meaning and by
words, over names, nicknames, company, job title, relationship and notes),
**Coming up** (birthdays and other dates in the next 30 days) and
everyone, A–Z, with their photo. A contact shows their photo, nickname,
work and relationship, phones, emails, addresses (press and hold to copy
one), websites, dates and notes, and lets you merge in a duplicate or
delete them. **New** and **Edit** are one form with all of it: add or
remove as many phones, emails, addresses, websites and dates as you like,
each with a label (preset, or Custom…).

## Files

```text
app.json                    manifest; homeai.index, homeai.exports (people v1)
schema.sql                  the database schema (plain SQLite DDL)
actions/savePerson.sql      add or update a person and replace all their lists
actions/mergePeople.sql     merge two entries for the same person
lib/contacts.ts             types, labels, address format, savePerson params, dates
lib/usePerson.ts            one person and all their rows, live
components/PersonForm.tsx   the New / Edit form
app/_layout.tsx             Stack: the list (with "+"), a contact, New and Edit modals
app/index.tsx               search, coming up, everyone
app/person/[id]/index.tsx   one contact: everything about them, merge, delete
app/person/[id]/edit.tsx    the Edit modal
app/new.tsx                 the New contact modal
```

## Data

`people`: one row per person. **Exported**, so other apps depend on it.

| column         | type    | meaning                                          |
|----------------|---------|--------------------------------------------------|
| `id`           | INTEGER | primary key                                      |
| `name`         | TEXT    | how they're known, e.g. `Priya Shah`             |
| `birthday`     | TEXT    | `YYYY-MM-DD`; NULL if not known                  |
| `notes`        | TEXT    | anything worth remembering; `''` if none         |
| `nickname`     | TEXT    | e.g. `Pree`; `''` if none                        |
| `company`      | TEXT    | where they work; `''` if none                    |
| `job_title`    | TEXT    | e.g. `Dentist`; `''` if none                     |
| `relationship` | TEXT    | who they are to you, e.g. `sister`; `''` if none |
| `photo`        | TEXT    | a photo id from `pickImage`; NULL if none        |

`contact_details`: phones and emails. Not exported.

| column      | type    | meaning                                       |
|-------------|---------|-----------------------------------------------|
| `id`        | INTEGER | primary key                                   |
| `person_id` | INTEGER | `people.id`; deleted with the person          |
| `kind`      | TEXT    | `phone` or `email` (`address`: see below)     |
| `label`     | TEXT    | e.g. `mobile`, `work`; `''` if none           |
| `value`     | TEXT    | the number or email                           |

Rows of kind `address` are one-line addresses from version 1.0. The
contact screen still shows them; the Edit form loads them as street line
1, and saving turns them into `addresses` rows. Don't write new ones.

`addresses`: postal addresses in parts (`label`, `street1`, `street2`,
`city`, `region`, `postcode`, `country`, all TEXT, `''` if empty).
`formatAddress` in `lib/contacts.ts` lays them out as lines.
`websites`: `label`, `url` (as typed). `dates`: `label` (e.g.
`anniversary`), `date` (`YYYY-MM-DD`, yearly like a birthday). All three
have `person_id` and are deleted with the person. Not exported.

Birthdays and dates are worked out in JS (`daysUntilBirthday` in
`lib/contacts.ts`): a Feb 29 date falls on Feb 28 in other years.

Photos are stored with the app's data by the host (`pickImage` /
`PhotoField`); the row keeps only the id. `PersonForm` deletes a photo
that was picked and not saved, and the old one once a new one is saved;
deleting a contact deletes their photo.

## Actions

- `savePerson`: `{ id, name, nickname, company, job_title, relationship,
  birthday, notes, photo, details, addresses, websites, dates }`. `id`
  NULL adds the person. The last four are JSON lists (`details`:
  `{kind, label, value}`; `addresses`: the columns above; `websites`:
  `{label, url}`; `dates`: `{label, date}`) that replace what the person
  had. Returns `[{ id }]`. Build the params with `savePersonParams`.
- `mergePeople`: `{ keep_id, drop_id }`. Moves `drop_id`'s phones,
  emails, addresses, websites and dates to `keep_id`, fills in what
  `keep_id` is missing (birthday, nickname, company, job title,
  relationship, photo), appends the notes, and deletes `drop_id`. Does
  nothing when they're the same id or either is missing.

Deleting a person is one `db.runAsync`.

## Exports and reads

`homeai.exports` offers `people` v1: the `people` table, read-only, with
no actions. Another app that declares
`"reads": [{ "app": "contacts", "export": "people", "version": "1" }]`
sees every Contacts copy it can reach as one view, `contacts_people`
(the columns above, plus `_space` and `_instance`). `photo` there is only
an id: another app can't show it.

So **don't rename or remove** `people`'s columns, or change what they
mean, without bumping the export to `version: "2"` (readers asking for v1
then stop seeing it). Adding a column is fine. Phones, emails,
addresses, websites and dates stay private to this app.

## How the code talks to the database

- `useQuery(sql, params)` for anything on screen; it re-runs when anyone
  changes the database. Don't copy results into state.
- `useDatabase()` gives `db.runAsync` etc.: one statement per call, `?`
  placeholders. Never build SQL by string concatenation.
- `useSpace()?.role` is `'viewer'` for people who can only look; the
  screens hide the add, edit, merge and delete controls from them.
- A save that touches several tables is one action (`savePerson`), so
  it all happens or none of it does.

## Extending it safely

- **New column**: add it to `schema.sql` with `NOT NULL DEFAULT <constant>`
  (or nullable) and a trailing `-- comment`. Adding is applied on the next
  build; renaming or removing is destructive and needs the owner's
  approval. A text column worth searching can join `homeai.index`'s `text`.
- **New kind of detail**: a new table like `websites` (changing the
  `CHECK` on `contact_details.kind` rebuilds the table, which needs the
  owner's approval), a list in `PersonForm`, a JSON param in
  `savePerson.sql` and `savePersonParams`, and a line in `mergePeople.sql`.
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
