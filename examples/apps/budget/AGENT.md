# Budget

Spending and income by month and category. **Month** shows what was spent,
what came in and what's left, a pie chart of where it went, and each
budgeted category's progress. **Transactions** lists the month, with how
many are still uncategorized. **Trends** charts the last six months and
the average a month per category. **Categories** manages categories, their
monthly budgets and their rules.

## Files

```text
app.json                        manifest; homeai.index makes descriptions searchable
schema.sql                      the database schema (plain SQLite DDL)
actions/addStarterCategories.sql the usual categories (Groceries, Rent… Salary)
actions/applyRules.sql          categorize what the rules match
actions/categorize.sql          set a transaction's category, optionally as a rule
lib/budget.ts                   types, months (YYYY-MM), CURRENCY
components/MonthPicker.tsx      ‹ October 2026 ›
components/TransactionForm.tsx  the fields of a transaction, used by new and edit
app/_layout.tsx                 Stack: the tabs, a transaction, a category, the Add modal
app/(tabs)/_layout.tsx          Tabs: Month, Transactions (badge: uncategorized), Trends, Categories
app/(tabs)/index.tsx            the month: totals, pie chart, budgets
app/(tabs)/transactions.tsx     the month's transactions, "Apply my rules"
app/(tabs)/trends.tsx           six months of spending and income, averages
app/(tabs)/categories.tsx       categories, and a form to add one
app/transaction/[id].tsx        edit or delete a transaction
app/category/[id].tsx           a category: name, budget, rules, this month
app/new.tsx                     the Add a transaction modal
```

## Data

`categories`

| column           | type    | meaning                                          |
|------------------|---------|--------------------------------------------------|
| `id`             | INTEGER | primary key                                      |
| `name`           | TEXT    | e.g. `Groceries`                                 |
| `monthly_budget` | REAL    | how much to spend a month; NULL for no budget    |
| `is_income`      | INTEGER | 1 for income (salary), 0 for spending            |

`transactions`: one row per payment, e.g. a line of a bank statement.

| column        | type    | meaning                                                  |
|---------------|---------|----------------------------------------------------------|
| `id`          | INTEGER | primary key                                              |
| `day`         | TEXT    | `YYYY-MM-DD` (a CHECK refuses anything else)             |
| `description` | TEXT    | the payee, or the bank's text                            |
| `amount`      | REAL    | **negative for money spent**, positive for money in      |
| `category_id` | INTEGER | `categories.id`; NULL until categorized                  |
| `note`        | TEXT    | free text; `''` if none                                  |

`rules`: `pattern` (text the description contains, any case) and
`category_id`. Deleting a category deletes its rules and leaves its
transactions uncategorized.

Amounts are in `CURRENCY` (`lib/budget.ts`, `USD`); change it there.
Months are compared as text: `day BETWEEN 'YYYY-MM-01' AND 'YYYY-MM-31'`.

## Importing a bank statement

There's no import screen in the app: the platform does it. Save the bank's
CSV in Files, open this app's info screen (ⓘ, "App info", in the header
above the running app), and
under **Data → Import a CSV file** pick `transactions`, give the file's
path and press **Check file**. Map each header to a column: the date to
`day`, the payee to `description`, the amount to `amount`; skip the rest
(balance, reference). The agent can do the same with `app_import`.

- Dates must be `YYYY-MM-DD` and amounts plain numbers (`-42.10`, no
  currency sign or thousands separator); a row that isn't fails the whole
  import with its line number, and nothing is written.
- Spending must be negative. A statement with separate "Debit"/"Credit"
  columns, or positive spending, needs fixing first (or an
  `app_update` afterwards: `UPDATE transactions SET amount = -amount …`).
- Imported rows are uncategorized; **Apply my rules** (or `applyRules`)
  sorts the ones a rule matches.

## Actions

Call them with `runAction(name, params)`; params are `:named`.

- `addStarterCategories`: `{}`. Groceries, Eating out, Rent, Bills,
  Transport, Fun and Salary (income), skipping names that exist.
- `applyRules`: `{}`. Categorizes uncategorized transactions a rule
  matches; the longest matching pattern wins. `changes` is how many.
- `categorize`: `{ id, category_id, pattern }`. Sets the transaction's
  category. With a non-empty `pattern` it also saves the rule (moving an
  existing rule with the same pattern) and applies the rules. The edit
  screen's "Always use this category" passes the pattern the person
  typed, prefilled by `suggestPattern` in `lib/budget.ts` (the
  description without trailing store numbers and reference codes).

## Exports and reads

None. A natural next step is exporting `transactions` so another app
(a net-worth tracker, say) can read them.

## How the code talks to the database

- `useQuery(sql, params)` for anything on screen; it re-runs when anyone
  changes the database (an import too). Don't copy results into state.
- `useDatabase()` gives `db.runAsync` etc.: one statement per call, `?`
  placeholders. Never build SQL by string concatenation.
- `useSpace()?.role` is `'viewer'` for people who can only look; the
  screens hide the add, edit and rule controls from them.

## Extending it safely

- **New column**: add it to `schema.sql` with `NOT NULL DEFAULT <constant>`
  (or nullable) and a trailing `-- comment`. Adding is applied on the next
  build; renaming or removing is destructive and needs the owner's
  approval.
- **New table** (accounts, say): a comment line above the `CREATE TABLE`,
  then a nullable `account_id INTEGER REFERENCES accounts(id) ON DELETE SET
  NULL` on `transactions`.
- **New screen**: a file under `app/` with a `Stack.Screen` in
  `app/_layout.tsx`; a new tab goes in `app/(tabs)/` with a `Tabs.Screen`.
  Helpers and components live outside `app/`.
- Charts (`BarChart`, `LineChart`, `PieChart`) and `Money` / `formatMoney`
  come from `@homeai/ui`; take colors from `useTheme()`.
- Imports are limited to `react`, `react-native`, `expo-router`,
  `expo-sqlite`, `@homeai/sdk`, `@homeai/ui`, `@expo/vector-icons` and
  relative files. No network, no DOM.
- Keep this file up to date when you change the schema, actions or
  screens.
