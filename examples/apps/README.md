# Example apps

Hand-written apps in the package format of `docs/PLATFORM.md` §7. They are
the templates the agent copies when it writes apps (M13), so they stick to
plain standards: SQLite DDL in `schema.sql`, `:named`-parameter SQL in
`actions/`, expo-router files under `app/`, React Native components, and the
expo-sqlite-shaped API from `@homeai/sdk`.

| App | What it shows |
|---|---|
| [`grocery-list/`](grocery-list/) | Default `create_app` template. One `items` table with quantity/note, two actions (`addItem`, `clearChecked`), a list screen with live data (`useQuery`), a detail screen (`app/item/[id].tsx`), and read-only UI for viewers. Start with its `AGENT.md`. |
| [`list/`](list/) | Generic checklist: `items` with `done`, `addItem` / `clearDone`, list + detail. |
| [`notes/`](notes/) | Notes: `title` / `body` / `pinned`, `addNote`, list + `app/note/[id].tsx`. |
| [`tracker/`](tracker/) | Habit tracker: `habits` + `checkins` (one row per habit per day), `addHabit` / `checkIn`, list + `app/habit/[id].tsx`. |

## Install one into a space

Apps live as files in a space's `Apps` folder; the platform registers the
folder, installs an instance in the space (with its own SQLite database),
and builds it. `scripts/e2e/app_fixture.mjs` does all of that as your own
user, through Caddy, with only Node (no npm install):

```bash
# Your Personal space
node scripts/e2e/app_fixture.mjs install --app examples/apps/grocery-list --user <you>

# A shared space you own or edit (its slug, as in Settings -> Spaces)
node scripts/e2e/app_fixture.mjs install --app examples/apps/grocery-list --user <you> --space family
```

It prompts for your password (or reads `$HOMEAI_PASSWORD`), uploads the
folder to `<space>/Apps/grocery-list`, registers it, installs it and builds
it, then prints the instance id. Open the **Apps** tab: the app is listed
under that space. Everyone in a shared space sees the same list, live;
viewers can look but not change it. `--base <url>` (default
`$HOMEAI_BASE_URL` or `http://homeai.local`) points it at another address.

Re-running `install` uploads the folder again and rebuilds, so it is also
how you try an edit. A schema change that would lose data (a dropped or
renamed column, say) isn't applied by the build; it waits for approval
(`docs/PLATFORM.md` §7 "Data").

To remove it:

```bash
node scripts/e2e/app_fixture.mjs uninstall --app examples/apps/grocery-list --user <you> [--space family]
```

Uninstalling keeps the instance's data in the space's `apps/.trash` and the
source files in `Apps/grocery-list`; delete that folder in the Files tab if
you don't want them.

The same steps with the recovery CLI, after putting the folder in the
space's `Apps` folder (e.g. by uploading it in the Files tab), are
`register-app` and `install-app` (README "Recovery CLI"); the CLI has no
build command, so build it with the script above or
`POST /api/platform/apps/<id>/build`.

## Checks

- `cd services/app-builder && npm test` builds `grocery-list`, `list`,
  `notes` and `tracker` with the builder (zero diagnostics, every route
  renders).
- `scripts/e2e/grocery_app_smoke.sh` installs it in a personal and a
  shared space on the live stack and drives it in a browser (add, check,
  detail screen, clear checked, reload, live updates between members, a
  viewer can't write).
