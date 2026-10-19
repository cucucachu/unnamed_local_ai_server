# Recipes

The household's recipes and a weekly meal plan. **Recipes** lists them
with a search box (by meaning and by words, over names, ingredients and
steps); a recipe shows its ingredients and steps, and can be planned or
sent to the grocery list. **Meal plan** shows a week, Monday first, with
breakfast, lunch and dinner per day.

## Files

```text
app.json                     manifest; homeai.index, homeai.reads (the grocery list)
schema.sql                   the database schema (plain SQLite DDL)
actions/planMeal.sql         plan a meal, replacing what was planned
lib/recipes.ts               dates (local YYYY-MM-DD), weeks, ingredient lines
components/RecipeForm.tsx    the fields of a recipe, used by new and edit
components/GroceryButton.tsx "Add ingredients to the grocery list"
app/_layout.tsx              Stack: the tabs, a recipe, its edit screen, two modals
app/(tabs)/_layout.tsx       Tabs: Recipes (with "+"), Meal plan
app/(tabs)/index.tsx         search and the list of recipes
app/(tabs)/plan.tsx          the week's meals
app/recipe/[id]/index.tsx    one recipe: ingredients, grocery list, steps, plan, delete
app/recipe/[id]/edit.tsx     edit it
app/new.tsx                  the New recipe modal
app/plan/[day].tsx           the Plan a meal modal (?recipe=<id> preselects one)
```

## Data

`recipes`: one row per dish.

| column        | type    | meaning                                              |
|---------------|---------|------------------------------------------------------|
| `id`          | INTEGER | primary key                                          |
| `name`        | TEXT    | e.g. `Dal tadka`                                     |
| `cuisine`     | TEXT    | e.g. `Indian`; `''` if not given                     |
| `servings`    | INTEGER | how many it feeds, 1 or more                         |
| `minutes`     | INTEGER | total time; NULL if unknown                          |
| `ingredients` | TEXT    | one per line, e.g. `200 g red lentils`               |
| `steps`       | TEXT    | the method                                           |

`meals`: the plan, at most one row per day and meal.

| column      | type    | meaning                                                   |
|-------------|---------|-----------------------------------------------------------|
| `id`        | INTEGER | primary key                                               |
| `day`       | TEXT    | the local date, `YYYY-MM-DD`                              |
| `slot`      | TEXT    | `Breakfast`, `Lunch` or `Dinner`                          |
| `recipe_id` | INTEGER | the recipe; NULL for a note-only meal or a deleted recipe |
| `note`      | TEXT    | e.g. `leftovers`; `''` if none                            |

Deleting a recipe keeps its planned meals (they show "A deleted recipe").
Dates are computed in JS (`localDate`, `weekStart` in `lib/recipes.ts`),
not with SQLite's UTC `date('now')`.

## Actions

- `planMeal`: `{ day, slot, recipe_id, note }`. Replaces whatever was
  planned for that meal; `recipe_id` may be null (or a recipe that no
  longer exists) for a note-only meal.

Recipe edits are one `db.runAsync` each.

## Exports and reads

`homeai.reads` asks for the Grocery list's `items` export (v1). The
platform attaches every grocery list the user can see, read-only:

- `homeai_exports` has one row per grocery list (`_instance`, `_space`,
  `app`, `export`), even an empty one; with none installed it's empty.
- `grocery_list_items` is their items, merged, with `_space` and
  `_instance` (it doesn't exist until a grocery list is installed).
- Writing goes through the grocery list's own exported action:
  `runExportAction(instance, 'items', 'addItem', { name })`. Adding a name
  that's already there unchecks it instead of duplicating it.

`GroceryButton` prefers the grocery list in the same space (`_space` is
`/spaces/<slug>`, or `/personal`), and shows a picker when there are
several. Ingredient lines lose a leading `- `, `* `, `•` or `1.`.

## How the code talks to the database

- `useQuery(sql, params)` for anything on screen; it re-runs when anyone
  changes the database. Don't copy results into state.
- `useDatabase()` gives `db.runAsync` etc.: one statement per call, `?`
  placeholders. Never build SQL by string concatenation.
- `useSpace()?.role` is `'viewer'` for people who can only look; the
  screens hide the add, plan, edit and grocery controls from them.

## Extending it safely

- **New column**: add it to `schema.sql` with `NOT NULL DEFAULT <constant>`
  (or nullable) and a trailing `-- comment`. Adding is applied on the next
  build; renaming or removing is destructive and needs the owner's
  approval. A text column worth searching can join `homeai.index`'s `text`.
- **New table**: a comment line above the `CREATE TABLE`; link rows with
  `REFERENCES recipes(id)` (`ON DELETE CASCADE` or `SET NULL`).
- **New screen**: a file under `app/` with a `Stack.Screen` in
  `app/_layout.tsx` (`presentation: 'modal'` for a sheet); a new tab goes
  in `app/(tabs)/` with a `Tabs.Screen` in its `_layout.tsx`. Helpers and
  components live outside `app/`.
- Build screens from `@homeai/ui` and take colors from `useTheme()`.
- Imports are limited to `react`, `react-native`, `expo-router`,
  `expo-sqlite`, `@homeai/sdk`, `@homeai/ui`, `@expo/vector-icons` and
  relative files. No network, no DOM.
- Keep this file up to date when you change the schema, actions or
  screens.
