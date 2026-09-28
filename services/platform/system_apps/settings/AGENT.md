# Settings

The host Settings tab (native screen at `/settings`). This package ships
in the platform image and is read-only.

## What it is

Account, sessions, TOTP, spaces and members, and (admins) users and
invites, plus chat preferences (HITL, thinking, default edit mode). A
fourth tab alongside Home, Chat, and Files — not a sibling modal.

## For the agent

Settings has no SQLite database and no `app_action`s. You must not perform
admin or auth-management actions (invites, disable users, reset passwords,
create sessions) even when the user is an admin: those stay in the UI with
step-up. Explain how to use Settings instead.

Spaces the user belongs to are listed by `ls /spaces`. File work uses the
file tools or the Files system app.
