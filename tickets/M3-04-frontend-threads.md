# M3-04 — Thread list + history hydration UI

**Milestone**: M3 · **Size**: M · **Depends on**: M3-02, M2-06 · **Blocks**: M3-06

## Context

Replace M2-06's fixed `"default"` thread with real thread management: list, create, switch,
delete, and hydrate history from REST when opening a thread.

## Spec

1. **`lib/threads.ts`**: typed client for the CONVENTIONS §5 Threads endpoints (uses
   `apiFetch`). Types `Thread`, `ThreadMessage` mirroring the DTOs.
2. **Navigation**: Chat tab becomes a stack — `app/(tabs)/chat/index.tsx` (thread list) and
   `app/(tabs)/chat/[threadId].tsx` (the M2-06 chat screen, now parameterized).
3. **Thread list screen**:
   - FlatList of threads (title + relative updated time, e.g. "2h ago" — implement a tiny
     helper, no date lib).
   - "New chat" button (top-right header): `POST /api/threads` then navigate to it.
   - Swipe-to-delete on native, long-press → confirm dialog on web (`Alert` +
     `window.confirm` fallback): `DELETE`, optimistic removal, restore + toast on failure.
   - Pull-to-refresh; auto-refresh on screen focus (`useFocusEffect`).
   - Empty state: "No conversations yet" + New chat button.
4. **History hydration** in `useChat(threadId)`: on mount, `GET /api/threads/{id}/messages`
   and map to `ChatItem`s before opening the socket — `user`/`assistant` rows map directly;
   `tool` rows map to tool items with `status:"success"` and the stored preview; `assistant`
   rows with `tool_calls` but empty content render nothing (the tool item covers them). Show a
   spinner until hydration completes; hydration failure → error banner with retry, socket not
   opened.
5. **Tests** (jest): history→items mapping table-driven (≥ 4 cases incl. tool rows); relative
   time helper; thread list rendering with mocked fetch (list/create/delete flows).

## Out of scope

Thread rename UI, search, archiving (not v1). Files screen (M3-05).

## Acceptance criteria (Tier A)

- [ ] `npm test` + `npx tsc --noEmit` green; `npx expo export --platform web` builds.
- [ ] Extend `scripts/e2e/chat_browser_smoke.sh` (playwright): create a new chat from the UI,
      send a message, go back to the list (title now = message prefix), reopen the thread —
      prior messages render (hydration), send a follow-up. Exits 0 on host.

## Tier B (append to docs/HOST-CHECKS.md under M3)

- [ ] Phone browser + Expo Go: create/switch/delete threads; history loads on reopen.
