# Stack

A 3D stacking game drawn with three.js. Tap to start; a block slides in
over the tower, and a tap drops it. Whatever overhangs the block below is
cut off and falls, and the next block is the size of what stayed. A drop
within a hair of the block below is "perfect": it snaps on, keeps its full
size and flashes. Miss the tower entirely and the game is over; the score is
how many blocks you stacked. Blocks come in along x and z by turns, a
little faster as the tower grows, the camera rises with the tower, and the
colors drift up it. Every finished game is saved for whoever played it, so
each member has their own best and recent games, and the Scores tab ranks
everyone's best. Viewers see the scores but can't play.

## Files

```text
app.json                   manifest
schema.sql                 the database schema (plain SQLite DDL)
lib/stack.ts               the rules without graphics: sizes, speed, where the sliding block is, what a drop does
components/StackGame.tsx   the three.js scene and frame loop (createEngine), and the game's on-screen text
app/_layout.tsx            Stack holding the tabs
app/(tabs)/_layout.tsx     Tabs: Play, Scores
app/(tabs)/index.tsx       Play: the game, full screen; saves each finished game
app/(tabs)/scores.tsx      your best and games, the leaderboard, your recent games
```

## Data

`games`: one row per finished game.

| column     | type    | meaning                                   |
|------------|---------|-------------------------------------------|
| `id`       | INTEGER | primary key                               |
| `score`    | INTEGER | blocks stacked before the miss            |
| `perfects` | INTEGER | how many of those were perfect drops      |

Who played is the platform's `_created_by` (a member's user id, see
`useMembers()`), and when is `_created_at`, so there is no player column:
your best is `MAX(score) ... WHERE _created_by = <your id>`, and the
leaderboard is `GROUP BY _created_by`. A game is only saved when it ends.

## The 3D scene

`GLView` (from `expo-gl`) hands `createEngine` a WebGL context, and three
renders into it with `new THREE.WebGLRenderer({ canvas: gl.canvas, context: gl })`.
`createEngine` owns everything 3D: an orthographic camera looking down at
the tower from a corner, an ambient and a directional light, one shared
unit `BoxGeometry` scaled per block, and a `MeshLambertMaterial` per block
colored by `hueAt(level)`. Its frame loop (`requestAnimationFrame`, cancelled
in `dispose` on unmount) resizes the renderer and camera when the view's
size changes, moves the sliding block, drops the cut-off pieces, fades the
perfect flash, eases the camera up to the tower's top, and renders.

Input is the `Pressable` around the `GLView` (`onPressIn`, so a drop happens
the moment the screen is touched, not on release); the text on top has
`pointerEvents="none"`. The
sliding block's position is a function of the time since it appeared
(`slide(score, seconds)` in `lib/stack.ts`), worked out again at the moment
of the tap, so a drop doesn't depend on the frame rate. The engine tells
the screen about starts, drops and the end through `onStart`, `onPlace`
and `onOver`; React state only holds the score and which message to show.

The background follows the theme (`renderer.setClearColor` with
`useTheme().colors.background`), so the game works in light and dark.

## Extending it safely

- **Tuning**: `SIZE`, `HEIGHT`, `RANGE`, `PERFECT` and `crossingSeconds` in
  `lib/stack.ts`. Keep `slide` a function of time only.
- **Growing back on perfect streaks**, combos, or sounds: count streaks in
  `createEngine`'s `tap`, and add columns to `games` if they should be kept.
- **New column**: add it to `schema.sql` with `NOT NULL DEFAULT <constant>`
  (or nullable) and a trailing `-- comment`. Adding is applied on the next
  build; renaming or removing, or changing a CHECK, is destructive and
  needs the owner's approval.
- three's add-ons (`three/examples/jsm`: OrbitControls, loaders) are not
  available; everything here is core three.
- Build screens from `@homeai/ui` and take colors from `useTheme()`.
- Imports are limited to `react`, `react-native`, `expo-router`,
  `expo-sqlite`, `expo-gl`, `three`, `@homeai/sdk`, `@homeai/ui`,
  `@expo/vector-icons` and relative files. No network, no DOM.
- Keep this file up to date when you change the schema or screens.
