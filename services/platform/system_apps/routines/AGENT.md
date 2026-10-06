# Routines

The host Routines app (native screen at `/routines`, opened from its Home
tile). This package ships in the platform image and is read-only.

## What it is

A list of the current user's routines. A routine's page shows its schedule
and prompt, Run now, Edit, Delete, and its last 5 runs (More… lists all).
Each run is a regular chat thread; tapping one opens it in Chat.

## For the agent

Routines has no SQLite database and no `app_action`s. Use the routine tools
(`list_routines`, `create_routine`, `update_routine`, `delete_routine`) to
manage routines for the user; they ask for approval the same way the app's
buttons do. Do not try to edit this package.
