# Calculator and units

Three tools that keep no data. **Calculator**: a keypad with + − × ÷, %,
parentheses and ⌫; the answer shows as you type, = keeps it to build on,
and "Earlier" lists this session's answers (tap one to use it).
**Units**: pick a group (length, weight, kitchen volumes, temperature,
speed, area), an amount and two units; the rest of the group is listed
below. **Tip**: the bill, a tip percentage and how many people, with each
share rounded up to a whole amount if you like.

## Files

```text
app.json                 manifest
schema.sql               no tables (a comment saying so)
lib/calc.ts              `evaluate` (a small parser) and `formatNumber`
lib/units.ts             the unit groups and `convert`
app/_layout.tsx          Stack: the tabs
app/(tabs)/_layout.tsx   Tabs: Calculator, Units, Tip
app/(tabs)/index.tsx     the calculator
app/(tabs)/units.tsx     conversions
app/(tabs)/tip.tsx       tip and split
```

## Data

None. Everything is React state, gone when the app closes.

## How the arithmetic works

`evaluate(text)` parses with the usual precedence: parentheses, then `^`
(right to left) and a leading minus, then × ÷, then + −. `%` divides the
number before it by 100, so `200 × 10%` is 20 and `200 + 10%` is 200.1
(not a phone calculator's 220). Commas are ignored, so a formatted answer
can be typed back in. It returns `{ value }` or `{ error }` (dividing by 0,
a missing `)`, two dots in a number); there's no `eval`.

`formatNumber` shows 12 significant digits with separators, and
e-notation past 10^15 or under 10^-9, so 0.1 + 0.2 shows 0.3.

## Units

Each unit in `lib/units.ts` has a `factor` to its group's base unit
(metres, kilograms, litres, metres per second, square metres).
Temperature has `toBase` / `fromBase` functions instead, through Celsius.
Kitchen volumes are US measures (a cup is 236.6 ml).

## Actions, exports and reads

None.

## Extending it safely

- **Remembering something** (the calculator's history, favorite
  conversions): add a table to `schema.sql` with a comment line above it
  and a `-- comment` on each column, then read it with `useQuery` and
  write it with `useDatabase().runAsync`. Adding a table is applied on the
  next build.
- **A new unit**: an entry in its group with the factor to the base unit.
  **A new group**: an entry in `GROUPS`; it gets a chip on its own.
- **New functions** (√, sin): a case in `primary()` in `lib/calc.ts` and a
  key in `KEYS`.
- **New screen**: a new tab in `app/(tabs)/` with a `Tabs.Screen`, or a
  file under `app/` with a `Stack.Screen` in `app/_layout.tsx`. Helpers
  live outside `app/`.
- Imports are limited to `react`, `react-native`, `expo-router`,
  `expo-sqlite`, `@homeai/sdk`, `@homeai/ui`, `@expo/vector-icons` and
  relative files. No network, no DOM.
- Keep this file up to date when you change the screens.
