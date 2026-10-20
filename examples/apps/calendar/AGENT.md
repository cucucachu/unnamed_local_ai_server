# Calendar

A shared calendar for the space, in month, week and day views. Month is a
grid of the weeks covering the month (tap a day to open it in Day view);
Week and Day are hour grids with an all-day strip on top, overlapping
events side by side, and a red line at the current time. The arrows (or a
sideways swipe) move by a day, week or month; Today jumps back. Tap an
empty hour to add an event there, or use +. Events can be timed or all
day, last several days, repeat, and say who they're for; the filter under
the heading shows one person's events only. Birthdays from an attached
Contacts copy show as all-day items (in a different color) and can't be
edited here. Other members' changes appear as they're made: every screen
re-reads whenever the database changes. Viewers can look but not edit.

## Files

```text
app.json                    manifest; homeai.index over events; reads Contacts' people
schema.sql                  the database schema (plain SQLite DDL)
actions/saveEvent.sql       add or update an event and who it's for (optionally skipping one occurrence)
actions/deleteEvent.sql     delete an event, every occurrence
lib/calendar.ts             types, date maths, repeats, the time-grid layout, form <-> params
lib/useOccurrences.ts       everything on screen for a range: occurrences and birthdays
lib/useEvent.ts             one event row
components/MonthView.tsx    the month grid
components/TimeGrid.tsx     the hour grid for Day (one column) and Week (seven)
components/EventForm.tsx    the New / Edit form
app/_layout.tsx             Stack: calendar, event, edit (modal), new (modal)
app/index.tsx               the calendar: view chips, arrows, person filter, the views
app/event/[id]/index.tsx    one occurrence: when, repeat, location, who, notes; edit and delete
app/event/[id]/edit.tsx     edit the whole series (scope=all) or one occurrence (scope=one)
app/new.tsx                 new event (params: date, optional time)
```

## Data

`events`: one row per event; a repeating event is one row, the series.

| column         | type    | meaning                                                         |
|----------------|---------|-----------------------------------------------------------------|
| `id`           | INTEGER | primary key                                                     |
| `title`        | TEXT    | what it is                                                      |
| `location`     | TEXT    | where, free text; `''` for none                                 |
| `notes`        | TEXT    | anything else; `''` for none                                    |
| `all_day`      | INTEGER | `1` for all-day (then both times are NULL), `0` for timed       |
| `start_date`   | TEXT    | local YYYY-MM-DD it starts; a series' first occurrence          |
| `start_time`   | TEXT    | local HH:MM; NULL when all-day                                  |
| `end_date`     | TEXT    | local YYYY-MM-DD it ends (all-day: the last day, inclusive)     |
| `end_time`     | TEXT    | local HH:MM; NULL when all-day                                  |
| `repeat`       | TEXT    | `none`, `daily`, `weekly`, `monthly` or `yearly`                |
| `repeat_every` | INTEGER | every N days / weeks / months / years                           |
| `repeat_days`  | TEXT    | weekly: weekdays as `"1,3"` (0 = Sunday); `''` = start's weekday |
| `repeat_until` | TEXT    | last date an occurrence may start; NULL repeats forever         |

`event_people` (`event_id`, `user_id`): who an event is for, space members
by user id (`useMembers()`). No rows means it's for nobody in particular;
the person filter then leaves it out.

`event_skips` (`event_id`, `date`): occurrences of a series that were
deleted, or edited into an event of their own. `date` is the occurrence's
start date.

Times are wall-clock local, with no time zone: "9:00 on the 19th" is 9:00
wherever you look at it. A timed event can run past midnight (`end_date`
after `start_date`). Who created or last changed an event are the
platform's `_created_by` / `_updated_by`; never write those yourself.

## Repeats

Occurrences are never stored. `occurrenceDates` in `lib/calendar.ts`
works out which start dates of a series overlap the range on screen,
leaving out `event_skips`; every occurrence has the series' length.

- **monthly** keeps the day of the month and skips months without it
  (a series on the 31st has none in April); **yearly** on Feb 29 only
  falls in leap years. Birthdays are different: Feb 29 shows on Feb 28.
- **Edit this event**: `saveEvent` with `:id` NULL saves the edited copy
  as a new, non-repeating event, and `:skip_event_id` / `:skip_date` skip
  the original occurrence, in one action.
- **Edit all events**: `saveEvent` with the series' `:id`. The form shows
  the series' first occurrence.
- **Delete this event** is one `INSERT OR IGNORE INTO event_skips`;
  **Delete all events** is `deleteEvent`.

## How the code talks to the database

- `useOccurrences(from, to, person)` is one `useQuery` over `events`
  (with `group_concat` of its people and skips) for the range, plus
  Contacts' birthdays. It re-runs when anyone changes the database. Don't
  copy results into state.
- `contacts_people` only exists while a Contacts copy is attached, so the
  birthdays query asks `homeai_exports` first (as Countdowns does).
- `saveEvent` and `deleteEvent` are actions (`runAction`), with the
  form's values from `saveEventParams`; `:people` is a JSON list of user
  ids, read with `json_each`.
- `useSpace()?.role` is `'viewer'` for people who can only look: no +, no
  empty-slot taps, no Edit or Delete.

## Extending it safely

- **New column**: add it to `schema.sql` with `NOT NULL DEFAULT <constant>`
  (or nullable) and a trailing `-- comment`, add it to `EVENT_SELECT`,
  `CalendarEvent`, the form and `saveEvent`. Adding is applied on the next
  build; renaming or removing, or changing a CHECK, is destructive and
  needs the owner's approval.
- **Colors or categories**: a `color TEXT NOT NULL DEFAULT ''` column (or a
  `categories` table and a nullable `category_id`), used by MonthView and
  TimeGrid instead of `theme.colors.tint`.
- **More from Contacts**: its other dates (anniversaries) are in its
  `dates` table, which it doesn't export yet; that needs Contacts to add
  `dates` to its `people` export first.
- **Reminders** need notifications, which apps can't send yet.
- Build screens from `@homeai/ui` and take colors from `useTheme()`.
- Imports are limited to `react`, `react-native`, `expo-router`,
  `expo-sqlite`, `@homeai/sdk`, `@homeai/ui`, `@expo/vector-icons` and
  relative files. No network, no DOM.
- Keep this file up to date when you change the schema or screens.
