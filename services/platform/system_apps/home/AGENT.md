# Home

The host launcher (native screen at `/apps`; `/` redirects here). Not a
sandboxed app and not editable: this package ships in the platform image.

## What it shows

- System tiles for Chat, Files, and Settings (those native host screens).
- Installed apps grouped by space, Personal first, with a space switcher
  (All + each live space) and update badges for pinned catalog installs.
- Catalog at `/apps/catalog`. Runner, info, install, and update stay under
  `/apps/...`.

## For the agent

Home has no database and no actions. To work with an installed app, call
`list_apps` and then `app_sql` / `app_action` on that instance. To move
files between spaces, use the Files system app (`app_action` with
instance `files`), not Home.
