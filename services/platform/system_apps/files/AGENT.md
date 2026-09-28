# Files

The host Files tab (native screen at `/files`). This package ships in the
platform image. You cannot edit its source. Its actions are privileged:
only this image-shipped app may declare them.

## Paths

Same virtual tree as the file tools:

- `/personal/...` — the current user's private files
- `/spaces/<slug>/...` — a shared space they belong to

The Files tab Move/Copy destination picker can send an entry into another
space. File tools cannot: they have no move or copy.

## What to use when

| Job | Tool |
|---|---|
| list, read, search | file tools `ls`, `read_file`, `glob`, `grep` |
| create or overwrite a file | `write_file` (creates missing folders) |
| edit or delete | `edit_file`, `delete` |
| rename, move, or move into another space | `app_action` instance `files`, action `moveToSpace` |
| copy, including into another space | `app_action` instance `files`, action `copyToSpace` |

Call `app_action` with instance `files` (the slug). Both actions need
`{src, dst}` virtual paths, matching the Files tab. A viewer of a shared
space can read it but not move or copy into or out of it.

There is no SQLite database for Files.
