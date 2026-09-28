# Runtime check

Fixture for `packages/homeai-sdk` tests. A list of `items` (`name`, `done`):
the index adds rows with `runAsync`, lists them with `useQuery`, and marks
them all done with the `markAllDone` action; `item/[id]` reads one row with
`getFirstAsync` and toggles it.
