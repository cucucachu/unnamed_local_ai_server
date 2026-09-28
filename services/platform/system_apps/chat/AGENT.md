# Chat

The host Chat tab (native screen at `/chat`). You are the agent in those
threads. This package ships in the platform image and is read-only.

## What it is

A list of the current user's threads and the conversation screen for each.
New threads, delete, and the usual chat controls (stop, edit, HITL cards)
live here. Chat settings (HITL, thinking, default edit mode) are on the
Settings tab, not in this app.

## For the agent

Chat has no SQLite database and no `app_action`s. You already *are* the
chat: reply in this thread. File work uses the file tools; app data uses
`list_apps` / `app_sql` / `app_action`. Do not try to edit this package.
