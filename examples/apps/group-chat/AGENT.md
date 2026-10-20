# Group chat

A group chat for everyone in the space: one room, newest at the bottom,
the message box pinned under it. Messages are grouped by sender (the name
shows when someone new speaks, or after a few minutes' gap) with a day
separator ("Today", "Yesterday", …) at each new day. Scrolling up loads
older ones. Tap one of your own messages to edit or delete it. Other
members' messages appear as they're sent: the chat re-reads whenever the
database changes. The header's search finds messages by meaning and by
words. Viewers can read but not post.

## Files

```text
app.json                   manifest; homeai.index over message text
schema.sql                 the database schema (plain SQLite DDL)
lib/chat.ts                the Message type, day and time labels, grouping
components/MessageRow.tsx  one message: day separator, sender, bubble
app/_layout.tsx            Stack: the room (titled after the space), search
app/index.tsx              the room: messages, edit/delete bar, composer
app/search.tsx             search messages
```

## Data

`messages`: one row per message.

| column        | type    | meaning                                              |
|---------------|---------|------------------------------------------------------|
| `id`          | INTEGER | primary key; also the order they were sent in        |
| `body`        | TEXT    | the message, plain text; `''` once deleted           |
| `edited`      | INTEGER | `1` once it has been changed                         |
| `deleted`     | INTEGER | `1` once deleted (it stays, as "Message deleted")    |

Who sent a message and when are the platform's `_created_by` (a user id)
and `_created_at` (UTC ISO); who last changed it, `_updated_by` /
`_updated_at`. Never add a sender column or write those yourself. Names
come from `useMembers()`, and "mine" is `_created_by === useUser()?.id`.

Only the sender sees Edit and Delete, but that is the app's choice: every
editor of the space can write this database (the agent too). So the
screens say "edited by …" or "deleted by …" whenever `_updated_by` isn't
the sender. Deleting clears `body` and sets `deleted`, rather than
removing the row, for the same reason.

## How the code talks to the database

- The room is one `useQuery`: `ORDER BY id DESC LIMIT ?`, newest first,
  in an `inverted` `FlatList` (so they sit at the bottom); reaching the
  top raises the limit by `PAGE`. It re-runs when anyone changes the
  database. Don't copy results into state.
- Sending is `INSERT INTO messages (body) VALUES (?)`; editing and
  deleting are one `UPDATE` each (`db.runAsync`, `?` placeholders).
- `useSpace()?.role` is `'viewer'` for people who can only look: no
  message box, and their own messages can't be selected.

## Extending it safely

- **New column**: add it to `schema.sql` with `NOT NULL DEFAULT <constant>`
  (or nullable) and a trailing `-- comment`. Adding is applied on the next
  build; renaming or removing is destructive and needs the owner's
  approval.
- **Several rooms**: a `rooms` table and a `room_id` column on `messages`
  (nullable, so existing messages stay in the main room), a room list as
  the home screen and the room at `app/room/[id].tsx`.
- **Reactions or replies**: their own table keyed by `message_id`; don't
  pack them into `body`.
- Build screens from `@homeai/ui` and take colors from `useTheme()`.
- Imports are limited to `react`, `react-native`, `expo-router`,
  `expo-sqlite`, `@homeai/sdk`, `@homeai/ui`, `@expo/vector-icons` and
  relative files. No network, no DOM.
- Keep this file up to date when you change the schema or screens.
